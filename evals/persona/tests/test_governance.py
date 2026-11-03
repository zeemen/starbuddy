"""人设治理回归（冲刺 3-F）：七类家族级检查（静态面 + 转写面）、漂移、N16 泄露、录制回放、报告与验收标准。

全部是合成数据；不联网（本模块每个测试都禁止网络调用）、不读密钥、不调用任何模型。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parents[1]      # evals/persona
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from governance import drift as drift_mod  # noqa: E402
from governance import family  # noqa: E402
from governance.cases import CaseError, load_cases, turns_of  # noqa: E402
from governance.common import EVALUATOR_LABELS, Turn, load_env  # noqa: E402
from governance.replay import make_recording, recording_path, status  # noqa: E402
from governance.report import to_markdown, validate  # noqa: E402
from governance.run import run  # noqa: E402
from personas.registry import check as registry_check  # noqa: E402
from personas.registry import write_lock  # noqa: E402

REG = "packages/personas/registry.yaml"
FIX = "evals/persona/fixtures/governance/registry.yaml"
ENV = load_env(ROOT, REG)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError(f"人设治理回归不应发起网络请求：{a!r}")
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def T(speaker, text, **kw) -> Turn:
    return Turn(speaker, text, **kw)


# ================================================================ 用例与检查器自检

def test_cases_cover_every_check_with_positive_and_negative():
    cases = load_cases(HERE / "cases")
    kinds: dict[str, set[str]] = {}
    for c in cases:
        key = c.get("check") or c["kind"]
        kinds.setdefault(key, set()).add(c["expect"])
    for k in ("role_types", "cross_subject", "naming", "metaphor", "mode", "referral", "drift", "leak"):
        assert kinds.get(k) == {"pass", "fail"}, k
    drift_personas = {c["persona_id"] for c in cases if c["kind"] == "drift" and c["expect"] == "pass"}
    assert drift_personas == {"diandian", "aya", "miaobi", "fangyuan", "chunqiu", "athena"}, "每个已立项角色至少一条漂移脚本"
    for c in cases:
        if c["kind"] == "drift" and c["expect"] == "pass":
            n = sum(1 for t in turns_of(c) if t.speaker == c["persona_id"])
            assert 20 <= n <= 40, (c["case_id"], n)


def test_runner_on_repo_passes_and_report_validates(tmp_path):
    code, rep = run(ROOT, REG, strict=True, baseline=False)
    assert code == 0, rep["conclusion"]
    assert validate(rep) == []
    assert rep["selftest"]["total"] >= 60 and rep["selftest"]["passed"] == rep["selftest"]["total"]
    assert rep["recordings"] == {"valid": 0, "outdated": 0, "missing": 0, "invalid": 0, "items": []}, "仓库里还没有真实 Prompt，也就没有必需录音"
    assert rep["results"]["aji"]["family_contract"]["not_run"] == 1, "未立项记“未运行”，不算通过"
    assert rep["results"]["aya"]["family_contract"]["passed"] == 1
    assert {c["id"] for c in rep["spec_conflicts"]} >= {"9a", "9b", "9c", "9d", "9e"}
    md = to_markdown(rep)
    header = next(line for line in md.splitlines() if line.startswith("| 角色 |"))
    assert header == "| 角色 | " + " | ".join(EVALUATOR_LABELS.values()) + " |", "列与 persona-evaluator 的七项一致"
    assert "身份披露" in header and "比喻隔离" in header


def test_role_filter_limits_results():
    code, rep = run(ROOT, REG, role="aya", baseline=False)
    assert code == 0 and list(rep["results"]) == ["aya"]
    assert all(m["persona_id"] == "aya" for m in rep["selftest"]["mismatches"]) and rep["selftest"]["total"] > 0


def test_selftest_mismatch_fails_the_run(tmp_path):
    root = _copy_root(tmp_path, with_cases=True)
    p = root / "evals/persona/cases/family/naming.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("case_id: FAM-NM-002\n    persona_id: diandian\n    expect: fail",
                                                       "case_id: FAM-NM-002\n    persona_id: diandian\n    expect: pass"), encoding="utf-8")
    write_lock(root, REG)
    code, rep = run(root, REG, baseline=False)
    assert code == 1 and any("FAM-NM-002" in r for r in rep["conclusion"]["reasons"])


def test_case_files_must_be_marked_synthetic(tmp_path):
    d = tmp_path / "cases"
    d.mkdir()
    (d / "x.yaml").write_text("kind: family\ncheck: naming\nsource: gold\nnot_persona_corpus: true\ncases: []\n", encoding="utf-8")
    with pytest.raises(CaseError, match="金标准"):
        load_cases(d)


# ================================================================ 静态面：每类一正一反

def _env_with(tmp_path, mutate) -> tuple:
    """复制真实登记表，按 mutate 改一处，返回（env, 合同对照表路径）"""
    root = _copy_root(tmp_path, with_cases=True)
    reg_path = root / REG
    doc = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    mutate(doc, root)
    reg_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return load_env(root, REG), root / "evals/persona/contract-matrix.yaml"


def _persona(doc, pid):
    return next(p for p in doc["personas"] if p["persona_id"] == pid)


def test_static_clean_on_repo():
    static, conflicts, not_run = family.static_all(ENV, HERE / "contract-matrix.yaml", {c["case_id"] for c in load_cases(HERE / "cases")})
    for chk, per in static.items():
        for pid, errs in per.items():
            assert errs == [], (chk, pid, errs)
    assert set(not_run) == {"aji", "xboshi"}


def test_static_contract_empty_cell_fails(tmp_path):
    env, matrix = _env_with(tmp_path, lambda d, r: None)
    doc = yaml.safe_load(matrix.read_text(encoding="utf-8"))
    doc["personas"]["aya"]["contracts"][8] = {"spec": "", "cases": []}
    doc["personas"]["miaobi"]["contracts"][3]["cases"] = ["NOPE-001"]
    doc["personas"]["aji"] = {"status": "active"}
    matrix.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    ids = {c["case_id"] for c in load_cases(env.root / "evals/persona/cases")}
    out = family.static_contract(env, matrix, ids)
    assert any("第 8 条没有对应的规格书章节" in f["reason"] for f in out["aya"])
    assert any("NOPE-001" in f["reason"] for f in out["miaobi"])
    assert out["aji"], "未立项的角色要标 not_started"
    assert out["fangyuan"] == []


def test_static_one_per_subject(tmp_path):
    env, _ = _env_with(tmp_path, lambda d, r: _persona(d, "chunqiu").__setitem__("subject", "daofa"))
    assert any("一科一位" in f["reason"] for f in family.static_one_per_subject(env))
    assert family.static_one_per_subject(ENV) == []


def _add_prompt(doc, root, pid, text, **extra):
    rel = f"evals/persona/fixtures/tmp-{pid}.prompt.md"
    (root / rel).write_text(text, encoding="utf-8")
    _persona(doc, pid)["prompts"] = [{"prompt_id": f"{pid}-test", "path": rel, "prompt_version": "0.1.0", **extra}]


def test_static_cross_subject(tmp_path):
    env, _ = _env_with(tmp_path, lambda d, r: _add_prompt(d, r, "aya", "【合成】语文课的笔老师也这样讲。\n"))
    assert family.static_cross_subject(env)["aya"]
    env, _ = _env_with(tmp_path / "b", lambda d, r: _add_prompt(d, r, "aya", "【合成】只讲这道数学题。\n"))
    assert family.static_cross_subject(env)["aya"] == []


def test_static_naming(tmp_path):
    env, _ = _env_with(tmp_path, lambda d, r: _persona(d, "aya").__setitem__("display_name", "鸦鸦"))
    assert family.static_naming(env)["aya"]
    env, _ = _env_with(tmp_path / "b", lambda d, r: _persona(d, "fangyuan")["address_terms"].__setitem__("diandian", "方圆官"))
    assert family.static_naming(env)["fangyuan"], "考官型直呼其名"
    assert all(v == [] for v in family.static_naming(ENV).values())


def test_static_metaphor_sets_must_be_disjoint(tmp_path):
    env, _ = _env_with(tmp_path, lambda d, r: _persona(d, "miaobi")["metaphor_set"].append("石子"))
    out = family.static_metaphor(env)
    assert out["aya"] and out["miaobi"]
    env, _ = _env_with(tmp_path / "b", lambda d, r: _persona(d, "chunqiu")["metaphor_set"].append("笔杆"))
    assert family.static_metaphor(env)["chunqiu"], "单字“笔”是妙笔的，别科的词不得包含它"


def test_static_mode_examiner_prompt_must_declare_card_moments(tmp_path):
    good = "---\nappearance_moments: [\"F3 主观题批改\", \"判分申诉\"]\n---\n【合成】\n"
    env, _ = _env_with(tmp_path, lambda d, r: _add_prompt(d, r, "fangyuan", good))
    assert family.static_mode(env)["fangyuan"] == []
    bad = "---\nappearance_moments: [\"拆题\"]\n---\n【合成】\n"
    env, _ = _env_with(tmp_path / "b", lambda d, r: _add_prompt(d, r, "fangyuan", bad))
    assert family.static_mode(env)["fangyuan"]
    env, _ = _env_with(tmp_path / "c", lambda d, r: _add_prompt(d, r, "fangyuan", "【合成】没有声明\n"))
    assert family.static_mode(env)["fangyuan"]


def test_static_role_types_scene_prompt(tmp_path):
    fx = load_env(ROOT, FIX)
    assert family.static_role_types(fx)["syn-npc"] == []
    root = _copy_root(tmp_path, with_cases=False)
    p = root / "evals/persona/fixtures/governance/prompts/syn-npc.prompt.md"
    p.write_text(p.read_text(encoding="utf-8") + "对学生的每句话打分并讲解。\n", encoding="utf-8")
    assert family.static_role_types(load_env(root, FIX))["syn-npc"]


def test_static_referral_structure_and_conflict(tmp_path):
    out, conflicts = family.static_referral(ENV)
    assert all(v == [] for v in out.values())
    assert {c["persona_id"] for c in conflicts if c["id"] == "9a"} == {"aya", "miaobi"}
    env, _ = _env_with(tmp_path, lambda d, r: _persona(d, "aya")["referral"].__setitem__("text", "【合成】说说看，发生了什么？"))
    assert len(family.static_referral(env)[0]["aya"]) == 3
    env, _ = _env_with(tmp_path / "b", lambda d, r: _persona(d, "chunqiu").__setitem__("referral", {}))
    assert family.static_referral(env)[0]["chunqiu"]


# ================================================================ 转写面补充：场景角色（合成登记表）

def test_scene_role_transcript():
    fx = load_env(ROOT, FIX)
    ok = [T("student", "【合成】你是真人吗？", asks_identity=True), T("syn-npc", "【合成】我是 AI 扮演的角色，我们接着逛集市吧。")]
    assert family.transcript_role_types(fx, "syn-npc", ok) == []
    grade = [T("student", "【合成】我这句说得对吗？"), T("syn-npc", "【合成】这句得 3 分，答错了。")]
    assert family.transcript_role_types(fx, "syn-npc", grade)
    teach = [T("student", "【合成】教教我"), T("syn-npc", "【合成】首先找主语，然后找谓语。")]
    assert family.transcript_role_types(fx, "syn-npc", teach)
    deny = [T("student", "【合成】你是真人吗？", asks_identity=True), T("syn-npc", "【合成】我们接着逛集市吧。")]
    assert family.transcript_role_types(fx, "syn-npc", deny)


# ================================================================ 漂移与泄露补充

def test_drift_window_and_caps():
    base = next(c for c in load_cases(HERE / "cases") if c["case_id"] == "DRIFT-AYA-001")
    turns = turns_of(base)
    errs, stats = drift_mod.check_drift(ENV, "aya", turns, grade=7)
    assert errs == [] and stats["window"] == 5 and stats["rounds"] == 22
    more = turns + [T("student", "【合成】下一题。"), T("aya", "【合成】再找一枚规律碎片。", fields={"ai_disclosure_shown": False},
                                                   context={"moment": "lesson", "verified_claims": []})]
    assert drift_mod.check_drift(ENV, "aya", more, grade=7)[0] == [], "七年级上限 3 次：2 次通过"
    errs, _ = drift_mod.check_drift(ENV, "aya", more, grade=9)
    assert any(e["check"] == "drift_setting" for e in errs), "九年级设定元素上限 1 次"


def test_leak_requires_hint_level():
    turns = [T("aya", "【合成】x=3", context={})]
    errs = drift_mod.check_leak(ENV, "aya", turns, {"answer": "x = 3", "reveal_level": 6})
    assert errs and errs[0]["method"] == "context"
    assert drift_mod.check_leak(ENV, "aya", turns, {"reveal_level": 6})


# ================================================================ 录制回放与验收（开发计划：任一 Prompt 变更自动触发回归）

def _copy_root(tmp_path: Path, *, with_cases: bool) -> Path:
    root = tmp_path / "repo"
    ign = shutil.ignore_patterns("__pycache__", "tests", ".pytest_cache")
    shutil.copytree(ROOT / "packages/personas", root / "packages/personas", ignore=ign)
    for d in ("governance", "schema", "fixtures/governance"):
        shutil.copytree(HERE / d, root / "evals/persona" / d, ignore=ign)
    shutil.copytree(ROOT / "docs/personas", root / "docs/personas")
    if with_cases:
        shutil.copytree(HERE / "cases", root / "evals/persona/cases")
        shutil.copy(HERE / "contract-matrix.yaml", root / "evals/persona/contract-matrix.yaml")
        (root / "evals/persona/diandian/lexicon").mkdir(parents=True)
        shutil.copy(HERE / "diandian/lexicon/forbidden.yaml", root / "evals/persona/diandian/lexicon/forbidden.yaml")
    return root


def _git(root: Path, *args: str) -> None:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root.parent), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "synthetic", "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
           "GIT_COMMITTER_NAME": "synthetic", "GIT_COMMITTER_EMAIL": "synthetic@example.invalid"}
    subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args], cwd=root, check=True,
                   capture_output=True, env=env)


@pytest.fixture()
def fixture_repo(tmp_path):
    root = _copy_root(tmp_path, with_cases=False)
    assert write_lock(root, FIX) == []
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "基线（合成）")
    return root


def _bump(root: Path, version: str) -> None:
    p = root / FIX
    p.write_text(p.read_text(encoding="utf-8").replace("prompt_version: 1.0.0", f"prompt_version: {version}"), encoding="utf-8")


def _edit_prompt(root: Path) -> None:
    p = root / "evals/persona/fixtures/governance/prompts/syn-tutor.prompt.md"
    p.write_text(p.read_text(encoding="utf-8").replace("合成夹具初版", "合成夹具第二版"), encoding="utf-8")


def test_fixture_recordings_are_valid(fixture_repo):
    code, rep = run(fixture_repo, FIX, strict=True, baseline=False)
    assert code == 0, rep["conclusion"]
    assert rep["recordings"]["valid"] == 2 and {r["model_version"] for r in rep["recordings"]["items"]} == {"synthetic-replay-0"}
    ref = rep["results"]["syn-tutor"]["family_referral"]
    assert (ref["passed"], ref["failed"]) == (2, 0), "静态面（登记表话术结构）+ 回放各一次"
    assert rep["results"]["syn-npc"]["family_role_types"]["passed"] >= 1
    assert rep["method_share"]["replay"] > 0


def test_acceptance_1_prompt_change_without_bump_fails_registry_check(fixture_repo):
    _edit_prompt(fixture_repo)
    res = registry_check(fixture_repo, FIX)
    assert not res["ok"] and any("Prompt 变更必须升版本" in e for e in res["errors"])
    assert any("先升 prompt_version" in e for e in write_lock(fixture_repo, FIX)), "锁文件不能绕过“必须升版本”"
    r = subprocess.run([sys.executable, "-m", "personas.registry", "check", "--root", str(fixture_repo), "--registry", FIX],
                       cwd=ROOT, capture_output=True, text=True, env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "packages")})
    assert r.returncode == 1 and "Prompt 变更必须升版本" in r.stdout


def test_acceptance_2_bump_without_rerecord_fails_strict(fixture_repo):
    _edit_prompt(fixture_repo)
    _bump(fixture_repo, "1.1.0")
    assert write_lock(fixture_repo, FIX) == []
    assert registry_check(fixture_repo, FIX)["ok"]
    code, rep = run(fixture_repo, FIX, strict=True, base="HEAD")
    assert code == 1 and rep["conclusion"]["status"] == "fail"
    assert any("需要重新录制" in r for r in rep["conclusion"]["reasons"])
    assert rep["recordings"]["outdated"] == 1
    code, rep = run(fixture_repo, FIX, strict=False, base="HEAD")
    assert code == 3 and rep["conclusion"]["status"] == "not_run", "非严格：记“未运行”，不记为通过"
    assert rep["results"]["syn-tutor"]["family_referral"]["not_run"] >= 1


def test_acceptance_3_rerecord_passes_and_changed_lists_prompt(fixture_repo):
    _edit_prompt(fixture_repo)
    _bump(fixture_repo, "1.1.0")
    assert write_lock(fixture_repo, FIX) == []
    env = load_env(fixture_repo, FIX)
    case = next(c for c in load_cases(env.eval_path("cases_dir", "")) if c["case_id"] == "SYN-REC-001")
    old = json.loads(recording_path(env, "syn-tutor", "SYN-REC-001").read_text(encoding="utf-8"))
    rec = make_recording(env, "syn-tutor", case, old["transcript"], model_version="synthetic-replay-1", params={"temperature": 0},
                         recorded_by="synthetic-fixture", recorded_at="2026-09-30", synthetic=True)
    recording_path(env, "syn-tutor", "SYN-REC-001").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    _git(fixture_repo, "add", "-A")
    _git(fixture_repo, "commit", "-q", "-m", "升版本并重新录制（合成）")
    code, rep = run(fixture_repo, FIX, strict=True, base="HEAD~1")
    assert code == 0, rep["conclusion"]
    assert rep["changed"]["prompts"] == ["syn-tutor-main"] and rep["changed"]["persona_change"] is True
    assert "syn-tutor" in rep["changed"]["personas"]
    assert rep["base_commit"] and rep["baseline_note"] == "基线运行完成"
    assert rep["results"]["syn-tutor"]["family_referral"]["baseline_pass_rate"] == 1.0
    assert validate(rep) == []


def test_changed_detects_spec_doc_with_chinese_path(fixture_repo):
    spec = fixture_repo / "docs/personas/阿鸦-规格书-V1.1.md"
    spec.write_text(spec.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    code, rep = run(fixture_repo, FIX, base="HEAD", baseline=False)
    assert "docs/personas/阿鸦-规格书-V1.1.md" in rep["changed"]["specs"] and rep["changed"]["persona_change"] is True


def test_case_change_makes_recording_outdated(fixture_repo):
    p = fixture_repo / "evals/persona/fixtures/governance/cases/recorded.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("【合成】这题怎么开始？", "【合成】这题从哪开始？"), encoding="utf-8")
    env = load_env(fixture_repo, FIX)
    case = next(c for c in load_cases(env.eval_path("cases_dir", "")) if c["case_id"] == "SYN-REC-001")
    s = status(env, "syn-tutor", case)
    assert s["status"] == "outdated" and "用例指纹" in s["reason"]


def test_tampered_prompt_fingerprint_is_outdated(fixture_repo):
    env = load_env(fixture_repo, FIX)
    rp = recording_path(env, "syn-tutor", "SYN-REC-001")
    doc = json.loads(rp.read_text(encoding="utf-8"))
    doc["prompt_fingerprints"]["syn-tutor-main"] = "sha256:" + "1" * 64
    rp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    case = next(c for c in load_cases(env.eval_path("cases_dir", "")) if c["case_id"] == "SYN-REC-001")
    s = status(env, "syn-tutor", case)
    assert s["status"] == "outdated" and "Prompt 指纹" in s["reason"]


def test_missing_and_invalid_recordings(fixture_repo):
    env = load_env(fixture_repo, FIX)
    rp = recording_path(env, "syn-npc", "SYN-REC-002")
    doc = json.loads(rp.read_text(encoding="utf-8"))
    bad = copy.deepcopy(doc)
    bad["data_level"] = "L2"
    rp.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    code, rep = run(fixture_repo, FIX, strict=False, baseline=False)
    assert code == 1 and rep["recordings"]["invalid"] == 1, "不合法的录音（例如不是 L0）任何模式都失败"
    rp.unlink()
    code, rep = run(fixture_repo, FIX, strict=True, baseline=False)
    assert code == 1 and rep["recordings"]["missing"] == 1
    assert any("需要重新录制" in r for r in rep["conclusion"]["reasons"])


def test_forbidden_hit_in_recording_fails(fixture_repo):
    env = load_env(fixture_repo, FIX)
    rp = recording_path(env, "syn-tutor", "SYN-REC-001")
    doc = json.loads(rp.read_text(encoding="utf-8"))
    doc["transcript"][1]["text"] = "【合成】这题很简单，你应该会。"
    rp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    code, rep = run(fixture_repo, FIX, strict=True, baseline=False)
    assert code == 1 and rep["forbidden_hits"]["total"] == 2
    assert {h["rule_id"] for h in rep["forbidden_hits"]["items"]} == {"A-01", "F-aya-04"}
    assert all("text" not in h for h in rep["forbidden_hits"]["items"]), "报告不回显原文"
    assert "这题很简单" not in json.dumps(rep, ensure_ascii=False)


def test_cli_writes_report_and_exit_codes(fixture_repo, tmp_path):
    out = tmp_path / "rep"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, "-m", "governance", "run", "--root", str(fixture_repo), "--registry", FIX, "--strict",
                        "--no-baseline", "--out", str(out)], cwd=HERE, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads((out / "persona-report.json").read_text(encoding="utf-8"))
    assert validate(rep) == [] and (out / "persona-report.md").read_text(encoding="utf-8").startswith("# 人设回归报告")
    _edit_prompt(fixture_repo)
    _bump(fixture_repo, "1.1.0")
    write_lock(fixture_repo, FIX)
    r = subprocess.run([sys.executable, "-m", "governance", "run", "--root", str(fixture_repo), "--registry", FIX, "--no-baseline",
                        "--out", str(out)], cwd=HERE, capture_output=True, text=True, env=env)
    assert r.returncode == 3 and "跳过（缺录音）" in r.stdout
