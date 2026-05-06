# 第三期实现计划 - Skill 技能包管理与 OpenClaw Agent 能力（v3.0）

> **v3.0 关键调整**（基于一二期已落地的真实代码状态 + 用户最新反馈）：
>
> - **不再合并 prompt + skill 表**，新建独立 `skills` / `skill_versions` / `skill_usage_logs` / `skill_safety_scans` 四张表，与一期 `prompt_templates` 各管各的
> - **零侵入一二期主流程**：复用一期 `agent_tools.TOOL_REGISTRY` + `register_tool` / `unregister_namespace` 机制；`chat_service` 仅在 `_handle_chat_stream` 内增量加 `skill_router.compose()` 一行
> - **Task 数从 v2.0 的 7 收敛到 6**：合并 OpenClaw 真机校验进 import/export task；"内置 Skill 重构"作为 Task 12.4 的一部分，不再独立
> - **MVP 优先**：先完成"管理 + 激活 + 调用"主流程；用户评分功能延后到 v3.1
>
> **保留 v2.0 全部架构精华**（详见 [`PHASE3_DESIGN.md`](./PHASE3_DESIGN.md)）：
> - 三层激活：always 注入 / 手动激活 / 触发词召回 + agent_callable
> - Lazy Tool 化：候选 skill 仅暴露 description（≈ 200 token），模型选中才 lazy load 全文
> - 安全治理：导入扫描 + 防御性 system prompt + 内置 system_* slug 锁定
> - OpenClaw 兼容：SKILL.md 标准 + 字段透传 + 导出 ZIP 真机可用

## 零侵入承诺（验收门槛）

> 三期所有 PR **必须满足以下 6 条回归基线**，详细自检清单见 `PHASE3_DESIGN.md` 第 11 章。

| 一二期路径 | 三期影响 | 验证 |
|---|---|---|
| AI 对话普通问答（`_handle_chat_stream`）| 仅在主循环开始前增量加 `skill_router.compose()`；空 `SkillContext` 时 `tools=TOOLS` 与 `messages` 与三期前字节等价 | `tests/skills/test_zero_intrusion.py` |
| AI 评审需求（`_handle_review_intent`）| **完全不动**——意图快通道在 `send_message_stream` 第 567 行命中后直接 return | `tests/llm/test_chat_intents.py` 整套通过 + e2e |
| AI 生成用例（`_handle_generate_intent`）| **完全不动**——同上第 577 行 | 同上 |
| AI 驱动 UI 自动化（前端"用例管理 → 执行 UI 自动化"主入口）| **完全不动**——`chat_service.py` 全文不 import 任何 `ui_automation` 子模块（已 grep 确认）| 二期 `tests/ui_automation/*` 整套通过 |
| PromptTemplate 系统 | **完全不动**——三期建独立 `skills` 表 | `prompt_templates` 表的 ORM/SQL 引用范围三期前后完全相等 |
| 一期 web_search tool | **完全不动**——`tools = TOOLS + skill_context.candidate_tools`，加号左侧不变 | `TOOLS / web_search / _execute_web_search` 0 改动 |

**关键审计结论**（来自对一期 `chat_service.send_message_stream` 第 521-589 行的实证）：

```python
# chat_service.py 入口已有的意图分发逻辑（三期不动）：
intent = detect_intent(user_content)
if intent.intent == IntentType.REVIEW and session.project_id:
    async for chunk in _handle_review_intent(...): yield chunk
    return                                       # ← 一期评审走这里直接 return
if intent.intent == IntentType.GENERATE_TESTCASES and session.project_id:
    async for chunk in _handle_generate_intent(...): yield chunk
    return                                       # ← 一期生成走这里直接 return
async for chunk in _handle_chat_stream(...):    # ← 三期 SkillRouter 仅注入到这条分支
    yield chunk
```

> 这一架构保证：意图快通道命中（评审 / 生成）时，SkillRouter 永远不会被调用 → 一期能力 0 回归；只有"普通对话"或"二期已下线 RUN_UI_TEST 后没有快通道拦截的场景（如跑用例）"才会触发 SkillRouter。

## 前置条件

- 一期全部功能已稳定（特别是 `agent_tools.TOOL_REGISTRY` + `chat_service.run_tool` + `PromptTemplate`）
- 二期 UI 自动化模块已落地（`ExecutionEngine` 可被 `platform_run_ui_execution` 桥接调用）
- 已了解一期 OpenAI tool-calling 协议（三期完全复用）

## 依赖变更

```bash
# 后端 Python 新增
./run.sh add-backend python-frontmatter   # 解析 SKILL.md（YAML 前言 + Markdown 正文）
# pyyaml 已被 frontmatter 间接依赖，无需单独装
```

无需新增基础设施容器。docker-compose.yml / .env / nginx 均不变。

## Task 规模约定

每个 task 标注规模等级（同二期约定）：

- 🟢 **S**（小）：≤ 3 文件 / ≤ 250 行 / ≤ 2 验证场景，会话宽裕
- 🟡 **M**（中）：≤ 6 文件 / ≤ 500 行 / ≤ 4 验证场景，会话舒适
- 🟠 **L**（大）：接近上限，但仍能在单会话完成；后续如发现超时再拆

---

## Phase 12：Skill 技能包模块（6 个 task）

### Task 12.1 — Skill 数据模型 + 迁移 + 基础 schemas 🟡 M

**目标**：建立独立的 skill 模块（与 prompt_templates 完全分离），包含 4 张表 + ORM + 基础 Pydantic schemas。

**前置依赖**：无（独立新增）

**产出文件**：

- `app/modules/skills/__init__.py`
- `app/modules/skills/models.py`
  - `class Skill(Base)`：4.1 字段（含 slug、triggers、activation_mode、tools_required、attachments、source、is_enabled、safety_scan_status、metadata、db_version）
  - `class SkillVersion(Base)`：每次保存追加
  - `class SkillUsageLog(Base)`：activation_reason / matched_trigger / outcome / tokens_consumed
  - `class SkillSafetyScan(Base)`：findings JSONB / status / scanner_version
- `app/modules/skills/schemas.py`
  - `SkillCreate / SkillUpdate / SkillResponse`（含 OpenClaw 字段映射）
  - `SkillVersionResponse / SkillUsageLogResponse / SkillSafetyScanResponse`
  - `SkillImportPreview`（解析 ZIP 后给前端预览的 dataclass）
  - `MatchTriggerRequest / MatchTriggerResponse`（debug 用）
- `alembic/versions/<rev>_add_skills_tables.py`
  - 4 张表 + 索引（按 §4 SQL 建）
  - 不修改 prompt_templates 表
- `app/models/__init__.py` 注册新 ORM
- 更新 `app/main.py` 启动钩子（Phase 13 才用，先留 import 预留）

**验证方式**：

- `./run.sh db-migrate "add skills tables"` 生成迁移文件
- `./run.sh db-upgrade` 成功
- 数据库里出现 4 张新表，索引正确
- 启动 backend，新表对应的 ORM 可正常 query
- pytest `tests/skills/test_models.py` 验证字段约束（slug 唯一、activation_mode CHECK 约束）

**会话启动模板**：

```
请执行 Task 12.1 - Skill 数据模型 + 迁移。
项目：/Users/wxh/Downloads/长轻Job/AITestPlatform
目标：建独立 skills 模块（与 prompt_templates 完全分离）
关键复用：app/models/base.Base 基类、UUID / JSONB / TIMESTAMPTZ 字段约定（参考 prompt_templates 表）
新文件：app/modules/skills/{__init__,models,schemas}.py + alembic/versions/<rev>_add_skills_tables.py
注意：不动 prompt_templates；slug 在 (project_id, slug) 唯一；system_* 命名空间锁定在 service 层做
```

---

### Task 12.2 — SkillRouter 三层激活 + skill_invoke Lazy Tool + chat 集成 🟠 L

**目标**：实现核心激活机制，把候选 skill 包装为 OpenAI tool 让 LLM 自主调用（lazy load 全文），与一期 chat_service 主流程无缝集成。

**前置依赖**：Task 12.1 完成

**产出文件**：

- `app/modules/skills/skill_router.py`（消息路由层，不是 HTTP router）
  - `dataclass SkillContext`：组装结果，**空对象时下游必须字节级等价于三期前**
    - `system_messages: list[dict]` — Layer 1 + Layer 2 拼好的 system message
    - `candidate_tools: list[dict]` — Layer 3 + 当 system_* skill 被召回时同步打包对应 platform_*
    - `active_system_skill_slugs: set[str]` — 本次会话激活的 system_* skill slug 集（**safe_run_tool 用此校验 platform_* 调用方权限**）
    - `skill_id_by_tool_name: dict[str, uuid.UUID]` — `skill_<slug>__invoke` 反查 skill_id，写 usage log 用
  - `async def compose(db, project_id, session, user_message) -> SkillContext`
    - Layer 1：always skill 拼 system message；slug 为 system_* 的加入 active_system_skill_slugs
    - Layer 2：手动选中 skill 拼 system message；同上更新 active_skills
    - Layer 3：触发词召回 + agent_callable 候选 → 包装为 OpenAI tool
      - **关键修正**：当候选 skill slug 以 `system_` 开头时，把它 `tools_required` 中的 platform_* tool 同步加入 candidate_tools（去重），并把 slug 加入 active_system_skill_slugs
      - 自定义 skill 即使在 SKILL.md 写了 `tools_required: [platform_run_ui_execution]`，candidate_tools **绝不**包含 platform_* — 这是三期权限第一道闸
  - `def _build_skill_invoke_tool(skill: Skill) -> dict`：skill → OpenAI function spec
    - tool name 用 `skill_<slug>__invoke`（`__` 满足一期 unregister_namespace 约定）
  - `async def execute_skill_invoke(db, skill_slug: str, args_json: str, *, ctx) -> str`
    - lazy load Skill.body → wrap_with_safety → 返回
    - 写一条 SkillUsageLog（activation_reason = `agent_callable`，outcome 默认 success）
    - 不在此处校验 platform_*（platform_* 是 LLM 在下一轮的独立 tool_call，由 safe_run_tool 校验）
- `app/modules/skills/safe_invoke.py`（三期权限第二道闸）
  - `async def safe_run_tool(name, args_json, *, active_system_slugs: set[str], skill_id_by_tool: dict) -> str`
    - 若 `name.startswith("platform_")`：
      - active_system_slugs 为空 → 拒绝（`{"error": "..."}`，不抛异常以保持流式不中断）
      - active_system_slugs 中所有 skill 的 tools_required 联合集不包含 name → 拒绝
      - 通过 → 调 `agent_tools.run_tool(name, args_json)`
    - 若 `name.startswith("skill_") and name.endswith("__invoke")`：拆 slug → 走 `execute_skill_invoke`
    - 其它（`web_search` / 二期 `<exec_id>__browser_*`）：直接 `agent_tools.run_tool(name, args_json)`，零变化
- `app/modules/skills/triggers.py`
  - `async def match_triggers(db, project_id, message: str, max: int = 3) -> list[Skill]`
  - 算法：子串大小写不敏感 + 长度加权打分（命中 trigger 长度 = 分数），按分倒序取前 max
- `app/modules/skills/safety.py`
  - `SAFETY_WRAPPER` 常量 + `wrap_with_safety(content)` + `extract_when_to_use(body)`
- `app/modules/llm/chat_service.py` 修改（**最小侵入清单**）
  - `_handle_chat_stream` 主循环开始前增量 1 行：`skill_ctx = await skill_router.compose(db, session.project_id, session, user_content)`
  - `openai_messages = _build_context(...)` 后追加：`openai_messages = openai_messages[:1] + skill_ctx.system_messages + openai_messages[1:]`（base prompt 之后、history 之前）
  - `tools=TOOLS` → `tools=TOOLS + skill_ctx.candidate_tools`
  - `result_json = await run_tool(name, args_raw)` → `result_json = await safe_run_tool(name, args_raw, active_system_slugs=skill_ctx.active_system_skill_slugs, skill_id_by_tool=skill_ctx.skill_id_by_tool_name)`
  - **不动**：`_handle_review_intent / _handle_generate_intent / detect_intent / send_message_stream` 上层意图分发；前端 SSE 事件名与字段格式
- `tests/skills/test_skill_router.py`
  - 触发词命中 → candidate_tools 含 `skill_<slug>__invoke`
  - system_* skill 被召回 → 其 tools_required 中的 platform_* 同步进 candidate_tools
  - 自定义 skill 在 tools_required 写 platform_* → candidate_tools 不含 platform_*（第一道闸）
  - always 模式 → 进 system_messages；slug=system_* 时进 active_system_skill_slugs
  - lazy load：execute_skill_invoke 调用前 SKILL.md 全文未在 messages 中
- `tests/skills/test_safe_invoke.py`
  - 自定义 skill 触发的对话中模型硬塞 platform_* 调用 → safe_run_tool 拒绝
  - 无任何 system_* 激活时调 platform_* → 拒绝
  - 普通 web_search 调用不受影响
- `tests/skills/test_zero_intrusion.py`（**强制基线**，本次新增）
  - 创建无 skill 的项目，mock SkillRouter.compose 返回空 SkillContext
  - 断言 `_handle_chat_stream` 输出的 `openai_messages` 与三期前 `_build_context` 直接输出字节级相等
  - 断言传给 `stream_chat` 的 `tools` is `TOOLS`（不是 `TOOLS + []`，要原对象引用）
  - 用一期已有 e2e fixture 跑全套通用对话场景，SSE 流字节对比
- `tests/skills/test_triggers.py`
  - 多 trigger 命中时按分排序；max=3 生效

**关键设计点**：

- **零侵入契约**：SkillContext 为空对象时，`_handle_chat_stream` 主循环行为字节级等价于三期前。chat 模块所有改动都在 `if skill_ctx.<field>:` 分支内，绝不改写既有变量
- **意图快通道优先**：`send_message_stream` 第 567/577 行的 REVIEW / GENERATE_TESTCASES 分支不动，SkillRouter 永远不会与一期评审 / 生成意图快通道双触发
- **platform_\* 权限两道闸**：(1) SkillRouter.compose 仅对 system_* skill 打包 platform_* 进 candidate_tools；(2) safe_run_tool 第二道防护防止模型幻觉调用未声明的 platform_*
- **与一期 agent_tools 不冲突**：skill_<slug>__invoke 只存在 candidate_tools 数组中（每次对话动态生成），**不**写入 TOOL_REGISTRY；TOOL_REGISTRY 仅有 web_search + 二期临时 browser_* + 三期 platform_*
- **前端事件零变化**：reasoning / delta / tool_call SSE 事件名与字段格式与三期前完全相同，前端 chat 渲染代码 0 改动

**验证方式**：

- 创建 skill `triggers=["UI 测试"], activation_mode="agent_callable"` + slug=`system_ui_automation` → 发"跑一下 UI 测试" → candidate_tools 同时含 `skill_system_ui_automation__invoke` 和 `platform_run_ui_execution` → 模型 lazy load → 调 platform_run_ui_execution → 二期 ExecutionEngine 启动
- `activation_mode=always` 的 skill 每次对话都进 system_messages
- `activation_mode=manual` 仅当 `session.context.manual_skill_ids` 含其 ID 时注入
- 多个候选 skill 同时存在，模型只 lazy load 实际选中的（用 SkillUsageLog 验证 token_consumed）
- `_handle_review_intent / _handle_generate_intent` 老路径**未受影响**（用一期已有测试覆盖）
- 普通问答（"今天上海天气"）`tools` 数组与三期前字节级相等

**会话启动模板**：

```
请执行 Task 12.2 - SkillRouter 三层激活 + skill_invoke Lazy Tool + chat 集成。
依赖：Task 12.1 完成。
目标：实现 skill 三层激活，把候选 skill 包装成 OpenAI tool 让 LLM 自主 lazy load 调用
关键复用：app/modules/llm/agent_tools.TOOL_REGISTRY（不变）+ chat_service.stream_chat / run_tool 循环
新文件：app/modules/skills/{skill_router,triggers,safety}.py + tests
修改：chat_service._build_context / _handle_chat_stream（最小侵入；前端事件不变）
注意：候选 skill_<slug>__invoke 不进 TOOL_REGISTRY，仅在每次对话动态生成
```

---

### Task 12.3 — SKILL.md 解析 + ZIP 导入/导出 + SafetyScanner + OpenClaw 兼容验证 🟠 L

**目标**：完整的导入导出能力 + 安全扫描 + OpenClaw 真机兼容验证。

**前置依赖**：Task 12.1 完成

**产出文件**：

- `app/modules/skills/parser.py`
  - `def parse_skill_md(content: str) -> ParsedSkill`（用 `python-frontmatter`）
  - 校验 name / description 必填
  - slug 缺失时根据 name 自动 slugify
  - YAML 前言全部进 metadata，识别字段提到顶层
- `app/modules/skills/safety_scanner.py`
  - `dataclass Finding(type, severity, snippet, line)`
  - `dataclass ScanResult(status, findings)`
  - `class SafetyScanner`
    - `def scan(body: str, metadata: dict) -> ScanResult`
    - 规则集（v3.0 内置；可演进为 yaml 配置）：
      - high: prompt injection 关键句正则（ignore previous instructions / system prompt 等）+ 敏感字符串（/etc/passwd / postgres:// / api_key）
      - medium: tools_required 含未注册 platform_*；body > 50 KB
      - low: 可疑标点 / 编码异常等
    - status 决策：发现 high → blocked；medium → warning；其它 → clean
    - 写入 `skill_safety_scans`
- `app/modules/skills/importer.py`
  - `async def import_zip(db, project_id, file: UploadFile, user) -> SkillImportPreview`
    - zipfile 解包到临时目录（限制：< 5 MB；附件 ≤ 50 个；附件单个 ≤ 1 MB）
    - 找 SKILL.md → parse → SafetyScan → 创建 Skill + 写附件到 `uploads/skills/<project_id>/<skill_id>/<version>/`
    - status=blocked → 不创建，返回 findings
    - status=warning → 创建但 is_enabled=false
    - status=clean → 创建且 is_enabled=true
  - `async def import_url(db, project_id, url: str, ref: str | None) -> Skill`
    - 支持 git+https / 普通 HTTPS ZIP 链接
    - 用 git clone 拉到临时目录（容器需有 git，已自带）
- `app/modules/skills/exporter.py`
  - `def export_skill_as_zip(skill: Skill) -> bytes`
    - 按 §9.3 输出 SKILL.md（YAML + Markdown）
    - description 末尾自动追加 `\n\nUse when: <triggers 拼接>`
    - 透传 metadata 完整字段（含未识别字段）
    - 自动生成 README.md
    - 附件按原路径
- `tests/skills/test_parser.py`
  - 标准 SKILL.md 解析所有字段
  - 缺 name → 报错
  - 未识别字段进 metadata
- `tests/skills/test_safety_scanner.py`
  - "ignore previous instructions" → blocked
  - 长度 > 50 KB → warning
  - 干净内容 → clean
- `tests/skills/test_importer_exporter.py`
  - 导入 ZIP → 解析正确 → 文件落地 `uploads/skills/...`
  - 导入 → 编辑 → 导出 → 重新解析后字段等价（除 description 末尾的 Use when 行）
- `tests/skills/fixtures/openclaw_samples/` — 5 个 OpenClaw 标准 skill 样本（取自用户 `~/.agents/skills/` 中的真实 skill 包结构作为参考）
- `tests/skills/test_openclaw_compat.py`
  - 5 个样本 100% 解析成功
  - 每个内置 system_* skill 导出后用 `python-frontmatter` 重读 → 等价
- 命令行验证工具：`./run.sh validate-skill <path-to-zip>`
  - 在 backend/ 加 `scripts/validate_skill.py`，run.sh 加对应命令

**关键设计点**：

- 安全扫描规则集采用**只读模式**（不改 SKILL.md 内容，仅打标）
- 内置 slug 锁定：service 层创建时若 slug 以 `system_` 开头且调用方非 init_data → 403
- 附件存储路径包含 `<version>` 子目录，回滚版本时各版本附件独立

**验证方式**：

- 上传一个标准 ZIP → 自动解析所有字段 → skill 创建 → 列表可见
- 上传含 prompt injection 的 SKILL.md → 标记 blocked → 拒绝创建
- 上传含敏感字符串的 → status=warning → 创建但 disabled
- 5 个 OpenClaw 真实样本 100% 解析通过
- 任一 skill 导出 → `cat SKILL.md` 严格符合 OpenClaw 格式
- `./run.sh validate-skill /tmp/foo.zip` → 输出 OK / FAIL 报告
- 试图创建 slug=system_xxx → 403（除 init_data 调用）

**会话启动模板**：

```
请执行 Task 12.3 - SKILL.md 解析 + ZIP 导入导出 + SafetyScanner + OpenClaw 兼容。
依赖：Task 12.1 完成。
目标：完整导入导出 + 安全扫描 + 真实 OpenClaw 样本兼容验证
新文件：app/modules/skills/{parser,safety_scanner,importer,exporter}.py + tests + scripts/validate_skill.py
依赖包：python-frontmatter（./run.sh add-backend python-frontmatter）
关键约束：附件存储 uploads/skills/<project_id>/<skill_id>/<version>/；ZIP 大小 < 5 MB
```

---

### Task 12.4 — Skill CRUD API + 权限 + 内置 system_* 同步 + platform_* tool 桥接 🟠 L

**目标**：完整的 HTTP CRUD API + 权限控制 + 内置 4 个 system_* skill 同步 + 桥接到一二期能力的 platform_* tool。

**前置依赖**：Task 12.2 + 12.3 完成

**产出文件**：

- `app/modules/skills/service.py`
  - `class SkillService`
  - `async def list(...)`、`get(id)`、`get_by_slug(...)`、`create / update / delete`、`toggle`、`list_active(project_id, activation_mode)`
  - `update` 内部：自动 +db_version、写 SkillVersion、重新 SafetyScan
  - `delete`：source=built_in 拒绝
  - `create / update`：slug 含 `system_` 前缀且调用方非 init_data → 403
- `app/modules/skills/usage_service.py`
  - `async def log(skill_id, activation_reason, message_id, ...)`
  - `async def aggregate_stats(project_id, days=7) -> dict[skill_id, {count, success_rate}]`
- `app/modules/skills/router.py`（HTTP）
  - 列表 / 详情 / 版本历史
  - 创建 / 编辑 / 删除 / toggle
  - import-zip / import-url / export
  - safety-scan（重新扫描）
  - active（当前会话激活）
  - match-triggers（debug）
  - usage-stats
  - 权限装饰器：`skill:view / edit / delete / import / export / scan`
- `app/modules/skills/built_in.py`
  - `SYSTEM_SKILLS_VERSION = "1.0"` 常量（提升时全量重写）
  - **MVP 真正生效的内置 skill = 1 个**（详见 `PHASE3_DESIGN.md` §8.2 修正说明）：
    - `SYSTEM_UI_AUTOMATION`：agent_callable 模式，slug=`system_ui_automation`，trigger=`["跑 UI 测试", "跑用例", "自动化测试", "执行 UI 用例"]`，tools_required=`[platform_run_ui_execution, platform_search_testcases, platform_list_environments]`
    - 真正生效原因：二期已下线 `IntentType.RUN_UI_TEST`，无意图快通道拦截，三期 SkillRouter 能正常召回
  - **展示 / 兼容用但不生效的内置 skill = 2 个**（trigger 字段标 deprecated_path=true）：
    - `SYSTEM_REQUIREMENT_REVIEW`：slug=`system_requirement_review`；body 中标注"实际由一期 review_service 提供，本 skill 仅为 ClawHub 导出兼容样本与平台能力清单展示"；不声明 platform tool；activation_mode=`manual`（避免 trigger 误命中）
    - `SYSTEM_TESTCASE_GENERATION`：slug=`system_testcase_generation`；同上结构；activation_mode=`manual`
  - `async def sync_built_in_skills(db, project_id)`：项目创建后调用，缺失则补齐；SYSTEM_SKILLS_VERSION 提升时全量重写
- `app/modules/skills/platform_tools.py`（桥接到二期能力 — MVP 仅 3 个）
  - `async def platform_run_ui_execution(args, *, session_ctx) -> str` → 调二期 `execution_engine.run` 启动 UI 自动化任务，返回 execution_id 供后续追踪
  - `async def platform_search_testcases(args) -> list` → 调一期 testcase_service.search，按用户意图过滤候选用例
  - `async def platform_list_environments(args) -> list` → 调二期 ui_env service，列出可用环境
  - **不实现** `platform_review_document` / `platform_generate_testcases` — 因为对应的内置 skill 在 v3.0 是 deprecated_path（一期意图快通道已覆盖；v3.1 下线 intent_handler 时再补）
  - 注册逻辑：在 main.py on_startup 把以上 3 个函数注册进 `agent_tools.TOOL_REGISTRY`
  - **安全两道闸**（详见 Task 12.2 的 `safe_invoke.py`）：
    1. SkillRouter.compose 仅对 system_* skill 打包 platform_* 进 candidate_tools，自定义 skill 看不到
    2. safe_run_tool 校验 active_system_skill_slugs + tools_required 联合集，防止模型幻觉调用未声明的 platform_*
- `app/modules/projects/service.py` 钩子：`create_project` 后调 `sync_built_in_skills`
- `app/main.py`：
  - on_startup 注册所有 platform_* tool 到 TOOL_REGISTRY
  - on_startup 扫描所有项目，缺失内置 skill 时补齐
- `app/modules/auth/permissions.py`：新增 7 个 skill:* 权限，分配给 admin / project_manager / tester / viewer
- `tests/skills/test_service.py`
  - CRUD 全覆盖
  - delete system_* 拒绝
  - create slug=system_ 非内置调用方拒绝
  - update 自动 +db_version + 写 SkillVersion + SafetyScan
- `tests/skills/test_router.py`
  - 各端点 happy path + 401 / 403
  - import / export 文件流
- `tests/skills/test_built_in.py`
  - 新建项目自动注入 4 个内置
  - SYSTEM_SKILLS_VERSION 提升 → 重写
- `tests/skills/test_platform_tools.py`
  - mock 一期 / 二期 service
  - 自定义 skill 调用 platform_* → 被拒
  - system_* skill 调用 platform_* → 通过

**关键设计点**：

- 内置 skill 不可被删，可被编辑（编辑后 `safety_scan_status` 重新扫描；保留 SkillVersion 历史以便恢复）
- 推荐用户"复制为自定义"再改，不直接编辑 system_*（前端按钮文案明示）
- platform_* tool 是**全局注册**进 TOOL_REGISTRY 的（与 web_search 同级），但只有 system_* skill 的 SKILL.md 中引用它们才有效

**验证方式**：

- 新建项目 → 自动包含 3 个内置 skill：1 个生效 `system_ui_automation` + 2 个 deprecated_path 展示用
- **AI 评审需求** 回归：在 chat 中说"评审需求文档"→ 走一期 `_handle_review_intent` 老快通道 → 评审输出与三期前完全一致 → 断言 `skill_usage_logs` 表无新记录
- **AI 生成用例** 回归：同上
- **AI 驱动 UI 自动化（chat 通路）**：chat 中说"跑一下登录用例" → SkillRouter 召回 `system_ui_automation` → candidate_tools 同时含 `skill_system_ui_automation__invoke + platform_run_ui_execution + platform_search_testcases + platform_list_environments` → 模型 lazy load → 多轮调 platform_* → 二期 ExecutionEngine 启动 → 实时进度通过 chat SSE 回报
- **AI 驱动 UI 自动化（前端入口）回归**：用例管理 → 选用例 → 执行 UI 自动化 → ExecutionEngine 启动 → 报告生成全流程与三期前完全一致（绕过 chat，不经 SkillRouter）
- 用户编辑 system_* skill → 保存成功 + db_version+1 + SkillVersion 表多一条
- 用户尝试删除 system_* skill → 403
- 自定义 skill SKILL.md 中声明 `tools_required: [platform_run_ui_execution]` + 用户触发该 skill → candidate_tools **不**含 platform_run_ui_execution（第一道闸）；模型即使硬塞调用也被 safe_run_tool 拒绝（第二道闸）
- 兼容性兜底：删除 3 个内置 skill 后，一期 review / generate 主流程**仍可工作**（因为 intent_handler 老路径保留）；前端"用例管理 → 执行 UI 自动化"主入口**仍可工作**（不依赖 skill 模块）

**会话启动模板**：

```
请执行 Task 12.4 - Skill CRUD API + 权限 + 内置同步 + platform_* tool 桥接。
依赖：Task 12.2 + 12.3 完成。
目标：完整 HTTP API + 4 个内置 system_* skill + 桥接到一二期能力
新文件：app/modules/skills/{service,usage_service,router,built_in,platform_tools}.py + tests
修改：app/main.py（on_startup 注册 platform_* + 同步内置 skill）；
      app/modules/auth/permissions.py（新增 7 个 skill:* 权限）；
      app/modules/projects/service.py（create_project 后调 sync_built_in_skills）
注意：platform_* tool 全局注册到 TOOL_REGISTRY，但 run_tool 包装层校验调用方 skill 是否 system_*
注意：不动一期 intent_handler；老 REVIEW / GENERATE_TESTCASES 快通道保留
```

---

### Task 12.5 — 前端 Skill 管理页（列表 / 编辑器 / 导入对话框 / 详情抽屉）🟠 L

**目标**：系统管理新增"技能包管理"子菜单，完整的前端管理界面。

**前置依赖**：Task 12.4 完成（API 可用）

**产出文件**：

- `src/views/settings/SkillManagement.vue` — 列表页
  - 顶部：[ 上传 ZIP ] [ URL 导入 ] [ 新建 ] 按钮
  - 左侧：分类筛选（动态从 categories 接口）
  - 中间：activation_mode 切换（全部 / agent_callable / trigger / always / manual）
  - 右侧表格：状态徽章 + 名称 + 分类 + 触发词 + 近 7 天使用次数 + 操作
  - 默认按使用次数倒序
  - 行点击 → 详情抽屉
- `src/views/settings/SkillEditor.vue` — 编辑器（独立路由 `/settings/skills/:id/edit`）
  - 双栏布局：左侧元数据表单 + 右侧 Markdown 编辑器
  - 元数据：name / slug / description / version / category / tags / triggers / activation_mode / tools_required / is_enabled
  - Markdown 编辑器：用 NaiveUI `n-input` type="textarea" + `markdown-it` 实时预览
  - 底部：完整 SKILL.md 预览（YAML + Markdown 拼接）
  - 附件管理（仅 source=imported / custom）：列出现有附件，支持上传 / 删除
  - 保存 → PATCH `/api/skills/:id` → db_version 自动 +1
  - 推荐用户"复制为自定义"按钮（针对 system_* skill）
- `src/components/skills/SkillImportDialog.vue` — 导入对话框
  - Tab 1: ZIP 拖拽上传
  - Tab 2: URL 导入（输入 git+https / HTTPS ZIP 链接）
  - Tab 3: 从模板创建（选 4 个内置示例之一作起点）
  - 解析预览：显示 name / slug / triggers / 附件数 / 安全扫描结果
  - 安全扫描状态徽章（绿 / 黄 / 红）+ findings 列表
  - 确认导入按钮（status=blocked 时禁用）
- `src/components/skills/SkillDetailDrawer.vue` — 详情抽屉
  - 元数据展示
  - SKILL.md 渲染（用 markdown-it）
  - 版本历史（点击 version 切换显示）
  - 使用统计（近 7 天 / 30 天调用次数 + 失败率）
  - 操作：编辑 / 启用禁用 / 重新扫描 / 导出 / 删除（system_* 隐藏删除）
- `src/components/skills/SafetyBadge.vue` — 安全扫描徽章组件（绿 clean / 黄 warning / 灰 unscanned / 红 blocked）
- `src/components/skills/TriggerEditor.vue` — 触发词数组编辑器（输入框 + Tag 列表 + 删除）
- `src/services/skills.ts` — API service（封装所有端点）
- `src/router/index.ts` 新增路由：
  - `path: "settings/skills"` → `SkillManagement.vue` (permission: `skill:view`)
  - `path: "settings/skills/:id/edit"` → `SkillEditor.vue` (permission: `skill:edit`)
- `src/layouts/MainLayout.vue` — 系统管理子菜单中插入"技能包管理"项（位于"提示词管理"之后）
- `src/constants/permissions.ts` 增加 7 个 skill:* 权限常量

**关键交互细节**：

- 列表页"使用 ↓"列点击表头切换排序方向
- 编辑器底部预览区**双向同步**：在 YAML 区改 description，左侧表单同步；反之亦然
- 触发词编辑：每个 trigger 是一个 NaiveUI Tag，回车新增；点击 Tag 删除
- 导入对话框安全扫描结果**实时显示**（不要等用户点确认才看到）
- 删除按钮二次确认（"此操作不可逆"）
- system_* skill 编辑页顶部黄条警告：":warning: 这是系统内置技能，建议先复制为自定义版本再修改"

**验证方式**：

- 进入 `/settings/skills` 列表页 → 看到 4 个内置 skill + 任何已导入的自定义
- 上传一个标准 ZIP → 解析预览正确 → 确认导入 → 列表新增一行
- 上传含 prompt injection 的 ZIP → 安全扫描 blocked → 导入按钮禁用
- 点击行 → 详情抽屉打开，SKILL.md 渲染正确，使用统计正确
- 编辑 skill → 改名 / 改 trigger / 改 body → 保存 → db_version+1
- 启用 / 禁用 toggle → 列表状态变更 → 对话中对应 skill 激活 / 静默
- 导出按钮 → 下载 ZIP，解开后 SKILL.md 格式标准
- system_* skill 删除按钮不可见
- 触发词编辑器交互流畅

**会话启动模板**：

```
请执行 Task 12.5 - 前端 Skill 管理页。
依赖：Task 12.4 完成（API 可用）。
目标：系统管理新增"技能包管理"子菜单（与 LLM 配置 / 提示词管理同级）
新文件：src/views/settings/{SkillManagement,SkillEditor}.vue
       src/components/skills/{SkillImportDialog,SkillDetailDrawer,SafetyBadge,TriggerEditor}.vue
       src/services/skills.ts
修改：src/router/index.ts（加 settings/skills 路由）；
      src/layouts/MainLayout.vue（侧边栏加"技能包管理"项）；
      src/constants/permissions.ts（加 7 个 skill:* 权限）
关键复用：NaiveUI 表格 / 抽屉 / 表单组件；markdown-it 渲染 SKILL.md
风格：参考已有 PromptManagement.vue 的代码风格
```

---

### Task 12.6 — Chat header Skill 选择器 + 自动激活提示 + 消息内徽章 + 使用统计页 🟡 M

**目标**：完整的对话集成体验，让用户清楚知道"AI 用了哪个 skill / 为什么用 / 效果如何"。

**前置依赖**：Task 12.4 + 12.5 完成

**产出文件**：

- `src/components/chat/ChatHeader.vue` — 升级（已有 prompt 选择器 + 联网搜索开关）
  - 增加 Skill 多选器（NaiveUI dropdown）
  - 列出当前项目所有 is_enabled=true 的 skill
  - 选中的 skill ID 持久化到 `session.context.manual_skill_ids`
  - 顶部按钮显示已选数量"已选 N 个技能"
  - 下拉底部"⚙ 管理技能包"链接 → 跳 `/settings/skills`
- `src/composables/useSkillSelection.ts`
  - 管理 manual_skill_ids 列表（响应式）
  - 调用 `GET /api/projects/:id/skills?is_enabled=true` 拉可选列表
  - 切换时同步到 session.context（PATCH `/api/chat/sessions/:id/context`）
- `src/components/chat/SkillActivationHint.vue`
  - 当 SSE 事件流中收到 `skill_activated` 事件 → 在消息区顶部显示 toast banner
  - "已自动激活：UI 自动化（trigger 命中：跑用例）"
  - 5 秒后自动消失，可点击 X 关闭
- `src/components/chat/SkillUsageBadge.vue`
  - 在 AI 消息气泡顶部显示徽章（如果该消息触发了 skill）
  - "🎯 UI 自动化（Agent 调用）" 或 "🎯 需求评审（trigger: 评审需求）"
  - 点击展开：显示 SKILL.md 全文（NaiveUI Modal）
- 后端 SSE 增量：
  - `app/modules/llm/chat_service.py` — 新增 SSE 事件类型：
    - `skill_activated` payload: `{skill_id, slug, name, activation_reason, matched_trigger?}`
    - 在 `compose_skill_context` 完成后立即推送
  - `app/modules/llm/sse.py`（或 chat_router）增加事件类型常量
- `src/composables/useSSE.ts` 处理 `skill_activated` 事件 → 触发 SkillActivationHint
- `app/modules/chat/models.py` — `ChatMessage` 表增加 `skill_invocation_id` 字段（FK 到 SkillUsageLog，nullable）
  - 迁移文件
  - 模型调用 skill_invoke tool 后写入此字段
  - 前端 GET 消息时带回此字段，触发 SkillUsageBadge
- `src/views/settings/SkillUsageStats.vue` — 使用统计独立页（路由 `/settings/skills/stats`）
  - 表格：skill 名 / 7 天调用 / 30 天调用 / 成功率 / 平均 tokens
  - 趋势图：按 skill 选择，显示日调用量曲线（7 / 30 天）
  - 失败明细抽屉：点击失败率数字弹出最近失败记录列表（含 error_message）
- `src/services/skills.ts` 增加 `fetchUsageStats(projectId, days)` 方法

**关键交互细节**：

- Chat header skill 多选下拉：高亮已激活的，显示 trigger 数量徽标（如"3 个触发词"）
- 自动激活 hint banner 区分"trigger 命中"vs "Agent 主动调用"两种 reason
- AI 消息气泡内的 badge 颜色与 reason 对应：
  - 蓝色 = Agent 主动调用
  - 绿色 = trigger 命中
  - 紫色 = 用户手动选中
- 使用统计页"成功率"低于 50% 的行显示红色背景，提示用户优化 trigger
- 失败明细抽屉中每条记录可"跳转到对应消息"（如果消息还存在）

**验证方式**：

- 在 chat header 多选 2 个 skill → 发消息 → 两个 skill 都注入 system message
- 消息中含"评审需求" → 顶部 banner 显示"已自动激活：需求评审（trigger 命中: 评审需求）"
- 模型主动调用某 skill → AI 消息气泡顶部出现徽章 → 点击展开 SKILL.md
- `session.context.manual_skill_ids` 持久化到 DB → 刷新页面后选中状态保留
- 进入 `/settings/skills/stats` → 看到所有 skill 的 7 / 30 天统计 → 点失败率数字弹失败明细
- 模拟某 skill 失败率 60% → 行变红色

**会话启动模板**：

```
请执行 Task 12.6 - Chat header Skill 选择器 + 激活提示 + 使用统计页。
依赖：Task 12.4 + 12.5 完成。
目标：用户在 chat 中能：(1) 手动选 skill；(2) 看到自动激活原因；(3) 看到 AI 用了哪个 skill；(4) 在统计页看效果
新文件：src/components/chat/{SkillActivationHint,SkillUsageBadge}.vue
       src/composables/useSkillSelection.ts
       src/views/settings/SkillUsageStats.vue
修改：src/components/chat/ChatHeader.vue（加 Skill 多选器）
      src/composables/useSSE.ts（处理 skill_activated 事件）
      app/modules/llm/chat_service.py（推送 skill_activated SSE）
      app/modules/chat/models.py + 迁移（ChatMessage 加 skill_invocation_id 字段）
关键复用：现有 ChatHeader prompt 选择器代码风格；SSE 事件处理同一期 reasoning/delta 模式
```

---

## 执行节奏

接续二期编号（二期结束于 Task 11.4，对话 37）：

```
对话 38: Task 12.1（Skill 数据模型 + 迁移 + schemas）           [M]
对话 39: Task 12.2（SkillRouter 三层激活 + Lazy Tool + chat 集成）[L]
对话 40: Task 12.3（解析 + ZIP 导入导出 + SafetyScanner + OpenClaw 兼容）[L]
对话 41: Task 12.4（CRUD API + 权限 + 内置同步 + platform_* 桥接）[L]
对话 42: Task 12.5（前端管理页 + 编辑器 + 导入对话框 + 详情抽屉） [L]
对话 43: Task 12.6（Chat 集成 + 激活提示 + 使用统计页）          [M]
```

**总计：6 次对话，约 6-9 天（单人开发）**

---

## 三期完成后的整体能力图

```
┌────────────────────────────────────────────────────────────────────┐
│                       AITestPlatform                                 │
├──────────────┬──────────────┬──────────────┬───────────────────────┤
│    一期       │    二期       │    三期       │   未来                  │
├──────────────┼──────────────┼──────────────┼───────────────────────┤
│ • 用户/权限   │ • UI 自动化   │ • Skill 管理 │ • CI 集成             │
│ • 项目管理    │   环境配置    │ • 三层激活    │ • 缺陷跟踪            │
│ • LLM 配置   │   AI 执行     │ • Lazy Tool  │ • 知识库 RAG          │
│ • Agent 对话 │   结果/截图   │ • ZIP 导入    │ • 数据看板            │
│   (tool 使用)│   执行统计    │ • OpenClaw   │ • 团队协作            │
│ • 联网搜索    │   调试模式    │   格式兼容   │ • Skill 评分          │
│ • 提示词模板  │   重放        │ • 内置 Skill │ • Embedding 召回      │
│ • 需求上传    │   Token 治理  │ • 安全扫描    │                       │
│ • AI 评审    │   Live View  │ • 防御 prompt│                       │
│ • 用例管理    │              │ • 使用统计    │                       │
│ • AI 生成    │              │              │                       │
└──────────────┴──────────────┴──────────────┴───────────────────────┘

对话 Context 组装（v3.0 三层激活）:
[基础 Prompt(一期)] + [always Skill(三期)] + [手动激活 Skill(三期)] + [历史] + [用户输入]
                                ↓
            候选 Skill 包装为 OpenAI tools（lazy load，仅 description）
                                ↓
                        LLM 自主决策
                                ↓
   [普通回复] / [调 web_search] / [调 skill_<slug>__invoke → lazy load 全文 → 按 SKILL.md 执行]
                                ↓
   [调用 platform_review_document / platform_generate_testcases / platform_run_ui_execution] → 实际执行业务
                                ↓
                     结果回传 → 渲染 ActionCard
```

---

## 新对话启动模板

```
请执行 Task 12.X - [任务名称]。
项目路径：/Users/wxh/Downloads/长轻Job/AITestPlatform
当前进度：一期+二期已完成，三期执行到 Task 12.X
关键复用：一期 agent_tools.TOOL_REGISTRY、PromptTemplate（不动）、二期 ExecutionEngine
新增模块：app/modules/skills/（独立模块；与 prompt_templates 完全分离）
注意：不动一期 intent_handler；老 REVIEW / GENERATE_TESTCASES 快通道保留作兜底
```

---

## v2.0 → v3.0 方案变更总结

| 维度 | v2.0 | v3.0 |
|------|------|------|
| 数据模型 | rename `prompt_templates` → `prompt_kits`，合并 prompt + skill | **独立 `skills` 表**，与 prompt_templates 各自独立 |
| 模块边界 | `app/modules/kits/`（接管 prompts） | `app/modules/skills/`（新增）+ `app/modules/prompts/`（不动）|
| Task 数量 | 7 | **6**（合并 OpenClaw 真机校验进 Task 12.3）|
| 内置 Skill 重构 | 单独 Task 12.6，重写 review/generate/ui-automation 主流程 | **作为 Task 12.4 一部分**：仅同步内置数据 + 桥接 platform_*；不动一二期主流程 |
| intent_handler 关系 | 全部废弃 | **保留**作为意图快通道；SkillRouter 兜底未命中场景 |
| 用户评分 | UsageCard 一期就做 | **延后到 v3.1**；MVP 只做调用次数 + 失败率 + 趋势图 |
| 关键词召回算法 | 子串 in 字符串 | 加权打分（命中 trigger 长度 = 分数），按分倒序 |
| 实现复杂度 | 高（rename 风险 + 迁移大手术） | 中（独立模块叠加，零侵入一二期）|

---

## 技术风险

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| skills 模块与 prompts 模块概念混淆 | 低 | 用户不知道用哪个 | 文档明确："prompt = 你是谁；skill = 你会做什么"；前端文案清晰区分 |
| skill 内容过长撑爆 context | **极低** | 对话失败 | Lazy Tool 化彻底解决；候选 skill 仅 200 token/个 |
| 触发词误匹配 | 中 | 不相关 skill 进候选池 | 候选最多 5 个 + Agent 自主拒绝 + 使用统计页帮用户优化 trigger |
| SKILL.md 格式不规范 | 低 | 解析失败 | python-frontmatter 宽松解析 + 必填字段缺失时清晰报错 + metadata 透传未知字段 |
| 安全扫描误报 | 中 | 合理 skill 被 block | warning 级允许人工启用；规则集 v3.1 改为可配 yaml |
| platform_* tool 被自定义 skill 滥用 | 低 | 数据安全 | run_tool 包装层校验调用方 skill slug 是否 system_* 命名空间 |
| 内置 skill 编辑后行为变化 | 中 | 平台核心能力波动 | 编辑后保留 SkillVersion 历史可恢复；前端推荐"复制为自定义"按钮 |
| OpenClaw 格式演进失配 | 低 | 导出不兼容新版 | metadata 字段透传 + 字段映射表维护 + Task 12.3 真机样本测试 |
| chat_service 修改影响一期意图快通道 | 低 | review/generate 失效 | Task 12.2 仅在主流程增量加 SkillContext，不动 intent_handler 分支；e2e 测试覆盖 |
| 多 skill 同时激活指令冲突 | 低 | AI 行为混乱 | Lazy Load 保证一次只 1 个 skill 在 context；防御 prompt 兜底 |

---

*文档版本：v3.0 — 独立 Skill 模块 + 三层激活 + Lazy Tool 化 + 零侵入一二期*
*最后更新：2026-05-06*
*主要变更：见文档头部 v3.0 关键调整列表与"v2.0 → v3.0 方案变更总结"章节*
