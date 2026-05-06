"""AI 测试用例生成服务。

架构：
  1. start_generation_batch 创建批次记录并派发真正的后台任务，立即返回。
  2. 后台任务 run_generation_batch 用自己的 DB session 拉 LLM 流，并把每个 chunk
     广播给 BatchStreamHub（进程内 pub-sub），同时按批次刷进数据库。
  3. 前端通过 SSE endpoint `/generation-batches/{id}/stream` 订阅任何活跃任务
     （刷新/切页后也能断线重连，订阅者从头收到所有缓冲事件）。
  4. 也保留纯轮询接口 `/generation-batches/{id}`：SSE 不可用时走回退。
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import decrypt
from app.core.exceptions import AppException, NotFoundException
from app.database import async_session_factory
from app.modules.auth.models import User
from app.modules.llm.models import LLMConfig
from app.modules.llm.prompts.testcase_gen import (
    TESTCASE_GEN_SYSTEM_PROMPT,
    build_testcase_gen_user_prompt,
)
from app.modules.llm.providers import build_client
from app.modules.requirements.models import RequirementDocument
from app.modules.testcases.models import (
    AIGenerationBatch,
    Testcase,
    TestcaseModule,
    TestcaseStep,
)
from app.modules.testcases.schemas import (
    BatchAcceptRequest,
    BatchAcceptResponse,
    GenerateRequest,
    GenerationBatchResponse,
)

logger = logging.getLogger(__name__)


# ─── in-process stream hub ─────────────────────────────────────────
class _BatchStream:
    """Buffered broadcaster for a single AI generation batch.

    - ``append()`` 追加一个事件并唤醒所有订阅者；
    - ``subscribe()`` 从 ``from_idx`` 开始按顺序拿到所有历史 + 新事件，直到完成。
    """

    __slots__ = ("chunks", "done", "_cond", "created_at")

    def __init__(self) -> None:
        self.chunks: list[tuple[str, dict]] = []
        self.done: bool = False
        self._cond: asyncio.Condition = asyncio.Condition()
        self.created_at: float = time.monotonic()

    async def append(self, event: str, data: dict) -> None:
        async with self._cond:
            self.chunks.append((event, data))
            if event in ("done", "error"):
                self.done = True
            self._cond.notify_all()

    async def subscribe(self, from_idx: int = 0) -> AsyncGenerator[tuple[str, dict], None]:
        while True:
            async with self._cond:
                while from_idx >= len(self.chunks):
                    if self.done:
                        return
                    await self._cond.wait()
                chunk = self.chunks[from_idx]
            from_idx += 1
            yield chunk


class _BatchStreamHub:
    """Process-wide registry mapping batch_id -> _BatchStream."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, _BatchStream] = {}
        self._lock = asyncio.Lock()

    async def register(self, batch_id: uuid.UUID) -> _BatchStream:
        async with self._lock:
            stream = _BatchStream()
            self._store[batch_id] = stream
            self._evict_stale_locked()
            return stream

    def get(self, batch_id: uuid.UUID) -> _BatchStream | None:
        return self._store.get(batch_id)

    def _evict_stale_locked(self) -> None:
        """Drop streams older than 30 min so memory doesn't leak across days."""
        cutoff = time.monotonic() - 30 * 60
        stale = [bid for bid, s in self._store.items() if s.done and s.created_at < cutoff]
        for bid in stale:
            self._store.pop(bid, None)


STREAM_HUB = _BatchStreamHub()


# ─── public API ────────────────────────────────────────────────────
async def start_generation_batch(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: GenerateRequest,
    user: User,
) -> GenerationBatchResponse:
    """Create the batch record, spawn a background task, return immediately.

    The background task streams from the LLM into STREAM_HUB, so the dialog can
    subscribe via SSE while the task owns its own DB session.
    """
    doc = await _get_document_or_404(db, data.document_id)
    if not doc.content_text:
        raise AppException("文档尚未解析成功，无法生成用例", code="DOC_NOT_PARSED", status_code=422)

    config, _api_key = await _resolve_llm_config(db, data.llm_config_id)
    module_name = await _get_module_name(db, data.module_id)
    batch = AIGenerationBatch(
        project_id=project_id,
        document_id=data.document_id,
        module_id=data.module_id,
        user_id=user.id,
        llm_config_id=config.id,
        model_used=config.model,
        status="generating",
    )
    db.add(batch)
    await db.flush()
    batch_id = batch.id
    response = _to_generation_batch_response(batch, doc.filename, module_name, [])

    # Commit so the background task's own session can see the batch.
    await db.commit()

    # Register stream BEFORE returning: any immediate SSE connect must find it.
    await STREAM_HUB.register(batch_id)
    asyncio.create_task(run_generation_batch(batch_id))
    return response


async def run_generation_batch(batch_id: uuid.UUID) -> None:
    """Stream the LLM response, broadcast chunks, persist result."""
    stream = STREAM_HUB.get(batch_id) or await STREAM_HUB.register(batch_id)

    async with async_session_factory() as db:
        result = await db.execute(
            select(AIGenerationBatch)
            .options(
                selectinload(AIGenerationBatch.document),
                selectinload(AIGenerationBatch.llm_config),
                selectinload(AIGenerationBatch.module),
            )
            .where(AIGenerationBatch.id == batch_id)
        )
        batch = result.scalar_one_or_none()
        if not batch:
            await stream.append("error", {"message": "生成任务不存在"})
            await stream.append("done", {"batch_id": str(batch_id)})
            return
        doc = batch.document
        config = batch.llm_config
        module_name = batch.module.name if batch.module else None
        if not doc or not doc.content_text or not config:
            batch.status = "failed"
            batch.raw_response = "需求文档或 LLM 配置不存在"
            await db.commit()
            await stream.append("error", {"message": batch.raw_response})
            await stream.append("done", {"batch_id": str(batch_id)})
            return

        await stream.append("batch_start", {
            "batch_id": str(batch_id),
            "model": config.model,
            "document": doc.filename,
            "module_name": module_name,
            "content": f"已提交给 {config.model}，正在分析「{doc.filename}」并生成用例…\n",
        })

        api_key = decrypt(config.api_key_encrypted) if config.api_key_encrypted else None
        start_ms = _now_ms()
        full_content = ""
        chunk_counter = 0
        FLUSH_EVERY_N_CHUNKS = 40  # persist raw_response incrementally for polling fallback
        try:
            client = build_client(config.provider, api_key, config.base_url)
            try:
                llm_stream = await client.chat.completions.create(
                    model=config.model,
                    messages=[
                        {"role": "system", "content": TESTCASE_GEN_SYSTEM_PROMPT},
                        {"role": "user", "content": build_testcase_gen_user_prompt(
                            doc.filename, doc.content_text,
                        )},
                    ],
                    temperature=0.4,
                    max_tokens=config.max_tokens or 8192,
                    stream=True,
                )
                async for chunk in llm_stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        delta = chunk.choices[0].delta.content
                        full_content += delta
                        await stream.append("delta", {"content": delta})
                        chunk_counter += 1
                        if chunk_counter % FLUSH_EVERY_N_CHUNKS == 0:
                            batch.raw_response = full_content
                            await db.commit()
            finally:
                await client.close()

            testcases = _parse_testcases_json(full_content)
            batch.generated_count = len(testcases)
            batch.status = "completed"
            batch.raw_response = full_content
            await db.commit()
            await stream.append("generated", {
                "batch_id": str(batch_id),
                "testcases": testcases,
                "count": len(testcases),
                "module_name": module_name,
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("Background testcase generation failed for batch %s", batch_id)
            batch.status = "failed"
            batch.raw_response = full_content or f"{type(exc).__name__}: {exc}"
            await db.commit()
            await stream.append("error", {
                "message": f"生成失败: {type(exc).__name__}: {exc}",
            })
        finally:
            batch.generation_time_ms = _now_ms() - start_ms
            try:
                await db.commit()
            except Exception:  # noqa: BLE001
                pass
            await stream.append("done", {"batch_id": str(batch_id)})


async def subscribe_batch_stream(
    batch_id: uuid.UUID, user: User
) -> AsyncGenerator[str, None]:
    """SSE subscription: replays buffered events then tails new ones.

    Used by the generate dialog; survives refresh because the hub retains events
    until the task finishes. If the hub has no record (stale batch, server
    restart, unknown id), we send a single snapshot from the DB instead.

    **Orphan task self-healing**: 如果 DB 里 status="generating" 但 hub 里没有
    live stream，说明承载 task 的进程死了（容器重启、崩溃、被 kill 等）。这种
    "孤儿任务"如果不主动收尸，前端 SSE 拿到 done 之后又去 poll 看到 generating
    会再次重连——形成死循环，浮窗里只能看到"初始化中..."却没任何进度，用户也
    无法中断。这里检测到 orphan 立即把 batch 标 failed，让前端立刻收到清晰的
    error+done，UI 进入"finished + 重新发起"。
    """
    async with async_session_factory() as db:
        batch = await db.execute(
            select(AIGenerationBatch)
            .options(
                selectinload(AIGenerationBatch.document),
                selectinload(AIGenerationBatch.module),
            )
            .where(AIGenerationBatch.id == batch_id)
            .where(AIGenerationBatch.user_id == user.id)
        )
        record = batch.scalar_one_or_none()
    if not record:
        yield _sse_event("error", {"message": "生成任务不存在或无权访问"})
        yield _sse_event("done", {"batch_id": str(batch_id)})
        return

    stream = STREAM_HUB.get(batch_id)
    if stream is None:
        # No live stream. 三种可能：
        #   (a) 任务已正常结束（completed / failed）—— 走原 replay 路径
        #   (b) 任务从未真正开始（status=generating 但承载 task 早已死掉）
        #       —— orphan，需要主动收尸标 failed
        #   (c) 后端进程刚重启，任务上一周期就在跑—— 也属于 orphan
        if record.status == "generating":
            await _mark_orphan_failed(
                batch_id,
                "任务被中断（后端进程已重启或异常退出，请重新发起）",
            )
            yield _sse_event(
                "error",
                {"message": "任务被中断（后端进程已重启或异常退出，请重新发起）"},
            )
            yield _sse_event("done", {"batch_id": str(batch_id)})
            return

        # 正常的"任务已结束 + replay 持久化数据"路径
        if record.raw_response:
            yield _sse_event("delta", {"content": record.raw_response})
        if record.status == "completed":
            testcases: list[dict] = []
            try:
                testcases = _parse_testcases_json(record.raw_response or "")
            except Exception:  # noqa: BLE001
                testcases = []
            yield _sse_event("generated", {
                "batch_id": str(batch_id),
                "testcases": testcases,
                "count": len(testcases),
                "module_name": record.module.name if record.module else None,
            })
        elif record.status == "failed":
            yield _sse_event("error", {"message": record.raw_response or "生成失败"})
        yield _sse_event("done", {"batch_id": str(batch_id)})
        return

    try:
        async for event, data in stream.subscribe():
            yield _sse_event(event, data)
    except asyncio.CancelledError:
        return


async def _mark_orphan_failed(batch_id: uuid.UUID, reason: str) -> None:
    """把 status=generating 的孤儿批次原地标记为 failed。"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(AIGenerationBatch).where(AIGenerationBatch.id == batch_id)
        )
        batch = result.scalar_one_or_none()
        if not batch:
            return
        if batch.status != "generating":
            return  # 已经是 completed / failed，无需干预
        batch.status = "failed"
        if not batch.raw_response:
            batch.raw_response = reason
        await db.commit()
        logger.info(
            "Marked orphan AI generation batch %s as failed: %s", batch_id, reason
        )


async def cancel_generation_batch(
    batch_id: uuid.UUID, user: User, *, reason: str = "用户主动终止"
) -> dict:
    """强制结束一个 AI 生成任务。

    使用场景：
    - 任务卡住/无进度，用户想立刻关掉它（不等 LLM 自然结束）
    - 后端进程死了留下孤儿任务，用户在另一台浏览器/会话主动收尸

    实现细节：
    - 把 DB 里 status 标为 ``failed``，原因写到 ``raw_response``；
    - 如果 hub 里还有 live stream，往里 push 一个 error+done 让所有订阅者收到；
    - 我们 **不** 真的去 ``cancel()`` 那个 ``asyncio.create_task``：那个 task
      持有 LLM HTTP 连接，强 cancel 反而可能让 LLM 服务侧产生悬挂连接。
      让它继续跑完自己的 finally 即可——它自己 commit 时会发现 status 已经
      是 failed，不会回写成 completed。
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(AIGenerationBatch)
            .where(AIGenerationBatch.id == batch_id)
            .where(AIGenerationBatch.user_id == user.id)
        )
        batch = result.scalar_one_or_none()
        if not batch:
            raise NotFoundException("生成任务不存在")
        already_done = batch.status in ("completed", "failed")
        if not already_done:
            batch.status = "failed"
            batch.raw_response = (batch.raw_response or "") + (
                f"\n\n[{reason}]" if batch.raw_response else reason
            )
            await db.commit()

    stream = STREAM_HUB.get(batch_id)
    if stream and not stream.done:
        await stream.append("error", {"message": reason})
        await stream.append("done", {"batch_id": str(batch_id)})

    return {
        "batch_id": str(batch_id),
        "status": "failed",
        "already_done": already_done,
        "reason": reason,
    }


async def batch_accept_testcases(
    db: AsyncSession,
    data: BatchAcceptRequest,
    user: User,
) -> BatchAcceptResponse:
    """批量接受 AI 生成的用例入库。"""

    batch = await _get_batch_or_404(db, data.batch_id)

    target_module_id = data.module_id if data.module_id is not None else batch.module_id

    # 一次性预占整批 case_no，避免每条用例都要 SELECT MAX 一次。
    from app.modules.testcases.service import _allocate_case_no
    next_case_no = await _allocate_case_no(db, batch.project_id)

    accepted = 0
    for tc_data in data.testcases:
        testcase = Testcase(
            project_id=batch.project_id,
            case_no=next_case_no,
            module_id=target_module_id,
            title=tc_data.title,
            precondition=tc_data.precondition,
            priority=tc_data.priority,
            source="ai_generated",
            created_by=user.id,
            generation_batch_id=batch.id,
        )
        next_case_no += 1
        db.add(testcase)
        await db.flush()

        for step_data in tc_data.steps:
            step = TestcaseStep(
                testcase_id=testcase.id,
                step_number=step_data.step_number,
                action=step_data.action,
                expected_result=step_data.expected_result,
            )
            db.add(step)

        accepted += 1

    batch.accepted_count = (batch.accepted_count or 0) + accepted
    await db.flush()

    return BatchAcceptResponse(accepted_count=accepted, batch_id=batch.id)


_ORPHAN_GRACE_SECONDS = 90  # 90 秒内允许 generating 任务暂时没有 hub 流（启动间隙）


async def list_generation_batches(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
) -> list[GenerationBatchResponse]:
    """Return recent unfinished AI generation batches so frontend can restore after refresh.

    顺手做"孤儿任务自愈"：超过 ``_ORPHAN_GRACE_SECONDS`` 秒还在 generating 但
    hub 里查不到 live stream 的任务，几乎可以肯定承载它的进程已经死了。原地
    标 failed，否则前端会一直把它当作"在跑"挂在悬浮窗、用户又点不动。
    """
    result = await db.execute(
        select(AIGenerationBatch)
        .options(
            selectinload(AIGenerationBatch.document),
            selectinload(AIGenerationBatch.module),
        )
        .where(AIGenerationBatch.project_id == project_id)
        .where(AIGenerationBatch.user_id == user.id)
        .order_by(AIGenerationBatch.created_at.desc())
        .limit(20)
    )
    batches = list(result.scalars().unique().all())

    # 第一遍：把孤儿任务原地标 failed，再走原有过滤逻辑
    now = datetime.now(timezone.utc)
    for batch in batches:
        if batch.status != "generating":
            continue
        # hub 里没有 live stream 且超出宽限期 → 孤儿
        if STREAM_HUB.get(batch.id) is not None:
            continue
        created = batch.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now - created < timedelta(seconds=_ORPHAN_GRACE_SECONDS):
            continue
        batch.status = "failed"
        if not batch.raw_response:
            batch.raw_response = "任务被中断（后端进程已重启或异常退出，请重新发起）"
        logger.info("Auto-healing orphan AI batch %s on list", batch.id)
    await db.commit()

    responses: list[GenerationBatchResponse] = []
    for batch in batches:
        testcases: list[dict] = []
        if batch.raw_response and batch.status == "completed":
            try:
                testcases = _parse_testcases_json(batch.raw_response)
            except Exception:  # noqa: BLE001
                testcases = []
        pending_count = max((batch.generated_count or 0) - (batch.accepted_count or 0), 0)
        if batch.status == "completed" and pending_count <= 0:
            continue
        if batch.status == "failed":
            # 失败的任务不再返给前端做"恢复"——避免反复弹错；用户想看历史
            # 可以走单独的批次详情接口。
            continue
        if batch.status not in {"generating", "completed", "failed"}:
            continue
        responses.append(_to_generation_batch_response(
            batch,
            batch.document.filename if batch.document else None,
            batch.module.name if batch.module else None,
            testcases,
        ))
    return responses


async def get_generation_batch(
    db: AsyncSession,
    batch_id: uuid.UUID,
    user: User,
) -> GenerationBatchResponse:
    result = await db.execute(
        select(AIGenerationBatch)
        .options(
            selectinload(AIGenerationBatch.document),
            selectinload(AIGenerationBatch.module),
        )
        .where(AIGenerationBatch.id == batch_id)
        .where(AIGenerationBatch.user_id == user.id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise NotFoundException("生成任务不存在")
    testcases: list[dict] = []
    if batch.raw_response and batch.status == "completed":
        try:
            testcases = _parse_testcases_json(batch.raw_response)
        except Exception:  # noqa: BLE001
            testcases = []
    return _to_generation_batch_response(
        batch,
        batch.document.filename if batch.document else None,
        batch.module.name if batch.module else None,
        testcases,
    )


def _to_generation_batch_response(
    batch: AIGenerationBatch,
    document_name: str | None,
    module_name: str | None,
    testcases: list[dict],
) -> GenerationBatchResponse:
    return GenerationBatchResponse(
        id=batch.id,
        project_id=batch.project_id,
        document_id=batch.document_id,
        module_id=batch.module_id,
        model_used=batch.model_used,
        status=batch.status,
        generated_count=batch.generated_count or len(testcases),
        accepted_count=batch.accepted_count or 0,
        generation_time_ms=batch.generation_time_ms,
        created_at=batch.created_at,
        document_name=document_name,
        module_name=module_name,
        testcases=testcases,
    )


async def _get_module_name(
    db: AsyncSession, module_id: uuid.UUID | None
) -> str | None:
    if not module_id:
        return None
    result = await db.execute(
        select(TestcaseModule.name).where(TestcaseModule.id == module_id)
    )
    return result.scalar_one_or_none()


# ── Internal helpers ──


def _parse_testcases_json(raw: str) -> list[dict]:
    """Best-effort parse of LLM testcase JSON.

    LLMs frequently emit tiny JSON defects even when prompted carefully:
    trailing commas, smart quotes, incomplete final object, prose prefix/suffix.
    We try progressively more aggressive strategies so a single stray character
    doesn't force the user to re-run a 60-second generation for nothing.
    """
    cleaned = raw.strip()

    code_fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if code_fence:
        cleaned = code_fence.group(1).strip()

    array_match = re.search(r"\[[\s\S]*\]", cleaned)
    if array_match:
        cleaned = array_match.group()

    data: list | None = None
    for candidate in _json_repair_candidates(cleaned):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            data = parsed
            break
        if isinstance(parsed, dict):
            data = [parsed]
            break

    if data is None:
        data = _extract_object_blocks(cleaned)

    if not data:
        raise AppException(
            "AI 返回的用例结果格式无法解析", code="PARSE_ERROR", status_code=502
        )

    if not isinstance(data, list):
        raise AppException("AI 返回的结果不是数组格式", code="INVALID_FORMAT", status_code=502)

    validated = []
    for item in data:
        if not isinstance(item, dict) or "title" not in item:
            continue
        validated.append({
            "title": item["title"],
            "precondition": item.get("precondition"),
            "priority": item.get("priority", "medium"),
            "steps": [
                {
                    "step_number": s.get("step_number", i + 1),
                    "action": s.get("action", ""),
                    "expected_result": s.get("expected_result"),
                }
                for i, s in enumerate(item.get("steps", []))
                if isinstance(s, dict) and s.get("action")
            ],
        })

    return validated


def _json_repair_candidates(raw: str) -> list[str]:
    candidates: list[str] = [raw]
    repaired = re.sub(r",\s*([}\]])", r"\1", raw)
    if repaired != raw:
        candidates.append(repaired)
    normalized = (
        repaired
        .replace("“", '"').replace("”", '"')
        .replace("‘", "'").replace("’", "'")
    )
    if normalized != repaired:
        candidates.append(normalized)
    balanced = _balance_brackets(normalized)
    if balanced != normalized:
        candidates.append(balanced)
    return candidates


def _balance_brackets(text: str) -> str:
    open_curly = text.count("{")
    close_curly = text.count("}")
    open_sq = text.count("[")
    close_sq = text.count("]")
    suffix = "}" * max(open_curly - close_curly, 0) + "]" * max(open_sq - close_sq, 0)
    return text + suffix if suffix else text


def _extract_object_blocks(text: str) -> list[dict]:
    out: list[dict] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                chunk = text[start : i + 1]
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict):
                    out.append(obj)
                start = -1
    return out


def _sse_event(event: str, data: dict) -> str:
    """Unified SSE envelope: embed the event name as ``type`` inside the JSON
    payload so the generic frontend dispatcher (useSSE) can branch on it
    without needing a separate ``event:`` line parser."""
    payload = {"type": event, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _get_document_or_404(
    db: AsyncSession, doc_id: uuid.UUID
) -> RequirementDocument:
    result = await db.execute(
        select(RequirementDocument).where(RequirementDocument.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundException("需求文档不存在")
    return doc


async def _resolve_llm_config(
    db: AsyncSession, config_id: uuid.UUID | None
) -> tuple[LLMConfig, str | None]:
    if config_id:
        result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
        config = result.scalar_one_or_none()
        if not config:
            raise NotFoundException("指定的 LLM 配置不存在")
    else:
        result = await db.execute(
            select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise AppException(
                "未配置默认 LLM，请先在设置中添加 LLM 配置",
                code="NO_LLM_CONFIG", status_code=422,
            )

    api_key = decrypt(config.api_key_encrypted) if config.api_key_encrypted else None
    return config, api_key


async def _get_batch_or_404(
    db: AsyncSession, batch_id: uuid.UUID
) -> AIGenerationBatch:
    result = await db.execute(
        select(AIGenerationBatch).where(AIGenerationBatch.id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise NotFoundException("生成批次不存在")
    return batch


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
