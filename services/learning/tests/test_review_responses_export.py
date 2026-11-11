"""审核台响应结构导出（specs/review-console-responses 第二节第 3 条）：提交进前端目录的 review-api.responses.json 必须就是现在从
后端响应模型导出的结果。后端改了响应模型而没有重新导出，这里失败；前端是否与导出文件一致由 apps/web/tests/review-responses.test.ts 核对。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import export_review_responses as X  # noqa: E402
from review_console import responses as R  # noqa: E402


def test_committed_export_matches_backend_response_models():
    assert X.OUT.exists(), f"缺少 {X.OUT}：运行 scripts/export_review_responses.py"
    assert X.OUT.read_text(encoding="utf-8") == X.render(X.export()), "review-api.responses.json 已过期：运行 scripts/export_review_responses.py"


def test_export_shape():
    routes = X.export()["routes"]
    assert len(routes) == 42
    me = routes["GET /review/v0/auth/me"]["fields"]
    assert me["login_name"] == {"required": True, "type": "string", "nullable": True, "enum": None}
    diff = routes["GET /review/v0/map-versions/{version_id}/diff"]["fields"]
    change = diff["changes"]["items"]["fields"]
    assert change["op"]["enum"] == ["added", "changed", "deprecated", "reactivated", "removed"]
    assert change["rubric_version"] == {"required": False, "type": "string", "nullable": False, "enum": None}  # 有时不给，给了不为空
    assert change["old"]["type"] == "any"
    assert diff["summary"]["fields"]["counts"]["type"] == "map"
    pages = routes["GET /review/v0/pages"]["fields"]
    assert pages["configured"]["required"] is True and pages["merged"]["required"] is False


def test_every_response_model_is_strict():
    for route, model in R.ROUTES.items():
        assert model.model_config.get("extra") == "forbid", route


def test_loose_model_is_refused(monkeypatch):
    class Loose(BaseModel):
        model_config = ConfigDict(extra="ignore")
        a: str

    class Outer(R._M):
        inner: Loose

    with pytest.raises(SystemExit, match="不是严格模型"):
        X._node(Outer.model_json_schema(mode="validation"), Outer.model_json_schema(mode="validation")["$defs"], "x")
