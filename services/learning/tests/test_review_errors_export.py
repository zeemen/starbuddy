"""审核台错误码导出（specs/review-console-error-codes）：提交进前端目录的 review-api.errors.json 必须就是现在从后端源码与库错误映射导出的结果。
后端加了错误码而没有重新导出，这里失败；前端每个码是否都有提示由 apps/web/tests/review-errors.test.ts 核对。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import export_review_errors as X  # noqa: E402


def test_committed_export_matches_backend_codes():
    assert X.OUT.exists(), f"缺少 {X.OUT}：运行 scripts/export_review_errors.py"
    assert X.OUT.read_text(encoding="utf-8") == X.render(X.export()), "review-api.errors.json 已过期：运行 scripts/export_review_errors.py"


def test_export_has_both_sources():
    codes = X.export()["codes"]
    assert codes["DIGEST_CHANGED"] == [409] and codes["TOO_LARGE"] == [413]  # 代码里的 ReviewError
    assert codes["FORBIDDEN"] == [403] and codes["INVALID_REFERENCE"] == [422]  # 库错误映射 _PG_MAP
    assert codes["INTERNAL"] == [500]


def test_dynamic_code_outside_db_error_is_refused(tmp_path):
    (tmp_path / "x.py").write_text("def f(code):\n    raise ReviewError(409, code)\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="不是字面量"):
        X.scan(tmp_path)
    (tmp_path / "x.py").write_text('def f():\n    raise ReviewError(418, "TEAPOT")\n', encoding="utf-8")
    assert X.scan(tmp_path) == {"TEAPOT": {418}}
