"""random 类型物料的模板 → 明文实例化。

本 task 只需覆盖最常见的 5 种模板，每种模板都是**幂等**（不读写状态）+ **无侧
效**（不打网络 / 不写日志），这样：
- 单测写起来简单（对同样的模板多次调用，结果分布合理即可）
- 执行引擎可以在极短时间内批量实例化几十个 random 物料
- 不依赖 requests / 三方库，保持纯 stdlib

支持的模板 DSL（见 PHASE2_DESIGN §2.4.2）：

- ``phone:CN``              → 11 位中国大陆手机号，首位 1 + 第二位 3-9
- ``phone:US``              → 11 位"1"前缀美国号，后跟 10 位数字
- ``email``                 → random_prefix@example.com
- ``email:gmail.com``       → random_prefix@gmail.com
- ``uuid`` / ``uuid4``      → 标准 UUID4
- ``digits:8``              → 8 位纯数字
- ``hex:16``                → 16 位小写 hex
- ``letters:6``             → 6 位纯字母（大小写混合）
- ``alnum:8``               → 8 位字母 + 数字
- ``username`` / ``name``   → 随机 6-10 位 alnum 前缀
- ``timestamp``             → 当前 Unix 毫秒时间戳（字符串形式）

未识别模板返回原字符串（让 AI / 用户自己判断是否 typo）并在 logger 里 warn
一次——**不** raise，避免一条物料生成失败拖垮整个执行。

边界约束：
- N 最大 128（避免用户手抖写 ``digits:1000000`` 吃内存）
- N 最小 1
- 所有数字转 int 失败（``digits:abc``）按原字符串返回
"""

from __future__ import annotations

import logging
import random
import secrets
import string
import time
import uuid

logger = logging.getLogger(__name__)

_MAX_N = 128
_DEFAULT_N = 8


def generate(template: str) -> str:
    """把 random 模板实例化为一个具体 string。

    Args:
        template: 模板字符串，如 ``phone:CN`` / ``digits:8``。为空串时返回空串。

    Returns:
        实例化后的 string（一定是 str，从不是 None）。

    Note:
        未识别模板会在 logger 里 warn 并原样返回，以保持 fail-open（执行引擎
        继续跑其他物料）。
    """
    if not template:
        return ""

    # 规范化：转小写 + 去空格；但原串长度短、非 ascii 概率极低，不做更复杂的 normalize
    key = template.strip()

    # 形如 "type:param"：split 成 prefix + param
    if ":" in key:
        prefix, _, param = key.partition(":")
        prefix = prefix.lower()
    else:
        prefix = key.lower()
        param = ""

    handler = _HANDLERS.get(prefix)
    if handler is None:
        logger.warning("random_generator: 未识别的模板 %r，原样返回", template)
        return template
    try:
        return handler(param)
    except Exception as exc:  # noqa: BLE001
        # 不向上抛；单个物料生成出问题不应该影响整个执行
        logger.warning("random_generator: 模板 %r 实例化失败（%s），原样返回", template, exc)
        return template


# ─── 单个 type 的实现 ─────────────────────────────────────────────────


def _gen_phone(param: str) -> str:
    region = param.upper() or "CN"
    if region == "CN":
        # 11 位：首位 1 + 第二位 3..9（避开 0 / 1 / 2 未用段）+ 9 位随机
        second = random.choice("3456789")
        rest = "".join(random.choice("0123456789") for _ in range(9))
        return f"1{second}{rest}"
    if region == "US":
        # 简化版美国号：1 + 10 位数字（不严格校验 NANP area-code 规则）
        return "1" + "".join(random.choice("0123456789") for _ in range(10))
    logger.warning("phone region %r 未支持，回退到 CN", region)
    return _gen_phone("CN")


def _gen_email(param: str) -> str:
    domain = param.strip() or "example.com"
    prefix_len = random.randint(6, 10)
    prefix = "".join(random.choice(string.ascii_lowercase) for _ in range(prefix_len))
    # 随机 3 位数字后缀增加唯一性（同一测试批次用 50 次 email 不想全重复）
    suffix = "".join(random.choice(string.digits) for _ in range(3))
    return f"{prefix}{suffix}@{domain}"


def _gen_uuid(_param: str) -> str:
    return str(uuid.uuid4())


def _parse_n(param: str, default: int = _DEFAULT_N) -> int:
    """把 ``param`` 解析成 1..MAX_N 的整数，失败则退回 default。"""
    if not param:
        return default
    try:
        n = int(param)
    except ValueError:
        logger.warning("random_generator: N=%r 不是合法整数，回退到 %d", param, default)
        return default
    if n <= 0:
        return 1
    if n > _MAX_N:
        logger.warning("random_generator: N=%d 超过上限 %d，截断", n, _MAX_N)
        return _MAX_N
    return n


def _gen_digits(param: str) -> str:
    n = _parse_n(param)
    return "".join(random.choice(string.digits) for _ in range(n))


def _gen_hex(param: str) -> str:
    n = _parse_n(param, default=16)
    # secrets 更均匀；hex 长度 = 字节数 * 2，所以按 bit 切
    raw = secrets.token_hex((n + 1) // 2)
    return raw[:n]


def _gen_letters(param: str) -> str:
    n = _parse_n(param, default=6)
    return "".join(random.choice(string.ascii_letters) for _ in range(n))


def _gen_alnum(param: str) -> str:
    n = _parse_n(param, default=8)
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def _gen_username(param: str) -> str:
    # param 可选指定长度，否则 6-10 随机
    if param:
        n = _parse_n(param, default=8)
    else:
        n = random.randint(6, 10)
    # 首位必须是字母，避免 "123abc" 这种不合法用户名
    first = random.choice(string.ascii_lowercase)
    rest = "".join(
        random.choice(string.ascii_lowercase + string.digits) for _ in range(n - 1)
    )
    return first + rest


def _gen_timestamp(_param: str) -> str:
    # 毫秒级 Unix 时间戳；随机物料的典型用法是生成"保证唯一"的订单号
    return str(int(time.time() * 1000))


_HANDLERS: dict[str, "callable[[str], str]"] = {  # type: ignore[type-arg]
    "phone": _gen_phone,
    "email": _gen_email,
    "uuid": _gen_uuid,
    "uuid4": _gen_uuid,
    "digits": _gen_digits,
    "hex": _gen_hex,
    "letters": _gen_letters,
    "alnum": _gen_alnum,
    "username": _gen_username,
    "name": _gen_username,
    "timestamp": _gen_timestamp,
}


SUPPORTED_TEMPLATES: tuple[str, ...] = tuple(sorted(_HANDLERS.keys()))
"""测试 / 文档 / 前端提示用的模板列表。"""


__all__ = ["SUPPORTED_TEMPLATES", "generate"]
