"""共享夹具：合成地图快照、合成页面仓库、人设词表。全部是合成数据（规则 8）。"""
from __future__ import annotations

import copy
import datetime as dt
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from knowledge.atoms import Snapshot, parse_snapshot
from knowledge.lint import lint_dir
from knowledge.lint.issues import LintReport
from knowledge.lint.lexicon import Lexicon, default_lexicon

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
PAGES = FIXTURES / "pages"
MAP_V1 = FIXTURES / "map-v1.yaml"
ROOT = HERE.parents[2]
TODAY = dt.date(2026, 9, 29)
LINEAR = "MATH.SYN.EQ.LINEAR"
TRANSPOSE = "MATH.SYN.EQ.TRANSPOSE"
APPLY = "MATH.SYN.EQ.APPLY"
SQUARE = "MATH.SYN.EXPR.SQUARE_DIFF"


def map_doc() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(MAP_V1.read_text(encoding="utf-8")))


def node_rec(doc: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(n for n in doc["nodes"] if n["id"] == node_id)


@pytest.fixture(scope="session")
def lexicon() -> Lexicon:
    return default_lexicon()


@pytest.fixture()
def snap_v1() -> Snapshot:
    return parse_snapshot(map_doc())


@pytest.fixture()
def pages_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pages"
    shutil.copytree(PAGES, d)
    return d


@pytest.fixture()
def run_lint(lexicon: Lexicon) -> Callable[..., LintReport]:
    def run(pages: Path, snapshot: Snapshot | None = None, **kw: Any) -> LintReport:
        return lint_dir(pages, snapshot=snapshot, lexicon=lexicon, today=kw.pop("today", TODAY), **kw)
    return run


def edit(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"夹具里找不到要改的文本：{old!r}"
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def page_file(pages: Path, node_id: str) -> Path:
    return pages / "kp" / f"{node_id}.md"


def checks_of(report: LintReport, severity: str = "block") -> set[str]:
    return {i.check_type for i in report.issues if i.severity == severity}


# ---------- 知识服务（PR ②）：一次性 Postgres 与异步测试 ----------
@pytest.fixture(scope="session")
def kgedb():
    """会话级一次性 Postgres：bootstrap 角色、全部迁移、合成 kge 数据（tests/pgkge.py）。
    没有 Postgres 时非严格模式跳过；严格模式（make ... CONTRACT_STRICT=1 → KNOWLEDGE_TEST_STRICT=1）直接失败"""
    import os

    from pgkge import EphemeralPG
    try:
        db = EphemeralPG()
    except FileNotFoundError as e:
        if os.environ.get("KNOWLEDGE_TEST_STRICT") == "1":
            raise
        pytest.skip(str(e))
    db.start()
    yield db
    db.stop()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
