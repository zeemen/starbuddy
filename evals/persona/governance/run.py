"""人设回归的离线运行器（make eval-persona 调用；spec 设计要点 1“触发回归”、6、7）。

  python -m governance run [--role 角色] [--base 提交] [--strict] [--out 目录] [--root 仓库根] [--registry 登记表]
                           [--no-baseline] [--json-only 文件]

每次 CI 都跑（不靠 paths 过滤）：
  1. 检查器自检：合成正例 / 反例（evals/persona/cases/）判得对，判错即失败；
  2. 静态面：登记表、Prompt 文本、合同对照表；
  3. 录制回放：已登记 Prompt 的角色，按用例找录音；指纹不匹配或缺录音 → 严格模式失败“需要重新录制”，非严格记“未运行”（退出码 3）；
  4. 基线：--base（默认 PR 的目标分支或上一个提交）检出到临时目录，用基线自己的运行器再跑一次，输出前后对比；
  5. 影响集：git diff <merge-base>，命中 Prompt、登记表、锁文件、禁语表、docs/personas、evals/persona 的标“人设变更”。
不联网、不读密钥、不调用任何模型。退出码：0 通过，1 不通过，3 缺录音（非严格）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from personas.registry import check as registry_check
from personas.registry import iter_prompts, lock_path

from . import drift as drift_mod
from . import family, replay
from .cases import CaseError, case_fingerprint, load_cases, turns_of
from .common import CATEGORIES, FAMILY_CHECKS, GUARD_VERSION, Env, load_env
from .report import empty_row, finalize_rows, validate, write

PERSONA_PATHS = ("packages/personas/registry.yaml", "packages/personas/prompts.lock", "packages/personas/forbidden/",
                 "packages/personas/patterns/", "docs/personas/", "evals/persona/")
DRIFT_TO_CATEGORY = {"drift_identity": "identity", "drift_forbidden": "forbidden", "drift_star": "starmap", "drift_protocol": "protocol"}
FAMILY_TO_EVALUATOR = {"cross_subject": "cross_subject", "metaphor": "metaphor", "referral": "referral", "mode": "protocol",
                       "role_types": "protocol"}
DIANDIAN_TO_CATEGORY = {"identity": "identity", "forbidden": "forbidden", "referral": "referral", "starmap": "starmap",
                        "handoff": "protocol", "drift": "drift"}
PROMPT_BASED_STATIC = ("role_types", "cross_subject", "mode")
TRANSCRIPT_CATEGORIES = ("identity", "forbidden", "protocol", "starmap", "drift", "leak")
FIXED_CONFLICTS = [
    {"id": "9b", "persona_id": None, "detail": "F 类被数学（阿鸦 V1.1）与语文（妙笔 V1.0）重复定义；本冲刺按 F:math / F:chinese 命名空间处理"},
    {"id": "9c", "persona_id": None, "detail": "七科规范第八章“比喻隔离”示例的“砺石”在妙笔 V1.0 里不存在（妙笔的比喻体系是笔 / 落笔 / 收笔 / 笔锋 / 留白）"},
    {"id": "9d", "persona_id": None, "detail": "阿基、X 博士没有规格书：合同符合性标“未立项”，不算通过"},
]


# ---------------------------------------------------------------- git

def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, text=True, timeout=60,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_TERMINAL_PROMPT": "0", "HOME": os.environ.get("HOME", "/tmp")})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def default_base(root: Path) -> str | None:
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref and _git(root, "rev-parse", "--verify", "-q", f"origin/{base_ref}"):
        return f"origin/{base_ref}"
    return "HEAD~1" if _git(root, "rev-parse", "--verify", "-q", "HEAD~1") else None


def trigger() -> str:
    ev = os.environ.get("GITHUB_EVENT_NAME")
    return {"pull_request": "pr", "push": "main"}.get(ev or "", "manual")


def changed_set(env: Env, base: str | None) -> dict[str, Any]:
    root = env.root
    out: dict[str, Any] = {"persona_change": False, "prompts": [], "specs": [], "personas": [], "paths": [], "source": ""}
    if base is None or _git(root, "rev-parse", "--verify", "-q", base) is None:
        out["source"] = "没有基线提交（或不在 git 仓库里），无法计算影响集"
        return out
    mb = (_git(root, "merge-base", base, "HEAD") or "").strip() or base
    # 中文路径（docs/personas/阿鸦-规格书…）：core.quotepath=off，按行切分
    paths = set((_git(root, "diff", "--name-only", mb) or "").splitlines())
    paths |= set((_git(root, "ls-files", "--others", "--exclude-standard") or "").splitlines())
    entries = [e for e in iter_prompts(env.reg)]
    prompt_paths = {e.path for e in entries}
    rel = lambda p: p.relative_to(root).as_posix() if p.is_relative_to(root) else str(p)  # noqa: E731
    reg_rel, lock_rel = env.registry_rel, rel(lock_path(env.reg, root))
    watched = [p for p in sorted(paths) if p in prompt_paths or p in (reg_rel, lock_rel) or p.startswith(PERSONA_PATHS)]
    prompts = {e.prompt_id for e in entries if e.path in paths}
    old_lock = _git(root, "show", f"{mb}:{lock_rel}")
    try:
        old = (json.loads(old_lock).get("prompts") or {}) if old_lock else {}
    except json.JSONDecodeError:
        old = {}
    cur_lock = lock_path(env.reg, root)
    cur = (json.loads(cur_lock.read_text(encoding="utf-8")).get("prompts") or {}) if cur_lock.is_file() else {}
    prompts |= {pid for pid in cur if cur.get(pid) != old.get(pid)}
    specs = sorted(p for p in paths if p.startswith("docs/personas/"))
    affected = {e.owner_id for e in entries if e.prompt_id in prompts and e.owner_type != "task"}
    affected |= {pid for pid, p in env.personas.items() if p.get("spec_doc") in specs}
    if any(p.startswith(("packages/personas/forbidden/", "packages/personas/patterns/")) or p == reg_rel for p in paths):
        affected |= {pid for pid, p in env.personas.items() if p.get("status") == "active"}
    out.update(persona_change=bool(watched), prompts=sorted(prompts), specs=specs, personas=sorted(affected), paths=watched,
               source=f"git diff {mb[:12]}（基线 {base}）")
    return out


def run_baseline(root: Path, base: str, role: str | None, registry_rel: str) -> tuple[dict[str, Any] | None, str | None, str]:
    """把基线提交的人设相关目录导出到临时目录，用基线自己的运行器跑一遍。返回（结果, 基线提交, 说明）"""
    sha = (_git(root, "rev-parse", "--verify", "-q", f"{base}^{{commit}}") or "").strip()
    if not sha:
        return None, None, f"基线 {base} 不存在"
    paths = [p for p in ("packages/personas", "evals/persona", "docs/personas") if _git(root, "cat-file", "-e", f"{sha}:{p}") is not None]
    if "evals/persona" not in paths or "packages/personas" not in paths:
        return None, sha, "基线提交里没有人设治理组件（早于冲刺 3-F）"
    try:
        r = subprocess.run(["git", "archive", "--format=tar", sha, "--", *paths], cwd=root, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None, sha, "基线导出失败"
    if r.returncode != 0:
        return None, sha, "基线提交里没有人设治理组件（早于冲刺 3-F）"
    with tempfile.TemporaryDirectory(prefix="persona-base.") as tmp:
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
            tf.extractall(tmp, filter="data")
        if not (Path(tmp) / "evals/persona/governance/__main__.py").is_file() or not (Path(tmp) / registry_rel).is_file():
            return None, sha, "基线提交里没有人设治理组件（早于冲刺 3-F）"
        out = Path(tmp) / "base.json"
        cmd = [sys.executable, "-m", "governance", "run", "--root", tmp, "--registry", registry_rel, "--no-baseline", "--json-only", str(out)]
        if role:
            cmd += ["--role", role]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}
        subprocess.run(cmd, cwd=Path(tmp) / "evals/persona", capture_output=True, text=True, timeout=300, env=env)
        if not out.is_file():
            return None, sha, "基线运行器没有产出结果"
        return json.loads(out.read_text(encoding="utf-8")).get("results"), sha, "基线运行完成"


# ---------------------------------------------------------------- 主流程

class Acc:
    def __init__(self, personas: list[str]) -> None:
        self.results = {pid: {c: empty_row() for c in CATEGORIES} for pid in personas}
        self.methods = {"rule": 0, "context": 0, "replay": 0, "model": 0, "human": 0}

    def add(self, pid: str, cat: str, outcome: str, method: str = "rule") -> None:
        if pid not in self.results:
            return
        row = self.results[pid][cat]
        row["total"] += 1
        row[{"pass": "passed", "fail": "failed", "not_run": "not_run"}[outcome]] += 1
        if outcome != "not_run":
            self.methods[method] = self.methods.get(method, 0) + 1


def _run_case_checks(env: Env, case: dict[str, Any], turns: list[Any], checks: list[str]) -> dict[str, list[dict[str, Any]]]:
    pid = case["persona_id"]
    out: dict[str, list[dict[str, Any]]] = {}
    for chk in checks:
        if chk in family.TRANSCRIPT_CHECKS:
            out[f"family_{chk}"] = family.TRANSCRIPT_CHECKS[chk](env, pid, turns)
        elif chk == "drift":
            out["drift"], _ = drift_mod.check_drift(env, pid, turns, grade=case.get("grade"), case_id=case["case_id"])
        elif chk == "leak":
            out["leak"] = drift_mod.check_leak(env, pid, turns, case.get("problem") or {}, case_id=case["case_id"])
        elif chk == "forbidden":
            from personas.guard import classify  # noqa: PLC0415
            hits = []
            for i, t in enumerate(turns):
                if t.speaker == pid:
                    hits += [{**h, "turn": i, "check": "forbidden", "persona_id": pid, "reason": h["rule_id"]}
                             for h in classify(pid, t.text, t.context, ctx=env.ctx)]
            out["forbidden"] = hits
        else:
            raise CaseError(f"{case['case_id']}: 未知检查 {chk}")
    return out


def selftest(env: Env, cases: list[dict[str, Any]], role: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """合成正例 / 反例：检查器判得对不对（expect 与实际结论一致；变体要因 expect_check 这一子项失败）"""
    rows = []
    conflicts: list[dict[str, Any]] = []
    for c in cases:
        if c["kind"] == "recorded" or (role and c["persona_id"] != role):
            continue
        turns = turns_of(c)
        if c["kind"] == "family":
            found = family.TRANSCRIPT_CHECKS[c["check"]](env, c["persona_id"], turns)
        elif c["kind"] == "drift":
            found, stats = drift_mod.check_drift(env, c["persona_id"], turns, grade=c.get("grade"), case_id=c["case_id"])
            conflicts += stats["spec_conflicts"]
        else:
            found = drift_mod.check_leak(env, c["persona_id"], turns, c.get("problem") or {}, case_id=c["case_id"])
        actual = "fail" if found else "pass"
        ok = actual == c["expect"]
        if ok and c.get("expect_check"):
            ok = any(f["check"] == c["expect_check"] for f in found)
        rows.append({"case_id": c["case_id"], "persona_id": c["persona_id"], "kind": c["kind"], "check": c.get("check") or c["kind"],
                     "expect": c["expect"], "actual": actual, "ok": ok,
                     "findings": [{k: f[k] for k in ("check", "reason", "turn", "rule_id") if k in f} for f in found][:5]})
    by: dict[str, dict[str, int]] = {}
    for r in rows:
        b = by.setdefault(r["check"], {"total": 0, "passed": 0, "pass_cases": 0, "fail_cases": 0})
        b["total"] += 1
        b["passed"] += r["ok"]
        b["pass_cases" if r["expect"] == "pass" else "fail_cases"] += 1
    uniq = {(x["id"], x["persona_id"]): x for x in conflicts}
    return ({"total": len(rows), "passed": sum(r["ok"] for r in rows), "by_check": by,
             "mismatches": [r for r in rows if not r["ok"]]}, list(uniq.values()))


def run(root: Path, registry_rel: str, *, role: str | None = None, base: str | None = None, strict: bool = False,
        baseline: bool = True) -> tuple[int, dict[str, Any]]:
    env = load_env(root, registry_rel)
    if role and role not in env.personas:
        raise SystemExit(f"登记表里没有角色 {role}")
    reasons: list[str] = []
    cases_dir = env.eval_path("cases_dir", "evals/persona/cases")
    try:
        cases = load_cases(cases_dir)
    except CaseError as e:
        cases = []
        reasons.append(f"用例不合法：{e}")
    personas = [pid for pid in env.personas if not role or pid == role]
    acc = Acc(personas)

    # 1. 检查器自检
    st, drift_conflicts = selftest(env, cases, role)
    for m in st["mismatches"]:
        reasons.append(f"检查器自检判错：{m['case_id']}（期望 {m['expect']}，实际 {m['actual']}）")
    for check_name, b in st["by_check"].items():
        if not b["pass_cases"] or not b["fail_cases"]:
            if not role:
                reasons.append(f"检查器自检：{check_name} 缺合成正例或反例")

    # 2. 静态面
    matrix = env.eval_path("contract_matrix", "evals/persona/contract-matrix.yaml")
    static, conflicts, not_started = family.static_all(env, matrix, {c["case_id"] for c in cases})
    for f in (static["contract"].get("__all__") or []):
        reasons.append(f["reason"])
    has_prompts = {e.owner_id for e in iter_prompts(env.reg) if e.owner_type != "task"}
    static_findings: list[dict[str, Any]] = []
    for pid in personas:
        for chk in FAMILY_CHECKS:
            per = static[chk]
            if pid in not_started:
                acc.add(pid, f"family_{chk}", "not_run")
                continue
            if pid not in per:
                continue  # 这一类不适用于该角色（例如点点没有合同对照行）
            if chk in PROMPT_BASED_STATIC and pid not in has_prompts:
                acc.add(pid, f"family_{chk}", "not_run")
                if chk in FAMILY_TO_EVALUATOR:
                    acc.add(pid, FAMILY_TO_EVALUATOR[chk], "not_run")
                continue
            outcome = "fail" if per[pid] else "pass"
            acc.add(pid, f"family_{chk}", outcome)
            if chk in FAMILY_TO_EVALUATOR:
                acc.add(pid, FAMILY_TO_EVALUATOR[chk], outcome)
            static_findings += per[pid]
            for f in per[pid]:
                reasons.append(f"{pid} {f['check']}：{f['reason']}")

    # 没有登记 Prompt 的已立项角色：转写面各项记“未运行”（不是“不适用”，也不记为通过）
    for pid in personas:
        if pid not in has_prompts and pid not in not_started:
            for cat in TRANSCRIPT_CATEGORIES:
                acc.add(pid, cat, "not_run")

    # 3. 录制回放
    rec_items: list[dict[str, Any]] = []
    forbidden_items: list[dict[str, Any]] = []
    for pid in personas:
        if pid not in has_prompts:
            continue
        required = [c for c in cases if c["kind"] == "recorded" and c["persona_id"] == pid]
        if pid == "diandian":
            required_dd = _diandian_required(env)
        else:
            required_dd = []
        if not required and not required_dd:
            reasons.append(f"{pid}：登记了 Prompt 但没有任何录制回放用例（evals/persona/cases/ 的 kind: recorded）")
        for c in required:
            s = replay.status(env, pid, c)
            rec_items.append({k: v for k, v in s.items() if k != "recording"})
            checks = list(c.get("checks") or [])
            if s["status"] != "valid":
                for chk in checks:
                    _add_check(acc, pid, chk, "not_run")
                continue
            turns = replay.recorded_turns(s["recording"])
            for key, found in _run_case_checks(env, c, turns, checks).items():
                _add_check(acc, pid, key.removeprefix("family_"), "fail" if found else "pass", method="replay", findings=found)
                for f in found:
                    if key == "forbidden":
                        forbidden_items.append({"persona_id": pid, "case_id": c["case_id"], "turn": f["turn"], "class": f["class"],
                                                "rule_id": f["rule_id"], "span": f["span"], "method": f["method"]})
                    else:
                        reasons.append(f"{pid} {c['case_id']} {f['check']}：{f['reason']}")
                    if f.get("method") == "context":
                        acc.methods["context"] += 1
        for item in required_dd:
            rec_items.append(item["row"])
            if item["row"]["status"] != "valid":
                _add_check(acc, pid, item["category"], "not_run")
            else:
                _add_check(acc, pid, item["category"], "pass" if item["passed"] else "fail", method="replay")
                if not item["passed"]:
                    reasons.append(f"diandian {item['row']['case_id']}：点点用例未通过")
    bad = [r for r in rec_items if r["status"] != "valid"]
    missing_recordings = bool(bad)
    for r in bad:
        msg = f"{r['persona_id']}/{r['case_id']}：{r['reason']}"
        if strict or r["status"] == "invalid":
            reasons.append(msg)
    if forbidden_items:
        reasons.append(f"禁语触发 {len(forbidden_items)} 次（规则层与结构层，触发率大于 0 即不通过）")

    # 4. 基线与影响集
    base_ref = base if base else (default_base(env.root) if baseline else None)
    base_results, base_sha, base_note = (None, None, "未请求基线") if not (baseline and base_ref) else run_baseline(env.root, base_ref, role, registry_rel)
    finalize_rows(acc.results, base_results)
    for pid, cats in acc.results.items():
        for cat, row in cats.items():
            if row["delta"] is not None and row["delta"] < 0:
                reasons.append(f"{pid} {cat} 通过率低于基线（{row['baseline_pass_rate']:.0%} → {row['pass_rate']:.0%}）")
    changed = changed_set(env, base_ref) if baseline or base else {"persona_change": False, "prompts": [], "specs": [], "personas": [],
                                                                   "paths": [], "source": "未计算（--no-baseline）"}
    reg_res = registry_check(env.root, registry_rel)
    if not reg_res["ok"]:
        reasons.append(f"persona-registry-check 未通过（{len(reg_res['errors'])} 处）")

    fail = list(reasons)
    status = "fail" if fail else ("not_run" if missing_recordings else "pass")
    commit = (_git(env.root, "rev-parse", "HEAD") or "unknown").strip()
    lex = env.ctx.lexicon
    case_files = sorted(cases_dir.rglob("*.yaml")) if cases_dir.is_dir() else []
    cases_version = "sha256:" + hashlib.sha256(b"".join(p.read_bytes() for p in case_files)).hexdigest()[:16]
    rep: dict[str, Any] = {
        "format": "persona-report/1",
        "run_id": os.environ.get("GITHUB_RUN_ID") or f"local-{commit[:12]}",
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": commit, "base_commit": base_sha, "baseline_note": base_note, "trigger": trigger(), "role": role, "strict": strict,
        "changed": changed,
        "versions": {"registry_version": str(env.reg.get("version")), "lexicon_version": lex.version, "lexicon_reviewed": lex.reviewed,
                     "guard_version": GUARD_VERSION, "cases_version": cases_version},
        "results": acc.results,
        "forbidden_hits": {"total": len(forbidden_items), "by_class": _by_class(forbidden_items), "items": forbidden_items},
        "method_share": acc.methods,
        "recordings": {"valid": sum(r["status"] == "valid" for r in rec_items), "outdated": sum(r["status"] == "outdated" for r in rec_items),
                       "missing": sum(r["status"] == "missing" for r in rec_items), "invalid": sum(r["status"] == "invalid" for r in rec_items),
                       "items": rec_items},
        "selftest": st,
        "spec_conflicts": conflicts + drift_conflicts + FIXED_CONFLICTS,
        "exemptions": reg_res["exemptions"],
        "static_findings": static_findings,
        "conclusion": {"status": status, "reasons": fail if fail else ([f"{r['persona_id']}/{r['case_id']}：{r['reason']}" for r in bad] if bad else [])},
    }
    errs = validate(rep)
    if errs:
        rep["conclusion"] = {"status": "fail", "reasons": [f"报告不符合 schema：{e}" for e in errs[:5]]}
        return 1, rep
    if status == "fail":
        return 1, rep
    if status == "not_run":
        return (1 if strict else 3), rep
    return 0, rep


def _by_class(items: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for h in items:
        out[h["class"]] = out.get(h["class"], 0) + 1
    return out


def _add_check(acc: Acc, pid: str, chk: str, outcome: str, *, method: str = "rule", findings: list[dict[str, Any]] | None = None) -> None:
    """把一个检查项的结论记进报告的类别：家族级进 family_* 并映射到评测七项；漂移按子项映射；泄露、禁语各进本栏"""
    if chk in FAMILY_CHECKS:
        acc.add(pid, f"family_{chk}", outcome, method)
        if chk in FAMILY_TO_EVALUATOR:
            acc.add(pid, FAMILY_TO_EVALUATOR[chk], outcome, method)
    elif chk == "drift":
        acc.add(pid, "drift", outcome, method)
        subs = {f["check"] for f in findings or []}
        for sub, cat in DRIFT_TO_CATEGORY.items():
            acc.add(pid, cat, "not_run" if outcome == "not_run" else ("fail" if sub in subs else "pass"), method)
    elif chk in CATEGORIES:
        acc.add(pid, chk, outcome, method)
    else:
        raise CaseError(f"未知检查 {chk}")


def _diandian_required(env: Env) -> list[dict[str, Any]]:
    """点点有登记 Prompt 时，1-I 的 120 条用例里开发集的每一条都要有录音（留出集不录、不跑，规则 14）"""
    from scorer.cases import load_cases as load_dd, read_holdout  # noqa: PLC0415
    from scorer.judge import load_lexicon  # noqa: PLC0415
    from scorer.report import score  # noqa: PLC0415
    p = env.personas["diandian"]
    base = env.root / str(p.get("diandian_cases") or "evals/persona/diandian/cases")
    holdout = set(read_holdout(base.parent / "holdout.txt"))
    lex = load_lexicon(env.root)
    out = []
    for c in load_dd(base):
        if c["case_id"] in holdout:
            continue
        fp = "sha256:" + hashlib.sha256(json.dumps(c, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        student = [{"speaker": "student", "text": str(t), "emotion_signal": False, "asks_identity": False} for t in c["turns"]]
        s = replay.status(env, "diandian", {"case_id": c["case_id"]}, case_fp=fp, student=student)
        passed = False
        if s["status"] == "valid":
            replies = [{"text": t.text, "fields": t.fields} for t in replay.recorded_turns(s["recording"]) if t.speaker == "diandian"]
            rep = score([c], [{"case_id": c["case_id"], "run": 1, "turns": replies}], lex)
            passed = bool(rep["detail"] and rep["detail"][0]["passed"])
        out.append({"row": {k: v for k, v in s.items() if k != "recording"}, "category": DIANDIAN_TO_CATEGORY.get(c["category"], "drift"),
                    "passed": passed})
    return out


def main_run(args: Any) -> int:
    root = Path(args.root).resolve()
    code, rep = run(root, args.registry, role=args.role, base=args.base, strict=args.strict, baseline=not args.no_baseline)
    if args.json_only:
        Path(args.json_only).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        return code
    out = Path(args.out) if args.out else None
    if out is not None:
        write(rep, out)
    c = rep["conclusion"]
    for r in c["reasons"][:40]:
        print(f"  {r}")
    label = {"pass": "通过", "fail": "不通过", "not_run": "未运行（缺录音）"}[c["status"]]
    if c["status"] == "not_run" and not args.strict:
        print("eval-persona: 跳过（缺录音），报告里记为“未运行”，不记为通过")
    st = rep["selftest"]
    print(f"eval-persona: 人设治理回归{label}（检查器自检 {st['passed']}/{st['total']}，录音 有效 {rep['recordings']['valid']} / "
          f"过期 {rep['recordings']['outdated']} / 缺失 {rep['recordings']['missing']}；报告 {out / 'persona-report.md' if out else '未写出'}）")
    return code

