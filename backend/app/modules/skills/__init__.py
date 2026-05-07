"""Skill 技能包模块（三期 Phase 12 起新增）。

与一期 ``app.modules.prompts`` 完全分离的独立模块；本期内不修改一期 / 二期任何主流程。

子模块：
- ``models``：4 张表的 SQLAlchemy ORM（Skill / SkillVersion / SkillUsageLog / SkillSafetyScan）
- ``schemas``：Pydantic 请求 / 响应 / 内部 dataclass（如 ImportPreview）

已落地（Phase 12）：
- ``skill_router`` / ``safe_invoke`` / ``triggers`` / ``safety`` / ``platform_chat_tools``（Task 12.2）
- ``parser`` / ``safety_scanner`` / ``importer`` / ``exporter``（Task 12.3）

后续 task：``service`` / ``router`` / ``built_in`` 等。
"""
