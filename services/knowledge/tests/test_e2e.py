"""端到端（spec 验收第 1 条的 PR ① 部分）：合成地图 + 合成页面仓库上跑通
原子变更 → 编入找出受影响页 → 渲染重建（已审核叙述段逐字不变）→ 程序体检 → 按风险分进合并窗口（低风险每页每天至多一次）。
走命令行（python -m knowledge.compiler / knowledge.lint），产物写到临时目录，不推送任何远程仓库。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from conftest import APPLY, LINEAR, MAP_V1, PAGES, SQUARE, TODAY, TRANSPOSE, map_doc, node_rec
from knowledge.pages.format import parse

SERVICE = Path(__file__).resolve().parents[1]


def _run(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", module, *args], capture_output=True, text=True, cwd=str(SERVICE))


def _write_map(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _narrative(text: str) -> dict[str, str]:
    return {s.id: s.content for s in parse(text).sections if s.kind == "narrative"}


def test_end_to_end_atom_change_to_merge_window(tmp_path):
    doc = map_doc()
    node_rec(doc, LINEAR)["excludes"] = ["二元一次方程组", "分式方程"]      # 定义变更 → 高风险
    doc["region_projections"][0]["weight"] = 0.06                         # APPLY 地区权重 → 低风险
    doc["region_projections"].append({"id": "syn-rp-003", "node_id": SQUARE, "region": "syn-region", "exam_year": 2025,
                                      "weight": 0.02, "typical_slots": ["选择题"], "source_ref": "synthetic"})  # 新增原子 → 低风险
    v2 = _write_map(tmp_path / "map-v2.yaml", doc)
    out = tmp_path / "out"
    r = _run("knowledge.compiler", "--pages", str(PAGES), "--old-snapshot", str(MAP_V1), "--new-snapshot", str(v2),
             "--out", str(out), "--now", "2026-09-29T10:00:00+08:00")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "编入：3 个编译任务" in r.stdout

    mrs = sorted(p.name for p in out.iterdir())
    low = "mr-2026-09-29-low"
    assert low in mrs and len(mrs) == 2
    high = next(m for m in mrs if m != low)
    assert high.startswith(f"mr-2026-09-29-{LINEAR}-high-")

    low_json = json.loads((out / low / "merge_request.json").read_text(encoding="utf-8"))
    assert [p["page_id"] for p in low_json["pages"]] == [APPLY, SQUARE]
    assert low_json["reviewers_required"] == 0 and low_json["lint"]["blocks"] == 0
    high_json = json.loads((out / high / "merge_request.json").read_text(encoding="utf-8"))
    assert high_json["reviewers_required"] == 2 and high_json["risk"] == "high"
    (page,) = high_json["pages"]
    assert page["new_version"] == "1.1.0" and page["changed_sections"] == ["definition"]
    assert [b["atom"] for b in page["basis_atoms"]] == [f"node:{LINEAR}"]
    assert "-包括：只含一个未知数" in page["diff"] and "+包括：只含一个未知数" in page["diff"]

    # 已审核叙述段逐字不变；新页面落在输出目录，没有改动输入目录
    for mr_dir, pid in ((low, APPLY), (low, SQUARE), (high, LINEAR)):
        new_text = (out / mr_dir / "pages" / "kp" / f"{pid}.md").read_text(encoding="utf-8")
        old_text = (PAGES / "kp" / f"{pid}.md").read_text(encoding="utf-8")
        assert _narrative(new_text) == _narrative(old_text)
        assert new_text != old_text
    assert not (out / low / "pages" / "kp" / f"{TRANSPOSE}.md").exists()

    md = (out / high / "MERGE_REQUEST.md").read_text(encoding="utf-8")
    for must in ("审核人数：2", "### 依据原子", "### 差异", "### 体检结果", "程序体检：阻断 0"):
        assert must in md

    # 合并后的仓库整库体检（CI 的做法）：用新快照、以上一审核版为基线，零阻断
    merged = tmp_path / "merged"
    shutil.copytree(PAGES, merged)
    for mr_dir in mrs:
        for f in (out / mr_dir / "pages").rglob("*.md"):
            target = merged / f.relative_to(out / mr_dir / "pages")
            target.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    lint = _run("knowledge.lint", str(merged), "--map-snapshot", str(v2), "--baseline", str(PAGES), "--today", TODAY.isoformat(), "--json")
    assert lint.returncode == 0, lint.stdout
    # 未合并时，旧页面在新快照下哈希过期 → 阻断（提示需要重编）
    stale = _run("knowledge.lint", str(PAGES), "--map-snapshot", str(v2), "--today", TODAY.isoformat(), "--json")
    assert stale.returncode == 1
    assert {i["check_type"] for i in json.loads(stale.stdout)["issues"]} >= {"atom_hash", "template"}


def test_end_to_end_same_day_low_risk_published_once(tmp_path):
    """同一页面一天内第二次低风险变更：已发布过的页面顺延到次日窗口"""
    doc = map_doc()
    doc["region_projections"][0]["weight"] = 0.06
    v2 = _write_map(tmp_path / "map-v2.yaml", doc)
    log = tmp_path / "published.json"
    log.write_text(json.dumps({APPLY: ["2026-09-29"]}), encoding="utf-8")
    out = tmp_path / "out"
    r = _run("knowledge.compiler", "--pages", str(PAGES), "--old-snapshot", str(MAP_V1), "--new-snapshot", str(v2), "--out", str(out),
             "--now", "2026-09-29T15:00:00+08:00", "--published-log", str(log))
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in out.iterdir()) == ["mr-2026-09-30-low"]
    assert "顺延" in (out / "mr-2026-09-30-low" / "MERGE_REQUEST.md").read_text(encoding="utf-8")


def test_compiler_cli_reports_lint_blocks_and_refuses_bad_input(tmp_path):
    doc = map_doc()
    doc["current_affairs"] = [{"id": "syn-ca-1", "node_ids": [APPLY], "source": "synthetic", "valid_from": "2026-01-01",
                               "valid_until": "2026-06-30"}]
    doc["region_projections"][0]["weight"] = 0.06
    v2 = _write_map(tmp_path / "map-v2.yaml", doc)
    r = _run("knowledge.compiler", "--pages", str(PAGES), "--old-snapshot", str(MAP_V1), "--new-snapshot", str(v2),
             "--out", str(tmp_path / "out"), "--now", "2026-09-29T10:00:00+08:00")
    assert r.returncode == 1 and "体检阻断 1" in r.stdout  # 过期的时政原子
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x").write_text("x", encoding="utf-8")
    r = _run("knowledge.compiler", "--pages", str(PAGES), "--old-snapshot", str(MAP_V1), "--new-snapshot", str(v2), "--out", str(busy))
    assert r.returncode == 2 and "不是空目录" in r.stderr
    r = _run("knowledge.compiler", "--pages", str(PAGES), "--old-snapshot", str(MAP_V1), "--new-snapshot", str(v2),
             "--out", str(PAGES / "out"))
    assert r.returncode == 2 and "页面目录里" in r.stderr


def test_outputs_contain_no_model_identifiers_or_real_data(tmp_path):
    doc = map_doc()
    doc["region_projections"][0]["weight"] = 0.06
    v2 = _write_map(tmp_path / "map-v2.yaml", doc)
    out = tmp_path / "out"
    assert _run("knowledge.compiler", "--pages", str(PAGES), "--old-snapshot", str(MAP_V1), "--new-snapshot", str(v2), "--out", str(out),
                "--now", "2026-09-29T10:00:00+08:00").returncode == 0
    for f in out.rglob("*"):
        if f.is_file():
            text = f.read_text(encoding="utf-8").lower()
            for word in ("fake-", "model:", "claude", "gpt", "qwen", "deepseek", "glm", "kimi", "doubao"):
                assert word not in text, (f, word)
