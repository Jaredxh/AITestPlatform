"""缺料自造：启发式规则库 + 可选 LLM 推断（Task 9.2）。

Layer A 精确 key 匹配 → Layer B 子串模糊匹配 → Layer C 走默认 LLMConfig 或小样本回调。
"""

from __future__ import annotations

import json
import logging
import secrets
import string
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.modules.llm.models import LLMConfig
from app.modules.llm.providers import complete_chat

logger = logging.getLogger(__name__)

InferFn = Callable[[str, str, str], Awaitable[str]]

def _rand_hex(n: int) -> str:
    if n <= 0:
        return ""
    return secrets.token_hex((n + 1) // 2)[:n]


def _rand_digits(n: int) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(max(1, n)))


def _now_ms() -> str:
    return str(int(time.time() * 1000))


def _fallback_literal(key: str, hint: str, value_type: str) -> str:
    suffix = _rand_hex(4)
    if value_type == "dataset":
        row = {"id": suffix, "note": hint[:80] if hint else "synthetic"}
        return json.dumps([row], ensure_ascii=False)
    if value_type in ("multiline",):
        return f"[自动化测试 {suffix}]\n{hint or 'placeholder'}"
    return f"test_{key}_{suffix}"


async def infer_via_default_llm(db: AsyncSession, key: str, hint: str, value_type: str) -> str | None:
    """使用 ``is_default=True`` 的全局 LLM 配置推断一条测试数据；失败返回 None。"""
    row = (await db.execute(select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1))).scalar_one_or_none()
    if row is None:
        return None
    api_key = decrypt(row.api_key_encrypted) if row.api_key_encrypted else None
    sys_msg = (
        "你是测试数据生成器，只输出一条可用于 UI 自动化的示例值，不要解释、不要 Markdown、不要引号包裹。\n"
        f"期望类型标签：{value_type}\n"
        "若类型为 dataset，输出单行紧凑 JSON 数组（元素为对象）。"
    )
    user_msg = f"字段 key：{key}\n场景 hint：{hint or '（空）'}\n只输出值本体。"
    try:
        text = (
            await complete_chat(
                row.provider,
                row.model,
                messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}],
                api_key=api_key,
                base_url=row.base_url,
                temperature=0.3,
                max_tokens=512,
            )
        ).strip()
    except Exception:
        logger.exception("DataSynthesizer LLM infer failed for key=%s", key)
        return None
    if not text:
        return None
    return text.splitlines()[0].strip()


# ── 启发式规则（§3.6.8 + 扩展常见字段）────────────────────────────────────


def _rules_map() -> dict[str, Callable[[str], str]]:
    return {
        # 账号 / 身份
        "username": lambda h: f"test_user_{_rand_hex(4)}",
        "user_name": lambda h: f"test_user_{_rand_hex(4)}",
        "account": lambda h: f"test_acc_{_rand_hex(4)}",
        "nickname": lambda h: f"测试用户{_rand_hex(2)}",
        "login": lambda h: f"login_{_rand_hex(4)}",
        "password": lambda h: f"Test@{_rand_digits(4)}pw",
        "passwd": lambda h: f"Test@{_rand_digits(4)}pw",
        # 联系方式
        "phone": lambda h: f"138{_rand_digits(8)}",
        "mobile": lambda h: f"138{_rand_digits(8)}",
        "tel": lambda h: f"138{_rand_digits(8)}",
        "telephone": lambda h: f"138{_rand_digits(8)}",
        "user_phone": lambda h: f"138{_rand_digits(8)}",
        "contact_phone": lambda h: f"138{_rand_digits(8)}",
        "email": lambda h: f"test_{_rand_hex(4)}@example.com",
        "mail": lambda h: f"test_{_rand_hex(4)}@example.com",
        # 验证码
        "captcha": lambda h: "0000",
        "sms_code": lambda h: "123456",
        "verify_code": lambda h: "123456",
        "otp": lambda h: "123456",
        "code": lambda h: "123456",
        # 业务标识
        "order_id": lambda h: f"TEST{_now_ms()}",
        "order_no": lambda h: f"ORD{_now_ms()}",
        "product_id": lambda h: f"SKU-TEST-{_rand_digits(4)}",
        "sku": lambda h: f"SKU-{_rand_digits(6)}",
        "trade_no": lambda h: f"T{_now_ms()}",
        "transaction_id": lambda h: f"TX{_rand_digits(10)}",
        "coupon_code": lambda h: f"CPN-{_rand_hex(6).upper()}",
        "invite_code": lambda h: f"INV{_rand_hex(4).upper()}",
        # 地址 / 地域
        "address": lambda h: "北京市朝阳区测试地址 100 号",
        "city": lambda h: "北京市",
        "province": lambda h: "北京市",
        "zip_code": lambda h: "100000",
        "postcode": lambda h: "100000",
        # 内容 / 搜索
        "comment": lambda h: f"[自动化测试] {_rand_hex(8)} {(h or '')[:40]}".strip(),
        "remark": lambda h: f"[备注]{_rand_hex(6)}",
        "search": lambda h: (h.strip() if h.strip() else "test"),
        "keyword": lambda h: (h.strip() if h.strip() else "test"),
        "query": lambda h: (h.strip() if h.strip() else "test"),
        "title": lambda h: f"自动化标题 {_rand_hex(4)}",
        "subject": lambda h: f"测试主题 {_rand_hex(4)}",
        "content": lambda h: f"自动化正文 {_rand_hex(8)}",
        "description": lambda h: f"描述样本 {_rand_hex(4)}",
        "company": lambda h: f"测试公司{_rand_digits(3)}",
        "shop_name": lambda h: f"测试店铺{_rand_hex(3)}",
        # 数值
        "amount": lambda h: "99.00",
        "price": lambda h: "19.90",
        "quantity": lambda h: "1",
        "count": lambda h: "2",
        # 其它
        "url": lambda h: "https://example.com/test",
        "link": lambda h: "https://example.com/test",
        "id_card": lambda h: "110105199001011234",
        "bank_card": lambda h: "6222021234567890123",
        "uuid": lambda h: secrets.token_hex(8),
        "timestamp": lambda h: _now_ms(),
    }


HEURISTIC_RULES: dict[str, Callable[[str], str]] = _rules_map()


def _pick_fuzzy_rule(norm_key: str) -> tuple[str, Callable[[str], str]] | None:
    """优先匹配更长 pattern，避免 ``user`` 抢走 ``user_phone``。"""
    for pattern in sorted(HEURISTIC_RULES.keys(), key=len, reverse=True):
        if pattern in norm_key:
            return pattern, HEURISTIC_RULES[pattern]
    return None


@dataclass(frozen=True)
class SynthesizedValue:
    value: str
    source: str


class DataSynthesizer:
    """物料缺省时的临时值生成器。"""

    __test__ = False

    def __init__(
        self,
        *,
        db: AsyncSession | None = None,
        infer_fn: InferFn | None = None,
    ) -> None:
        self._db = db
        self._infer_fn = infer_fn

    async def synthesize(self, key: str, hint: str, value_type: str) -> SynthesizedValue:
        raw_key = (key or "").strip()
        norm_key = raw_key.lower()
        hint_clean = (hint or "").strip()

        if norm_key in HEURISTIC_RULES:
            fn = HEURISTIC_RULES[norm_key]
            return SynthesizedValue(value=fn(hint_clean), source="heuristic_exact")

        fuzzy = _pick_fuzzy_rule(norm_key)
        if fuzzy is not None:
            pat, fn = fuzzy
            return SynthesizedValue(value=fn(hint_clean), source=f"heuristic_fuzzy:{pat}")

        if self._infer_fn is not None:
            body = await self._infer_fn(raw_key, hint_clean, value_type)
            return SynthesizedValue(value=body, source="ai_inferred")

        if self._db is not None:
            inferred = await infer_via_default_llm(self._db, raw_key, hint_clean, value_type)
            if inferred is not None:
                return SynthesizedValue(value=inferred, source="ai_inferred")

        return SynthesizedValue(
            value=_fallback_literal(raw_key, hint_clean, value_type),
            source="fallback_no_llm",
        )


__all__ = [
    "HEURISTIC_RULES",
    "DataSynthesizer",
    "InferFn",
    "SynthesizedValue",
    "infer_via_default_llm",
]
