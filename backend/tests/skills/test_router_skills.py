"""Task 12.4 — skills router 路由注册检查（含 HTTP 方法断言）。"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.modules.skills.router import router


def _routes_index() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in router.routes:
        if not isinstance(r, APIRoute):
            continue
        out.setdefault(r.path, set()).update(r.methods or set())
    return out


def test_skills_router_mount_paths_exist() -> None:
    paths = {r.path for r in router.routes if isinstance(r, APIRoute)}
    expected = {
        "/api/projects/{project_id}/skills",
        "/api/projects/{project_id}/skills/import",
        "/api/projects/{project_id}/skills/import-url",
        "/api/projects/{project_id}/skills/active-for-chat",
        "/api/projects/{project_id}/skills/manual-for-chat",
        "/api/projects/{project_id}/skills/chat/activate-manual",
        "/api/projects/{project_id}/skills/match-triggers",
        "/api/projects/{project_id}/skills/usage-stats",
        "/api/skills/{skill_id}",
        "/api/skills/{skill_id}/versions",
        "/api/skills/{skill_id}/toggle",
        "/api/skills/{skill_id}/scan",
        "/api/skills/{skill_id}/export",
    }
    missing = expected - paths
    assert not missing, f"missing routes: {missing}"


def test_skills_router_methods_for_project_skills() -> None:
    """同一路径既要有 GET（列表）又要有 POST（创建）。"""
    idx = _routes_index()
    project_skills_methods = idx.get("/api/projects/{project_id}/skills", set())
    assert "GET" in project_skills_methods, (
        f"GET /api/projects/{{project_id}}/skills 未注册（仅有 {project_skills_methods}）"
    )
    assert "POST" in project_skills_methods


def test_skills_router_methods_for_single_skill() -> None:
    idx = _routes_index()
    single = idx.get("/api/skills/{skill_id}", set())
    assert {"GET", "PATCH", "DELETE"}.issubset(single), single
