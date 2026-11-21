"""审核台请求体导出（W13 T35，specs/review-console-types-t35 第三节第 1 条）：提交进前端目录的 review-api.requests.json 必须就是现在从
后端模型导出的结果。后端改了请求体模型而没有重新导出，这里失败；前端是否与导出文件一致由 apps/web/tests/review-requests.test.ts 核对。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import export_review_requests as X  # noqa: E402


def test_committed_export_matches_backend_models():
    assert X.OUT.exists(), f"缺少 {X.OUT}：运行 scripts/export_review_requests.py"
    assert X.OUT.read_text(encoding="utf-8") == X.render(X.export()), "review-api.requests.json 已过期：运行 scripts/export_review_requests.py"


def test_export_covers_every_strict_request_model():
    routes = X.export()["routes"]
    assert len(routes) >= 21
    sign = routes["POST /review/v0/content-versions/{cv}/sign"]
    assert sign["decision"] == {"required": True, "type": "string", "nullable": False, "enum": ["approved", "rejected"]}
    assert sign["basis_code"]["required"] is False and sign["basis_code"]["nullable"] is True
    node = routes["PATCH /review/v0/map-versions/{version_id}/nodes/{node_id}"]
    assert node["cognitive_levels"]["items"]["enum"] == ["apply", "know", "synthesize", "understand"]
    assert json.loads(X.render(X.export()))["_notice"].startswith("由 services/learning/scripts/export_review_requests.py")
