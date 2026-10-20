"""开关矩阵（deploy/flags/staging.yaml，LT-01 范围第 5 条）：
- 结构：学科与功能齐全（决定记录：数学、道法、语文批改开，英语关；今日计划、诊断、再练、复测、星图、信任界面、周报、对账卡、审核台开；
  模考、月度计划、讲评白板、知识读缓存关）；
- 每个开关名在源码里真实存在（字面量或按学科拼出的模板）；
- 关的功能在 staging 不设（value 为 null 或关的取值）；开发开关清单里的名字都存在；
- 语义：BFF 与前端用真实的解析函数核对（flags_check.ts）；Python 服务用真实的 Settings.from_env 核对；
- 模考没有接进任何服务路由（没有开关可开）；编排文件里 BFF 与前端的开关取值与矩阵一致。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT
from names import name_exists, read_sources

FLAG_VARS = {"STARBUDDY_TRUST_UI", "STARBUDDY_STARMAP", "STARBUDDY_PRACTICE", "STARBUDDY_RETEST_UI", "STARBUDDY_TODAY_ORCH",
             "STARBUDDY_MONTHLY_PLAN", "STARBUDDY_FAMILY_GOALS", "STARBUDDY_SUBJECT_GATE"}
REQUIRED_ON = {"math", "daofa", "chinese_grading", "today_plan", "diagnosis", "practice", "retest", "star_map", "trust_ui", "review_console"}
REQUIRED_OFF = {"english", "mock_exam", "monthly_plan", "stage_whiteboard", "knowledge_read_cache"}
COVERS = {"weekly_report", "reconcile_card"}
OFF_VALUES = {"KNOWLEDGE_READ_CACHE": "0", "LEARNING_TODAY_SNAPSHOT": "0"}


def entries(flags):
    return [*flags["subjects"], *flags["features"]]


def test_structure(flags):
    assert flags["schema"] == "starbuddy-flags/1" and flags["environment"] == "staging"
    by_id = {e["id"]: e for e in entries(flags)}
    assert len(by_id) == len(entries(flags)), "id 重复"
    for i in REQUIRED_ON:
        assert by_id[i]["state"] == "on", i
    for i in REQUIRED_OFF:
        assert by_id[i]["state"] == "off", i
    covered = {c for e in entries(flags) for c in e.get("covers", [])}
    assert COVERS <= covered, "周报、对账卡须由信任界面开关覆盖"
    for e in entries(flags):
        pend = e.get("pending", [])
        assert e["state"] in ("on", "off") and isinstance(pend, list), e["id"]
        assert set(pend) <= {"signed_content", "internal_v1", "model_provider", "human"}, e["id"]
    # 契约审查 A2：诊断、再练、复测（以及依赖它们的今日计划）在 learning-internal v1 签字与启用（LT-04）之前不可用
    for i in ("diagnosis", "practice", "retest", "today_plan"):
        assert "internal_v1" in by_id[i]["pending"], i


def test_switch_names_exist_in_source(flags):
    for e in entries(flags):
        for s in e["switches"]:
            assert name_exists(s["var"], read_sources([s["source"]])), f"{e['id']}：{s['var']} 不在 {s['source']}"
    for s in flags["dev_only"]:
        assert name_exists(s["var"], read_sources([s["source"]])), f"dev_only：{s['var']} 不在 {s['source']}"


def test_off_switches_not_set(flags):
    for e in entries(flags):
        if e["state"] != "off":
            continue
        for s in e["switches"]:
            assert s["value"] is None or s["value"] == OFF_VALUES.get(s["var"]), f"{e['id']}：{s['var']} 关的功能在 staging 不能设"
    dev = {(s["service"], s["var"]) for s in flags["dev_only"]}
    for e in entries(flags):
        if e["state"] == "on":
            for s in e["switches"]:
                assert (s["service"], s["var"]) not in dev or s["value"] is None, f"{s['var']} 是开发开关，staging 不能设"


def _service_values(flags, service):
    out = {}
    for e in entries(flags):
        for s in e["switches"]:
            if s["service"] == service:
                prev = out.get(s["var"], "unset")
                assert prev in ("unset", s["value"]), f"{service} {s['var']} 在矩阵里取值矛盾"
                out[s["var"]] = s["value"]
    return out


def test_bff_web_semantics_with_real_parsers(flags):
    """BFF 与前端：staging 接受矩阵取值且解析结果一致；关的按关；pilot / 生产拒绝或按关（flags_check.ts）"""
    node = shutil.which("node")
    if node is None or not (ROOT / "services/bff/node_modules").is_dir():
        pytest.skip("缺 node 或 BFF 依赖")
    keep = lambda d: {k: v for k, v in d.items() if k in FLAG_VARS}  # noqa: E731
    payload = {"bff": keep(_service_values(flags, "bff")), "web": keep(_service_values(flags, "web"))}
    r = subprocess.run([node, "--no-warnings", str(ROOT / "deploy/tests/flags_check.ts")], input=json.dumps(payload), capture_output=True,
                       text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["checked"] == len(payload["bff"]) + len(payload["web"])


def _run_py(venv_service: str, code: str, env: dict) -> subprocess.CompletedProcess:
    py = ROOT / "services" / venv_service / ".venv" / "bin" / "python"
    if not py.exists():
        pytest.skip(f"缺 services/{venv_service}/.venv")
    return subprocess.run([str(py), "-I", "-c", code], cwd=ROOT / "services" / venv_service, env={"PATH": "/usr/bin:/bin", **env},
                          capture_output=True, text=True, timeout=60)


SYN = {"t1": "a" * 40, "t2": "b" * 40, "t3": "c" * 40, "t4": "d" * 40, "t5": "e" * 40, "t6": "f" * 40}


def test_learning_settings_accept_matrix(flags):
    vals = {k: v for k, v in _service_values(flags, "learning").items() if v is not None}
    env = {"LEARNING_ENV": "staging", "LEARNING_SERVICE_TOKEN": SYN["t1"], "LEARNING_DATABASE_URL": "postgresql://syn@172.28.0.20/syn",
           "LEARNING_HOST": "172.28.0.10", "LEARNING_PRACTICE_TICKET_SECRET": SYN["t2"], "LEARNING_ADAPTER_AYA_TOKEN": SYN["t3"],
           "LEARNING_ADAPTER_ZHIXING_TOKEN": SYN["t4"], "LEARNING_ADAPTER_WENLI_TOKEN": SYN["t5"], **vals}
    code = ("import sys; sys.path.insert(0, '.'); from learning.config import Settings; s = Settings.from_env(); "
            "print(s.env, sorted(s.adapters), s.today_snapshot)")
    r = _run_py("learning", code, env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == "staging" and "echo" not in r.stdout
    for dev in [s for s in flags["dev_only"] if s["service"] == "learning"]:
        r = _run_py("learning", code, {**env, dev["var"]: "synthetic" if "PRESENTER" in dev["var"] else "1"})
        assert r.returncode != 0, f"staging 设 {dev['var']} 没有拒绝"


def test_review_console_and_knowledge_insight_accept_matrix(flags):
    rc = _service_values(flags, "review_console")
    env = {"REVIEW_CONSOLE_KGE_DATABASE_URL": "postgresql://kge_syn@172.28.0.20/syn",
           "REVIEW_CONSOLE_REVIEW_DATABASE_URL": "postgresql://review_syn@172.28.0.20/syn", "REVIEW_CONSOLE_HOST": "172.28.0.13",
           **{k: v for k, v in rc.items() if v is not None}}
    code = "import sys; sys.path.insert(0, '.'); from review_console.config import ReviewSettings; print(ReviewSettings.from_env().env)"
    r = _run_py("learning", code, env)
    assert r.returncode == 0 and r.stdout.strip() == "staging", r.stderr
    assert _run_py("learning", code, {**env, "REVIEW_CONSOLE_FEATURES": "tag_worklist"}).returncode != 0
    kv = {k: v for k, v in _service_values(flags, "knowledge").items() if v is not None}
    code = ("import sys; sys.path.insert(0, '.'); from knowledge.service.config import Settings; s = Settings.from_env(); "
            "print(s.env, s.read_cache)")
    r = _run_py("knowledge", code, {"KNOWLEDGE_ENV": "staging", "KNOWLEDGE_DATABASE_URL": "postgresql://syn@172.28.0.20/syn",
                                    "KNOWLEDGE_TOKEN_LEARNING": SYN["t6"], "KNOWLEDGE_HOST": "172.28.0.12", **kv})
    assert r.returncode == 0 and r.stdout.split() == ["staging", "False"], r.stderr
    iv = {k: v for k, v in _service_values(flags, "insight").items() if v is not None}
    code = ("import sys; sys.path.insert(0, '.'); from insight.service.config import Settings; s = Settings.from_env(); "
            "print(s.env, s.monthly_enabled)")
    r = _run_py("insight", code, {"INSIGHT_ENV": "staging", "INSIGHT_DATABASE_URL": "postgresql://syn@172.28.0.20/syn",
                                  "INSIGHT_TOKEN_BFF": SYN["t1"], "INSIGHT_HOST": "172.28.0.14", **iv})
    assert r.returncode == 0 and r.stdout.split() == ["staging", "False"], r.stderr


def test_mock_exam_not_wired():
    """模考没有开关可开：学习服务不注册模考路由，BFF 没有模考路由（mock_exam 只作为计划任务类型与计量资源名出现），三科适配器的 plan
    不产生 mock_exam 任务"""
    app = (ROOT / "services/learning/learning/app.py").read_text(encoding="utf-8")
    assert "mock_exam" not in app, "模考接进了学习服务路由：矩阵须给出开关并保持关"
    for p in (ROOT / "services/bff/src").rglob("*.ts"):
        assert not re.search(r"['`]/[^'`\n]*mock", p.read_text(encoding="utf-8")), f"{p}：出现了模考路由"
    for p in (ROOT / "adapters").glob("*/src/*.ts"):
        assert "mock_exam" not in p.read_text(encoding="utf-8"), f"{p}：适配器产生模考任务"


def test_compose_and_k8s_flags_match_matrix(flags, compose_staging, k8s_docs):
    for svc in ("bff", "web"):
        want = {k: v for k, v in _service_values(flags, svc).items() if k in FLAG_VARS}
        env = compose_staging["services"][svc]["environment"]
        for k, v in want.items():
            assert env.get(k) == v, f"compose {svc} {k}"
        kenv = {}
        for d in k8s_docs:
            if d.get("kind") == "Deployment" and d["metadata"]["name"] == svc:
                kenv = {e["name"]: e.get("value") for c in d["spec"]["template"]["spec"]["containers"] for e in c["env"]}
        for k, v in want.items():
            assert kenv.get(k) == v, f"k8s {svc} {k}"
