"""Agent tool definitions and executor.

The chat agent uses the OpenAI tool-calling protocol so the configured LLM can
autonomously decide *when* and *what* to search. This mirrors how production
agents (Claude, GPT-4-turbo, GLM) work: the model reasons, calls tools if it
needs fresh facts, reads the results, and continues the response.

Exposed tools:
- web_search(query, top_k?, sources?): multi-source live search; aggregates
  results from Baidu/Sogou/360/Bing/DuckDuckGo + optional vertical sites
  (Zhihu/CSDN/Juejin/Baidu Baike/Wikipedia/Xiaohongshu/Douban/…).

To add a new tool, implement it here and register it in ``TOOL_REGISTRY``.
The registry is intentionally small — tools we expose must be fast, safe, and
have narrow contracts so the model can use them reliably.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from app.modules.llm.web_search import available_sources, search

logger = logging.getLogger(__name__)


# ── OpenAI-compatible tool schemas ────────────────────────────────
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "多源聚合的中文联网搜索（对齐豆包等 App 的做法，自动跨综合搜索 + "
                "垂直平台交叉验证）。\n"
                "何时使用：用户询问实时事实（天气、新闻、价格、日期、最近政策/赛事/"
                "事件、具体城市或人物的当前情况），或者需要权威百科/技术文档/学术文献/"
                "生活攻略时必须调用。\n"
                "何时不使用：纯知识性问题（基础数学、语法、已固化的历史常识）不要滥用。\n"
                "query 要求：必须是精确的中文搜索关键词，**已经展开相对时间并写入"
                "具体年份**（参考 system message 顶部"
                "【当前时间】部分），例如 `2026年5月3日 北京 实时天气`、"
                "`2026 CBA 季后赛 今日赛程`，而不是 `明天天气`、`今天 CBA 赛程`。\n"
                "recency 参数：当问题涉及"
                "今天/最新/近期/赛程/比分/股价/汇率/油价/新闻/发布会/政策"
                "等时效性事实时，**必须显式设置** "
                "（day=近一天、week=近一周、month=近一月、year=近一年），"
                "否则搜索引擎默认返回的是按相关度排序的旧文章。\n"
                "sources 参数：默认留空（'auto'）即可，系统会按问题自动挑选；"
                "如果你判断问题属于某个领域，可以显式指定源或 bundle 来提高命中率。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "精确中文搜索关键词，已展开相对时间并带具体年份",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果条数，默认 8，最大 12",
                        "default": 8,
                    },
                    "recency": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year"],
                        "description": (
                            "时间过滤窗口：day=近一天、week=近一周、month=近一月、"
                            "year=近一年。涉及'今天/最新/赛程/比分/股价/新闻'等时效"
                            "信息时必须设置（推荐 day 或 week），不设置时搜索引擎"
                            "会按相关度返回旧文章。"
                        ),
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "可选：指定检索源或话题 bundle。\n"
                            "综合搜索：bing_cn、baidu、sogou、so_360、bing_global、duckduckgo。\n"
                            "垂直平台：zhihu、baidu_baike、wikipedia_zh、csdn、juejin、github、"
                            "stackoverflow、runoob、xiaohongshu、douban、baidu_scholar、gushiwen、mp_weixin。\n"
                            "话题 bundle（展开为一组垂直源）：tech、knowledge、academic、lifestyle、history。\n"
                            "特殊值：'auto'（默认，自动选源）、'all'（启用全部源）。"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
]


async def _execute_web_search(args: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "empty query"}
    top_k = min(int(args.get("top_k") or 8), 12)
    sources_arg = args.get("sources")
    if isinstance(sources_arg, str):
        sources_arg = [sources_arg]
    elif isinstance(sources_arg, list):
        sources_arg = [str(s) for s in sources_arg if isinstance(s, (str, int))]
    else:
        sources_arg = None

    recency_arg = args.get("recency")
    if isinstance(recency_arg, str) and recency_arg.lower() in ("day", "week", "month", "year"):
        recency = recency_arg.lower()
    else:
        recency = None

    # 给模型一个明确的"本次检索时间"印记。哪怕模型忘了今天是几号、
    # 想了一个"2024 CBA 赛程"的 query 进来，看到结果里 search_time 是 2026
    # 也能反应过来检查年份是否一致。
    tz = timezone(timedelta(hours=8))
    search_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M (UTC+8)")

    try:
        results = await asyncio.wait_for(
            search(query, max_results=top_k, sources=sources_arg, recency=recency),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        return {
            "error": "search timeout",
            "query": query,
            "search_time": search_time,
            "recency": recency,
            "results": [],
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_search tool failed for %s", query)
        return {
            "error": str(exc),
            "query": query,
            "search_time": search_time,
            "recency": recency,
            "results": [],
        }
    sources_used = sorted({r.get("source") for r in results if r.get("source")})
    return {
        "query": query,
        "search_time": search_time,
        "recency": recency,
        "count": len(results),
        "sources_used": sources_used,
        "results": [
            {
                "title": r.get("title"),
                "snippet": r.get("snippet"),
                "url": r.get("url"),
                "source": r.get("source"),
            }
            for r in results
        ],
    }


ToolFn = Callable[[dict], Awaitable[dict]]

TOOL_REGISTRY: dict[str, ToolFn] = {
    "web_search": _execute_web_search,
}


# ── 动态工具注册 helpers（二期 Task 7.2 引入）─────────────────────────────
#
# 一期所有 tool 都是模块级常量，启动时一次性注册到 TOOL_REGISTRY。二期 UI
# 自动化执行需要"按 execution 临时注册一批 MCP browser_* 工具，执行结束就
# 卸载"，因此提供下面这组带命名空间的 register/unregister。
#
# 命名空间约定：``<scope>:<tool>``。例如：
#   - ``<execution_id>:browser_navigate``  二期 UI 测试动态加载
#   - ``<execution_id>:platform_get_secret``  Task 9.0 物料平台工具
#
# 这样 ``run_tool`` 只需查 TOOL_REGISTRY，对调用方完全透明；模型那边收到的
# OpenAI tool name 也会带这个前缀，避免和一期 ``web_search`` 等全局工具撞名。


def register_tool(name: str, fn: ToolFn) -> None:
    """注册一个工具到全局 TOOL_REGISTRY。

    若名字已存在则覆盖（带 logger.warning，方便排查"为什么我的工具被替换了"）。
    调用方应该确保命名空间内唯一（用 ``<execution_id>:<tool>`` 这种前缀）。
    """
    if name in TOOL_REGISTRY:
        logger.warning("Overriding existing tool registration: %s", name)
    TOOL_REGISTRY[name] = fn


def unregister_tool(name: str) -> bool:
    """从 TOOL_REGISTRY 移除一个工具。返回是否真的移除了。"""
    return TOOL_REGISTRY.pop(name, None) is not None


def unregister_namespace(prefix: str) -> int:
    """批量移除所有以 ``<prefix>__`` 开头的工具，返回被移除的数量。

    主要给 UI 自动化 MCPBridge.unregister(execution_id) 收尾用：execution
    跑完后清掉这个 execution 注册过的所有 ``<execution_id>__browser_*``。

    分隔符使用 ``__``：OpenAI Chat 接口要求 ``tools[i].function.name`` 匹配
    ``^[a-zA-Z0-9_-]+$``，所以 namespaced tool name 也用 ``__`` 拼。
    """
    if not prefix:
        return 0
    namespaced = f"{prefix}__"
    targets = [n for n in TOOL_REGISTRY if n.startswith(namespaced)]
    for n in targets:
        TOOL_REGISTRY.pop(n, None)
    return len(targets)


async def run_tool(name: str, arguments_json: str) -> str:
    """Execute a tool call and return a JSON string suitable for the tool role."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)
    try:
        args = json.loads(arguments_json) if arguments_json else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    payload = await fn(args)
    # Keep tool output compact — models don't need pretty-printing, and long
    # strings inflate token usage. Truncate individual snippets if too long.
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        payload["results"] = [_clamp_result(r) for r in payload["results"]]
    return json.dumps(payload, ensure_ascii=False)


def _clamp_result(r: Any) -> Any:
    if not isinstance(r, dict):
        return r
    return {
        "title": (r.get("title") or "")[:160],
        "snippet": (r.get("snippet") or "")[:480],
        "url": r.get("url"),
        "source": r.get("source"),
    }


def _describe_sources_for_prompt() -> str:
    """把可用的搜索源罗列成提示词片段，让模型知道有哪些资源可以调。"""
    info = available_sources()
    general = "、".join(item["name"] for item in info.get("general", []))
    vertical = "、".join(item["name"] for item in info.get("vertical", []))
    bundles = "、".join(item["id"] for item in info.get("bundles", []))
    return (
        f"综合搜索引擎：{general}。\n"
        f"垂直平台（百科/知识/技术/学术/生活）：{vertical}。\n"
        f"话题 bundle：{bundles}。"
    )


def build_agent_system_guidance() -> str:
    """生成"带当前时间"的 Agent 守则。

    历史教训：之前把守则写成一个 module-level 常量，今天的日期靠另一条系统消息
    `_runtime_context()` 递给模型，结果模型经常忽略边缘消息，按训练截止时知道
    的"最近 CBA 赛季"去查询，命中 2024/2025 老文章。把日期前置到守则正文顶部
    并强制 query/recency 行为，事故率明显下降。
    """
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    weekday_zh = "一二三四五六日"[now.weekday()]
    today_str = now.strftime("%Y年%-m月%-d日")
    today_iso = now.strftime("%Y-%m-%d")
    year = now.year

    return (
        "你是一个具备多源联网搜索能力的中文智能助手（Agent）。\n\n"
        "════════ 【当前时间】════════\n"
        f"今天是 {today_str}（星期{weekday_zh}），{now.strftime('%H:%M')}，"
        f"中国标准时间 UTC+8。本年度为 {year} 年。\n"
        "凡用户提到 \"今天/今日/现在/此刻/明天/昨天/本周/这周/最近/近期/本月/最新/这赛季/本届\""
        f"，你必须按上述日期换算成精确日期，**绝不允许沿用任何早于 {year} 年的旧"
        "日期或赛季**。如果你回忆不起最新事实，请通过 web_search 联网查询，不要"
        "凭训练数据里的旧记忆作答。\n"
        "═══════════════════════════════════\n\n"
        "行为守则：\n"
        f"1. 涉及实时/具体事实（{year} 年的天气、当日汇率、今日/近期新闻、"
        "具体城市政策、最新赛事、当前价格、近期赛程/比分/排名等），**必须**先调用 "
        "web_search 工具，并参照下面 2、3 条要求构造 query，再基于结果作答。"
        "不要凭训练记忆猜测当年赛事/赛程。\n"
        f"2. **query 必须包含具体年份**（默认就是当前 {year} 年），把"
        "\"今天/最近/近期/本届\"等相对词换成精确日期或赛季名。"
        f"反例：`CBA 季后赛 今天赛程`、`今日北京天气`。"
        f"正例：`{year} CBA 季后赛 {today_iso} 赛程`、`{today_iso} 北京 实时天气`。\n"
        "3. **recency 参数必须按问题时效设置**："
        "今天/此刻/赛程/比分/股价/汇率/油价/新闻/发布会/政策类问题 → recency=\"day\" 或 \"week\"；"
        "近一个月内的趋势/榜单 → recency=\"month\"；"
        "年度回顾/年报 → recency=\"year\"。"
        "**不显式设置 recency 时，搜索引擎会按相关度返回历史高权重文章，可能给你早于今年的数据。**\n"
        "4. **结果年份核对（关键）**：拿到 web_search 返回后，先扫一遍 results 的"
        f"标题/摘要里出现的年份。如果用户问的是\"今天/最近/本赛季\"但结果里"
        f"主要是 {year - 1} 年或更早的内容，**说明搜索没命中最新数据**，必须立即"
        "重新搜索一次：在 query 里再补一个更精确的日期/赛季名（例如 "
        f"`{today_iso}` 或 `{year}-{year + 1}赛季`），并把 recency 收紧到 \"day\"。\n"
        "5. 参数 sources 按需设置：多数问题默认 auto 即可（会自动混合百度/搜狗/"
        "360/必应/DuckDuckGo）；百科类问题可指定 ['baidu_baike','zhihu','wikipedia_zh']，"
        "技术/编程问题可指定 ['csdn','juejin','stackoverflow','github']，"
        "学术问题用 ['baidu_scholar']，生活/好物用 ['xiaohongshu','douban']。"
        "新闻/赛事类不必额外指定。\n"
        "6. 节制调用：核心数据已拿到就立刻停止搜索（最多 2 次）。已知通识"
        "（简单数学、基础编程语法、固化的历史常识）直接作答。\n"
        "7. 所有最终回答必须使用中文 Markdown（标题/列表/关键数据加粗）；"
        "引用搜索结果时用 [1][2] 角标，末尾附『参考来源』清单（标题 + 平台 + 日期，"
        "若摘要里能识别到日期）。\n"
        f"8. 如果搜索结果中明显标注的日期晚于 {today_iso}（例如赛事预告中的未来"
        "日期），可如实告知用户\"截至今日尚未开赛\"或\"此为预告\"，不要把"
        "未来日期当成已发生的事实。\n"
        "9. 不要把推理过程（reasoning）当成最终答案输出。\n\n"
        "当前可用搜索源清单（供你挑选 sources 参数）：\n"
        f"{_describe_sources_for_prompt()}"
    )


# 注：以前导出的 AGENT_SYSTEM_GUIDANCE 常量已废弃，请调用
# ``build_agent_system_guidance()`` 以拿到包含"当前时间"的最新守则。
# Python 的 ``from m import X`` 只会绑定一次，常量化会让"今天日期"被冻结
# 到首次导入时刻。
