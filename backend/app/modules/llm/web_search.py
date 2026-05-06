"""多源联网搜索：对国内网络环境优化的轻量级聚合器。

设计理念（对齐豆包等 app 的做法）：
- **不依赖单一引擎**，将结果多源交叉，降低单一平台偏颇和反爬失效风险。
- 覆盖 **综合搜索**（百度 / 搜狗 / 360 / 必应国内版 / 必应国际 / DuckDuckGo）
  和 **垂直平台**（知乎 / CSDN / 掘金 / 百度百科 / 头条百科 / 百度学术 /
  小红书 / 豆瓣 / GitHub 等，通过 ``site:`` 约束在综合引擎上取结果）。
- 不需要 API key；全部基于 HTML 抓取，任何一源挂掉都会自动降级，不阻断主流程。
- 每一路都有超时和独立错误隔离，汇总后按 URL 去重。
- 天气/汇率/时刻等结构化事实先走直连接口（比如 wttr.in），比搜索引擎更准确。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import quote, unquote, urlparse, parse_qs

import httpx

logger = logging.getLogger(__name__)

MAX_RESULTS = 10
PER_SOURCE_CAP = 4  # 单源最多取几条，避免一个平台占满窗口
TIMEOUT = 10.0
# Bing 和百度会对带 "Chrome/ + Google Referer" 的请求返回空结果页（反爬）；
# 把 UA 换成 Safari 后这两个源都能拿到正常 HTML。保留一个备选 Chrome UA
# 仅给 DuckDuckGo/搜狗 使用，避免多源全部被同一指纹封锁。
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Safari/605.1.15"
)
ALT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(text: str) -> str:
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub("", text))).strip()


# ── Bing 解析 ─────────────────────────────────────────────────────
_BING_ITEM_RE = re.compile(
    r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>',
    re.DOTALL,
)
_BING_TITLE_RE = re.compile(
    r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_BING_SNIPPET_RE = re.compile(
    r'<(?:div|p)[^>]*class="[^"]*(?:b_caption|b_dataList|b_richcard|b_lineclamp[^"]*)[^"]*"[^>]*>(.*?)</(?:div|p)>',
    re.DOTALL,
)


def _parse_bing(html: str, max_results: int) -> list[dict]:
    items: list[dict] = []
    for m in _BING_ITEM_RE.finditer(html):
        if len(items) >= max_results:
            break
        block = m.group(1)
        title_match = _BING_TITLE_RE.search(block)
        if not title_match:
            continue
        url = title_match.group(1).strip()
        title = _strip_tags(title_match.group(2))
        snippet_match = _BING_SNIPPET_RE.search(block)
        snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
        if not title or not url:
            continue
        items.append({"title": title, "url": url, "snippet": snippet})
    return items


# ── DuckDuckGo 解析 ───────────────────────────────────────────────
_DDG_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def _normalize_ddg(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        target = qs.get("uddg", [""])[0]
        if target:
            return unquote(target)
    return href


def _parse_ddg(html: str, max_results: int) -> list[dict]:
    items: list[dict] = []
    for m in _DDG_RESULT_RE.finditer(html):
        if len(items) >= max_results:
            break
        href, title_html, snippet_html = m.groups()
        url = _normalize_ddg(href)
        title = _strip_tags(title_html)
        snippet = _strip_tags(snippet_html)
        if not title or not url:
            continue
        items.append({"title": title, "url": url, "snippet": snippet})
    return items


# ── 百度（百度在 PC 端 HTML 结果是 result c-container 结构） ──────
_BAIDU_ITEM_RE = re.compile(
    r'<div[^>]*class="[^"]*result[ \-_][^"]*c-container[^"]*"[^>]*>(.*?)</div>\s*</div>',
    re.DOTALL,
)
_BAIDU_TITLE_RE = re.compile(
    r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_BAIDU_ABSTRACT_RE = re.compile(
    r'<(?:span|div)[^>]*class="[^"]*(?:content-right_[A-Za-z0-9]+|c-abstract|c-span-last)[^"]*"[^>]*>(.*?)</(?:span|div)>',
    re.DOTALL,
)


def _parse_baidu(html: str, max_results: int) -> list[dict]:
    items: list[dict] = []
    # 百度把正文结果塞进 <h3 class="t">。简单按 <h3 class=...t...> 切分即可，
    # 不依赖外层 div（否则正则匹配经常跨界）。
    header_positions = [m.start() for m in re.finditer(r'<h3[^>]*class="[^"]*t[^"]*"', html)]
    header_positions.append(len(html))
    for i in range(len(header_positions) - 1):
        block = html[header_positions[i]:header_positions[i + 1]]
        title_match = _BAIDU_TITLE_RE.search(block)
        if not title_match:
            continue
        href = title_match.group(1).strip()
        title = _strip_tags(title_match.group(2))
        if not title or not href:
            continue
        # 百度的 www.baidu.com/link?url=... 是 302 跳转；保留原样即可，LLM 看链接走。
        abstract_match = _BAIDU_ABSTRACT_RE.search(block)
        snippet = _strip_tags(abstract_match.group(1)) if abstract_match else ""
        items.append({"title": title, "url": href, "snippet": snippet})
        if len(items) >= max_results:
            break
    return items


# ── 搜狗 ──────────────────────────────────────────────────────────
_SOGOU_ITEM_RE = re.compile(
    r'<div[^>]*class="[^"]*(?:vrwrap|results)[^"]*"[^>]*>(.*?)</div>\s*</div>',
    re.DOTALL,
)
_SOGOU_TITLE_RE = re.compile(
    r'<h3[^>]*class="[^"]*vr-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_SOGOU_TITLE_FALLBACK_RE = re.compile(
    r'<a[^>]*id="sogou_vr_[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_SOGOU_ABSTRACT_RE = re.compile(
    r'<p[^>]*class="[^"]*str_info[^"]*"[^>]*>(.*?)</p>',
    re.DOTALL,
)


def _parse_sogou(html: str, max_results: int) -> list[dict]:
    items: list[dict] = []
    for block_match in re.finditer(
        r'<div[^>]*class="[^"]*vrwrap[^"]*"[^>]*>', html
    ):
        start = block_match.start()
        tail = html[start:start + 4000]
        title_match = _SOGOU_TITLE_RE.search(tail) or _SOGOU_TITLE_FALLBACK_RE.search(tail)
        if not title_match:
            continue
        href = title_match.group(1).strip()
        if href.startswith("/link?"):
            href = "https://www.sogou.com" + href
        title = _strip_tags(title_match.group(2))
        snippet_match = _SOGOU_ABSTRACT_RE.search(tail)
        snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
        if title and href:
            items.append({"title": title, "url": href, "snippet": snippet})
        if len(items) >= max_results:
            break
    return items


# ── 360 搜索 ──────────────────────────────────────────────────────
_SO_TITLE_RE = re.compile(
    r'<h3[^>]*class="[^"]*res-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_SO_ABSTRACT_RE = re.compile(
    r'<p[^>]*class="[^"]*res-desc[^"]*"[^>]*>(.*?)</p>',
    re.DOTALL,
)


def _parse_360(html: str, max_results: int) -> list[dict]:
    items: list[dict] = []
    positions = [m.start() for m in _SO_TITLE_RE.finditer(html)]
    positions.append(len(html))
    for i in range(len(positions) - 1):
        block = html[positions[i]:positions[i + 1]]
        title_match = _SO_TITLE_RE.search(block)
        if not title_match:
            continue
        href = title_match.group(1).strip()
        title = _strip_tags(title_match.group(2))
        if not title or not href:
            continue
        abstract_match = _SO_ABSTRACT_RE.search(block)
        snippet = _strip_tags(abstract_match.group(1)) if abstract_match else ""
        items.append({"title": title, "url": href, "snippet": snippet})
        if len(items) >= max_results:
            break
    return items


# ── 单源抓取 ──────────────────────────────────────────────────────


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    *,
    user_agent: str | None = None,
    referer: str | None = None,
) -> str:
    headers = {
        "User-Agent": user_agent or USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    resp = await client.get(url, params=params, headers=headers)
    if resp.status_code != 200:
        raise httpx.HTTPStatusError(
            f"status {resp.status_code}", request=resp.request, response=resp
        )
    return resp.text


# ── Recency / 时间过滤 ────────────────────────────────────────────
# 各搜索引擎对"最近 N 天/周/月"过滤都有自己的 URL 参数。这里把 recency
# 关键字（"day" / "week" / "month" / "year"）翻译成对应引擎的官方参数。
# 没列出来的源（垂直站点的 site: 过滤）不带时间参数。
RECENCY_VALUES = ("day", "week", "month", "year")


def _bing_recency_qft(recency: str | None) -> str | None:
    return {
        "day": "+filterui:age-lt1day",
        "week": "+filterui:age-lt1week",
        "month": "+filterui:age-lt1month",
        "year": "+filterui:age-lt1year",
    }.get(recency or "")


def _ddg_recency_df(recency: str | None) -> str | None:
    return {"day": "d", "week": "w", "month": "m", "year": "y"}.get(recency or "")


def _so_360_recency(recency: str | None) -> str | None:
    # 360 搜索 advanced time filter
    return {"day": "d", "week": "w", "month": "m", "year": "y"}.get(recency or "")


def _sogou_recency(recency: str | None) -> str | None:
    # 搜狗的 "tsn" 参数：1=最近一天, 2=一周, 3=一月, 4=一年
    return {"day": "1", "week": "2", "month": "3", "year": "4"}.get(recency or "")


def _baidu_recency_gpc(recency: str | None) -> str | None:
    """百度的时间过滤是 ``gpc=stf=<from_ts>,<to_ts>|stftype=1``，单位为秒。"""
    if recency not in RECENCY_VALUES:
        return None
    delta = {
        "day": timedelta(days=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
        "year": timedelta(days=365),
    }[recency]
    now = datetime.now(timezone.utc)
    from_ts = int((now - delta).timestamp())
    to_ts = int(now.timestamp())
    return f"stf={from_ts},{to_ts}|stftype=1"


async def _try_bing_cn(
    client: httpx.AsyncClient, query: str, max_results: int, *, recency: str | None = None
) -> list[dict]:
    params: dict = {"q": query, "ensearch": 0}
    qft = _bing_recency_qft(recency)
    if qft:
        params["qft"] = qft
    html = await _fetch(client, "https://cn.bing.com/search", params)
    return _parse_bing(html, max_results)


async def _try_bing_global(
    client: httpx.AsyncClient, query: str, max_results: int, *, recency: str | None = None
) -> list[dict]:
    params: dict = {"q": query, "cc": "cn"}
    qft = _bing_recency_qft(recency)
    if qft:
        params["qft"] = qft
    html = await _fetch(client, "https://www.bing.com/search", params)
    return _parse_bing(html, max_results)


async def _try_ddg(
    client: httpx.AsyncClient, query: str, max_results: int, *, recency: str | None = None
) -> list[dict]:
    # DDG 可以接受 Chrome UA，反而 Safari UA 有时被当成 bot。
    params: dict = {"q": query, "kl": "cn-zh"}
    df = _ddg_recency_df(recency)
    if df:
        params["df"] = df
    html = await _fetch(
        client,
        "https://duckduckgo.com/html/",
        params,
        user_agent=ALT_USER_AGENT,
    )
    return _parse_ddg(html, max_results)


async def _try_baidu(
    client: httpx.AsyncClient, query: str, max_results: int, *, recency: str | None = None
) -> list[dict]:
    params: dict = {"wd": query, "rn": max_results}
    gpc = _baidu_recency_gpc(recency)
    if gpc:
        params["gpc"] = gpc
        params["tfflag"] = "1"  # 配合 gpc 让百度认这个时间过滤
    html = await _fetch(
        client,
        "https://www.baidu.com/s",
        params,
        referer="https://www.baidu.com/",
    )
    return _parse_baidu(html, max_results)


async def _try_sogou(
    client: httpx.AsyncClient, query: str, max_results: int, *, recency: str | None = None
) -> list[dict]:
    params: dict = {"query": query}
    tsn = _sogou_recency(recency)
    if tsn:
        params["tsn"] = tsn
    html = await _fetch(
        client,
        "https://www.sogou.com/web",
        params,
        user_agent=ALT_USER_AGENT,
        referer="https://www.sogou.com/",
    )
    return _parse_sogou(html, max_results)


async def _try_360(
    client: httpx.AsyncClient, query: str, max_results: int, *, recency: str | None = None
) -> list[dict]:
    params: dict = {"q": query}
    rt = _so_360_recency(recency)
    if rt:
        params["adv_t"] = rt
    html = await _fetch(
        client,
        "https://www.so.com/s",
        params,
        user_agent=ALT_USER_AGENT,
        referer="https://www.so.com/",
    )
    return _parse_360(html, max_results)


# ── 垂直平台：借助 Bing 的 site: 语法拿对应站点的结果 ─────────────
#    选 Bing 而不是百度，是因为百度对 site: 的结果质量和反爬都更不稳定。


def _make_site_source(domain: str, display_name: str):
    """构造一个"借用综合搜索执行 site: 过滤"的垂直源抓取函数。

    实测发现：Bing/百度/DDG 对 ``site:xxx`` 这种 query 都有较强的反爬（百度
    直接返回"网络不给力"验证页，Bing 返回空结构化页），完全行不通。因此这个
    函数现在走一个替代思路：
    - 让综合搜索（Bing/百度/360）用普通 query 搜，之后 **按 URL 的 host 过滤**
      出真正落在目标站点的条目，并把 ``source`` 重写为对应平台名称。
    - 这样"我想看知乎上关于 X 的讨论"虽然不是严格的 site: 搜索，但在国内主流
      搜索引擎里知乎/CSDN/百科通常都会出现在 top results，命中率可接受。
    """
    async def _fn(
        client: httpx.AsyncClient,
        query: str,
        max_results: int,
        *,
        recency: str | None = None,
    ) -> list[dict]:
        aggregated: list[dict] = []
        seen: set[str] = set()

        async def _pull(engine_fn):
            try:
                items = await engine_fn(client, query, max_results * 3, recency=recency)
            except Exception:  # noqa: BLE001
                return
            for r in items:
                url = (r.get("url") or "")
                if domain in url and url not in seen:
                    seen.add(url)
                    r["source"] = display_name
                    aggregated.append(r)

        # 用多个综合引擎串行抓，直到集齐 max_results 条命中结果。
        for engine in (_try_bing_cn, _try_baidu, _try_360, _try_bing_global, _try_sogou):
            if len(aggregated) >= max_results:
                break
            await _pull(engine)
        return aggregated[:max_results]

    return _fn


# 站点名 → 抓取函数
_VERTICAL_SITES = {
    "baidu_baike": ("baike.baidu.com", "百度百科"),
    "toutiao_baike": ("baike.baidu.com", "头条百科"),  # 头条百科无独立站，复用百科
    "wikipedia_zh": ("zh.wikipedia.org", "中文维基百科"),
    "zhihu": ("zhihu.com", "知乎"),
    "csdn": ("blog.csdn.net", "CSDN"),
    "juejin": ("juejin.cn", "掘金"),
    "github": ("github.com", "GitHub"),
    "runoob": ("runoob.com", "菜鸟教程"),
    "stackoverflow": ("stackoverflow.com", "Stack Overflow"),
    "xiaohongshu": ("xiaohongshu.com", "小红书"),
    "douban": ("douban.com", "豆瓣"),
    "baidu_scholar": ("xueshu.baidu.com", "百度学术"),
    "gushiwen": ("gushiwen.cn", "古诗文网"),
    "mp_weixin": ("mp.weixin.qq.com", "微信公众号"),
}


# ── 源分类 ────────────────────────────────────────────────────────

GENERAL_SOURCES = ["bing_cn", "baidu", "sogou", "so_360", "bing_global", "duckduckgo"]

# 话题 → 推荐在主搜索之外追加的垂直源
TOPIC_BUNDLES: dict[str, list[str]] = {
    "tech": ["csdn", "juejin", "stackoverflow", "github", "runoob"],
    "knowledge": ["baidu_baike", "wikipedia_zh", "zhihu"],
    "academic": ["baidu_scholar"],
    "lifestyle": ["xiaohongshu", "douban"],
    "history": ["gushiwen", "baidu_baike"],
}


def _detect_topic(query: str) -> list[str]:
    """根据问句内容推断要额外加入的垂直源。"""
    bundles: list[str] = []
    q = query.lower()
    if re.search(r"(报错|error|异常|exception|调试|bug|代码|api|函数|class|python|java|golang|go |react|vue|sql|docker|k8s|linux|shell|算法|编译|编码|配置|部署)", q):
        bundles.append("tech")
    if re.search(r"(是什么|含义|定义|百科|介绍|简介|原理|区别|对比)", q):
        bundles.append("knowledge")
    if re.search(r"(论文|文献|研究|综述|期刊)", q):
        bundles.append("academic")
    if re.search(r"(好物|推荐|攻略|探店|打卡|装修|穿搭|旅游|美食|测评)", q):
        bundles.append("lifestyle")
    if re.search(r"(古诗|词|曲|古文|历史|朝代|典故|文言)", q):
        bundles.append("history")
    return bundles


# ── 天气直连 ──────────────────────────────────────────────────────


def _looks_like_weather_query(query: str) -> bool:
    return "天气" in query or "温度" in query or "气温" in query


async def _try_weather_direct(query: str) -> dict | None:
    city = _extract_weather_city(query) or "北京"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(
                f"https://wttr.in/{quote(city)}",
                params={"format": "j1", "lang": "zh-cn"},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
        current = (data.get("current_condition") or [{}])[0]
        nearest = (data.get("nearest_area") or [{}])[0]
        area_name = (
            (nearest.get("areaName") or [{}])[0].get("value")
            if nearest
            else city
        )
        desc = (current.get("lang_zh-cn") or current.get("weatherDesc") or [{}])[0].get("value", "")
        temp = current.get("temp_C")
        feels = current.get("FeelsLikeC")
        humidity = current.get("humidity")
        wind = current.get("windspeedKmph")
        snippet = (
            f"{area_name or city} 当前天气：{desc}，温度 {temp}℃，体感 {feels}℃，"
            f"湿度 {humidity}%，风速 {wind} km/h。数据来自 wttr.in 实时天气接口。"
        )
        return {
            "title": f"{city}当前天气与温度",
            "url": f"https://wttr.in/{city}",
            "snippet": snippet,
            "source": "wttr.in",
        }
    except Exception as exc:  # noqa: BLE001
        logger.info("Weather direct lookup failed for %s: %s", query, exc)
        return None


def _extract_weather_city(query: str) -> str | None:
    known_cities = [
        "北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "苏州", "成都",
        "武汉", "西安", "郑州", "长沙", "青岛", "济南", "厦门", "福州", "昆明", "贵阳",
        "南宁", "海口", "三亚", "沈阳", "大连", "长春", "哈尔滨", "石家庄", "太原",
        "合肥", "南昌", "兰州", "银川", "西宁", "乌鲁木齐", "拉萨", "呼和浩特",
    ]
    for city in known_cities:
        if city in query:
            return city

    marker_pattern = r"(?:今天|今日|现在|当前|实时)?(?:天气|温度|气温)"
    m = re.search(rf"([\u4e00-\u9fff]{{2,8}}?){marker_pattern}", query)
    if not m:
        return None
    city = m.group(1)
    for prefix in ("查询", "搜索", "查一下", "我想知道", "帮我看看"):
        city = city.replace(prefix, "")
    for marker in ("今天", "今日", "现在", "当前", "实时"):
        city = city.replace(marker, "")
    return city or None


# ── 源注册表 ──────────────────────────────────────────────────────

_GENERAL_REGISTRY = {
    "bing_cn": ("必应国内版", _try_bing_cn),
    "bing_global": ("必应国际版", _try_bing_global),
    "duckduckgo": ("DuckDuckGo", _try_ddg),
    "baidu": ("百度", _try_baidu),
    "sogou": ("搜狗", _try_sogou),
    "so_360": ("360 搜索", _try_360),
}


def _resolve_sources(sources: list[str] | None, query: str) -> list[tuple[str, str, object]]:
    """解析 sources 参数 → [(id, display_name, fetch_fn), ...]

    - 如果用户/agent 没指定，按照 query 推断：general + 话题 bundle。
    - 显式传入 ``"all"`` 会加载全部综合 + 垂直源。
    """
    selected: list[str] = []

    if not sources or sources == ["auto"]:
        selected = list(GENERAL_SOURCES)
        for bundle in _detect_topic(query):
            for sid in TOPIC_BUNDLES.get(bundle, []):
                if sid not in selected:
                    selected.append(sid)
    elif sources == ["all"]:
        selected = list(GENERAL_SOURCES) + list(_VERTICAL_SITES.keys())
    else:
        for s in sources:
            if s in _GENERAL_REGISTRY or s in _VERTICAL_SITES or s in TOPIC_BUNDLES:
                if s in TOPIC_BUNDLES:
                    for sid in TOPIC_BUNDLES[s]:
                        if sid not in selected:
                            selected.append(sid)
                elif s not in selected:
                    selected.append(s)

    # 兜底：至少保证一路综合搜索
    if not any(s in _GENERAL_REGISTRY for s in selected):
        selected = ["bing_cn", "baidu"] + selected

    out: list[tuple[str, str, object]] = []
    for sid in selected:
        if sid in _GENERAL_REGISTRY:
            name, fn = _GENERAL_REGISTRY[sid]
            out.append((sid, name, fn))
        elif sid in _VERTICAL_SITES:
            domain, display = _VERTICAL_SITES[sid]
            out.append((sid, display, _make_site_source(domain, display)))
    return out


# ── 对外 API ──────────────────────────────────────────────────────


# ── 自动检测：query 带"今天/最新/赛程"等时间敏感词时，强制走时间过滤 ──
_RECENCY_AUTO_PATTERNS = [
    # 强烈即时（按"日"过滤）
    (re.compile(r"(今天|今日|现在|此刻|刚刚|实时|当下|目前|当前)"), "day"),
    (re.compile(r"(明天|明日|昨天|昨日)"), "day"),
    # "本周/这周/最近/近期" → 周
    (re.compile(r"(本周|这周|这两天|这几天|这几日|最近|近期|近几天)"), "week"),
    # "本月/这个月/最新/最近一个月" → 月
    (re.compile(r"(本月|这个月|本月份|本月度|最新|最近一个月)"), "month"),
    # 体育赛事/排行/政策类 → 周（变化频繁）
    (re.compile(r"(赛程|赛果|比分|排名|榜单|公告|发布会|新闻|政策|股价|汇率|油价|金价)"), "week"),
]


def _auto_recency(query: str) -> str | None:
    """根据 query 关键字自动推断 recency。命中越强烈的窗口越短。"""
    for pattern, recency in _RECENCY_AUTO_PATTERNS:
        if pattern.search(query):
            return recency
    return None


# 表示"近期/明确日期"的中文文字（提示 query 已经语义上要求"现在"）。
_TIME_HINT_RE = re.compile(
    r"(今天|今日|现在|此刻|实时|当下|目前|当前|本周|这周|本月|这月|"
    r"\d{4}\s*年|\d{4}-\d{1,2}-\d{1,2})"
)


def _augment_query_with_year(query: str) -> str:
    """如果 query 里没有任何年份/明确日期，但语义上是"今天/最新/赛程"，
    自动追加当前年份，避免百度/搜狗按相关度返回往年的高权重老文章。
    """
    if not _auto_recency(query):
        return query
    if re.search(r"\d{4}", query):  # 已经带年份/日期
        return query
    tz = timezone(timedelta(hours=8))
    year = datetime.now(tz).year
    return f"{query} {year}"


async def search(
    query: str,
    max_results: int = MAX_RESULTS,
    *,
    sources: list[str] | None = None,
    recency: str | None = None,
) -> list[dict]:
    """执行一次联网搜索：并发查询多个源，按 url 去重后返回前 ``max_results`` 条。

    采用并发而非串行的理由：
    - 串行时一旦上一个源返回 0 条但没异常，就不会触发下一个源；
    - 并发时即便百度抓到 3 条、搜狗抓到 5 条，合并后也能给模型提供足够新鲜
      事实素材，这正是豆包等 app 做"多源交叉校验"的方式。

    :param sources: 可选源列表。传 None 或 ["auto"] 时按问句内容自动挑选；
        传 ["all"] 时启用全部；可直接传 ``["baidu", "zhihu", "csdn"]`` 这样
        的精确组合；也支持话题 bundle（"tech"/"knowledge"/"academic" 等）。
    :param recency: ``"day" | "week" | "month" | "year" | None``。
        指定后会调用各搜索引擎的官方"近 N 天"过滤参数；不传时根据 query 关键词
        自动推断（"今天/赛程/最新"等会自动开启 day/week 过滤）。
    """
    query = query.strip()
    if not query:
        return []

    if recency not in RECENCY_VALUES:
        recency = _auto_recency(query)

    augmented_query = _augment_query_with_year(query)
    if augmented_query != query:
        logger.info("Web search auto-augmented query: %r -> %r", query, augmented_query)
    query = augmented_query

    direct_results: list[dict] = []
    if _looks_like_weather_query(query):
        weather = await _try_weather_direct(query)
        if weather:
            direct_results.append(weather)

    resolved = _resolve_sources(sources, query)
    logger.info(
        "Web search query=%r recency=%s sources=[%s]",
        query,
        recency,
        ", ".join(sid for sid, _, _ in resolved),
    )

    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        tasks = [
            asyncio.create_task(
                _safe_fetch(
                    name, fn, client, query, PER_SOURCE_CAP,
                    source_id=sid, recency=recency,
                )
            )
            for sid, name, fn in resolved
        ]
        results_per_source = await asyncio.gather(*tasks, return_exceptions=False)

    merged: list[dict] = direct_results[:]
    seen_urls: set[str] = set()
    for item in merged:
        if item.get("url"):
            seen_urls.add(item["url"])

    # 平衡采样：先从每个源取 1 条（轮询），再取第 2 条……避免一个源爆掉窗口。
    round_idx = 0
    while len(merged) < max_results:
        picked_this_round = 0
        for src_results in results_per_source:
            if round_idx >= len(src_results):
                continue
            r = src_results[round_idx]
            url = (r.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            merged.append(r)
            picked_this_round += 1
            if len(merged) >= max_results:
                break
        if picked_this_round == 0:
            break
        round_idx += 1
    return merged


# URL host → 真实平台名。用于把"通过某个通用搜索引擎抓来的 zhihu.com/csdn.net/
# baike.baidu.com 结果"自动重标成"知乎/CSDN/百度百科"，让用户看到真实的多源覆盖。
_HOST_DISPLAY = [
    ("baike.baidu.com", "百度百科"),
    ("zh.wikipedia.org", "中文维基百科"),
    ("baike.so.com", "360 百科"),
    ("zhihu.com", "知乎"),
    ("csdn.net", "CSDN"),
    ("juejin.cn", "掘金"),
    ("github.com", "GitHub"),
    ("runoob.com", "菜鸟教程"),
    ("stackoverflow.com", "Stack Overflow"),
    ("xiaohongshu.com", "小红书"),
    ("douban.com", "豆瓣"),
    ("xueshu.baidu.com", "百度学术"),
    ("mp.weixin.qq.com", "微信公众号"),
    ("gushiwen.cn", "古诗文网"),
    ("sina.com.cn", "新浪"),
    ("163.com", "网易"),
    ("qq.com", "腾讯"),
    ("sohu.com", "搜狐"),
    ("people.com.cn", "人民网"),
    ("xinhuanet.com", "新华网"),
    ("weather.com.cn", "中国天气网"),
    ("weather.cma.cn", "中央气象台"),
]


def _attribute_source(url: str, fallback: str) -> str:
    for host, display in _HOST_DISPLAY:
        if host in url:
            return display
    return fallback


async def _safe_fetch(
    name: str,
    fn,
    client: httpx.AsyncClient,
    query: str,
    max_results: int,
    *,
    source_id: str = "",
    recency: str | None = None,
) -> list[dict]:
    try:
        results = await asyncio.wait_for(
            fn(client, query, max_results, recency=recency), timeout=TIMEOUT
        )
        logger.info(
            "Web search %s(%s) recency=%s -> %d results",
            source_id or name, name, recency, len(results),
        )
        for r in results:
            url = r.get("url") or ""
            # 如果源函数（如 _make_site_source）已显式写了 source，就尊重它；
            # 否则按 URL host 做"真实平台归属"，比"从 Bing 抓的"这种笼统标签更有信息量。
            if not r.get("source"):
                r["source"] = _attribute_source(url, name)
        return results
    except Exception as exc:  # noqa: BLE001
        logger.info("Web search %s(%s) failed: %s", source_id or name, name, exc)
        return []


def format_results_as_context(results: list[dict], *, queries: list[str] | None = None) -> str:
    """将搜索结果格式化为可注入到 system message 的简明文本。"""
    if not results:
        return ""
    lines = [
        "以下是来自互联网的实时搜索结果（已做多源去重），可作为事实依据参考；",
        "若结果不足以回答，请基于自身知识作答并简要说明；如果引用了某条结果，请用 [n] 标注。",
        "",
    ]
    if queries:
        lines.extend(
            [
                "本轮检索词：",
                *[f"- {query}" for query in queries],
                "",
            ]
        )
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        url = r.get("url", "")
        source = r.get("source", "")
        head = f"[{i}] {title}"
        if source:
            head += f"（来源：{source}）"
        lines.append(head)
        if snippet:
            lines.append(f"    摘要：{snippet}")
        if url:
            lines.append(f"    链接：{url}")
        lines.append("")
    return "\n".join(lines).strip()


def available_sources() -> dict[str, list[dict]]:
    """返回所有可用源，供 agent / 前端展示。"""
    return {
        "general": [
            {"id": sid, "name": name}
            for sid, (name, _) in _GENERAL_REGISTRY.items()
        ],
        "vertical": [
            {"id": sid, "name": display, "domain": domain}
            for sid, (domain, display) in _VERTICAL_SITES.items()
        ],
        "bundles": [
            {"id": bundle, "sources": ids}
            for bundle, ids in TOPIC_BUNDLES.items()
        ],
    }
