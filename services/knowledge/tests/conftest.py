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


# ---------- PR ③：已发布页面快照目录（合成） ----------
def make_page_snapshot(root: Path, *, commit: str = "d" * 40, drop: tuple[str, ...] = (),
                       map_version: str | None = None) -> Path:
    """把合成页面按 knowledge.compiler.snapshot 的导出格式放进 root（manifest.json + pages/kp/*.md）。
    drop：manifest 不列的页面（文件仍在 = 未发布）；map_version：改写 manifest 与页面头部的地图版本（模拟页面落后）"""
    import hashlib
    import json

    (root / "pages" / "kp").mkdir(parents=True)
    entries = []
    for p in sorted((PAGES / "kp").glob("*.md")):
        text = p.read_text(encoding="utf-8")
        mv = "0.9.0"
        if map_version is not None:
            text = text.replace("map_version: 0.9.0", f"map_version: {map_version}", 1)
            mv = map_version
        raw = text.encode("utf-8")
        (root / "pages" / "kp" / p.name).write_bytes(raw)
        if p.stem in drop:
            continue
        entries.append({"page_id": p.stem, "path": f"pages/kp/{p.name}", "page_version": "1.0.0", "map_version": mv,
                        "sha256": hashlib.sha256(raw).hexdigest()})
    (root / "manifest.json").write_text(json.dumps({"format": "kge-page-snapshot/1", "commit": commit, "pages": entries}),
                                        encoding="utf-8")
    return root
