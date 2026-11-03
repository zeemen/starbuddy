"""Gate 0 证据包的星图截图（spec 第 3 节、待人确认 #4；make gate0-evidence GATE0_STARMAP=1）。

在合成联调完成后的同一一次性库上：给选中学生的合成家长登记 +999 登录号码、建 bff_app 角色（只在 EphemeralPG 上）→ 起 BFF
（只连本机临时实例，短信捕获桩，限流放宽的一份配置，同 apps/web/scripts/e2e.sh）→ 测试构建前端（.next-e2e，原子占位）并 next start
（随机端口）→ apps/web/scripts/gate0-starmap.ts 用本机 Chromium 经真实会话登录、截学生壳内星图页（家长视角另 1 张），并用同一会话取
BFF 星图 JSON、试开一名未绑定学生的星图页与接口 → 本文件对账。

**v0 截图证明什么**：星图 v0（3-H）每片星空画该科全部已登记能力点、掌握未写库，全部是中性底星，页面本身不含任何只属于这名学生的星。
所以截图只证明“真实会话 + RLS 能打开该学生的页、打不开未绑定学生的页，星空等于在读科目”；
Gate 0 硬1“同一学生模型”主要由 events.csv、nodes.csv、checks.json 证明，截图只作辅证（lessons 待人确认）。

对账规则（reconcile，纯函数，tools/e2e-cohort/tests/test_starmap.py 覆盖；RULES 里同时写明 v0 下的效力）：
  1. 星空科目 = 库里该学生 status = 'active' 的选科（只计有已登记能力点的科目），严格相等、不重复；
  2. 页面 DOM 与同一会话取到的 BFF 星图 JSON 一致：每片星空 SVG 星数 = 列表行数 = JSON 星数，三态与保持计数一致；
  3. JSON 节点（skill = 科目:local_id）= 该科已登记能力点，label_code 与登记表一致；
  4. 该学生该科证据节点（nodes.csv）⊆ 页面节点；
  5. 带状态的星（非未亮、保持或待验证）⊆ 该学生该科证据节点；
  6. 掌握未写库：带状态的星 = 0；
  7. 同一会话打开未绑定学生（另一户）的星图页与 BFF 星图接口都是 404；
  8. 家长视角与同一学生的学生视角逐颗一致。
截图字节不要求确定性（只记文件与像素 SHA-256）；星数与对账结果确定。
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .cohort import PATTERN_LABELS, PATTERNS, Cohort

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps/web"
BFF = ROOT / "services/bff"
SCRIPT = "scripts/gate0-starmap.ts"
DIST_DIR = ".next-e2e"  # next.config.ts 只认这一个测试构建目录（与 scripts/e2e.sh 共用，不能同时跑）

PAGE_VERSION = "星图 v0（3-H），掌握未写库，无点亮星；H5 合入后重截"
WHAT_IT_PROVES = ("v0 截图只证明真实会话 + RLS 能打开该学生页（打不开未绑定学生的页）、星空等于在读科目；"
                  "硬1 的同一学生模型主要由 events.csv、nodes.csv、checks.json 证明，截图只作辅证")
DEFAULT_STUDENTS = 3
VIEWPORT = {"width": 1024, "height": 768}
IMAGE = {"type": "jpeg", "quality": 60}
SELECTION_RULE = ("按作答模式顺序（" + "、".join(PATTERN_LABELS[p] for p in PATTERNS) + "）取前 N 种模式，每种取标签最小的合成学生，"
                  "学生视角各 1 张；家长视角另截第一名选中学生的家长 1 张；跨学生 404 检查用标签最小的、不同家庭的合成学生")
WATERMARK = "原型 · 合成数据"
RULES = [
    {"rule": "1 星空科目 = 库里该学生 active 选科（有已登记能力点的科目），严格相等", "v0": "有效（按学生）"},
    {"rule": "2 页面 DOM 与同一会话的 BFF 星图 JSON 一致（星数、三态、保持）", "v0": "有效（页面渲染与接口一致）"},
    {"rule": "3 JSON 节点 = 该科已登记能力点，label_code 与登记表一致", "v0": "与学生无关（每名学生相同）"},
    {"rule": "4 该学生该科证据节点 ⊆ 页面节点", "v0": "与学生无关（页面 = 全部已登记能力点，任何证据节点都在其中）"},
    {"rule": "5 带状态的星 ⊆ 该学生该科证据节点", "v0": "空真（没有带状态的星）；H5 或掌握写库后生效，届时须重截"},
    {"rule": "6 掌握未写库：带状态的星 = 0", "v0": "有效（状态断言，与学生无关）"},
    {"rule": "7 同一会话打开未绑定学生的星图页与 BFF 星图接口都是 404", "v0": "有效（按会话）"},
    {"rule": "8 家长视角与同一学生的学生视角逐颗一致", "v0": "有效"},
]
REGISTRY_SOURCE = "公共登记表 platform.skill_node（联结 platform.curriculum_version 取 content_version），一次性库管理员连接只读查询"


class StarmapError(RuntimeError):
    pass


# ---------------- 选取与计划（纯函数） ----------------
def select_students(cohort: Cohort, n: int = DEFAULT_STUDENTS) -> list:
    if not 1 <= n <= len(PATTERNS):
        raise ValueError(f"星图截图学生数须在 1–{len(PATTERNS)} 之间")
    return [min((s for s in cohort.students if s.pattern == p), key=lambda s: s.label) for p in PATTERNS[:n]]


def unbound_other(cohort: Cohort, student) -> object:
    """跨学生检查的对象：标签最小的、不同家庭的合成学生（对该学生本人与其家长都没有绑定）"""
    return min((s for s in cohort.students if s.household_id != student.household_id), key=lambda s: s.label)


def plan_shots(cohort: Cohort, n: int = DEFAULT_STUDENTS) -> tuple[list[dict], dict[str, str]]:
    """返回（截图计划，家长账号 → 合成登录号码）。号码用未分配的国家码 +999，按家长首次出现的顺序编号。"""
    chosen = select_students(cohort, n)
    phones: dict[str, str] = {}
    for s in chosen:
        phones.setdefault(s.guardian_id, f"+999000090{len(phones) + 1:02d}")

    def shot(s, kind: str) -> dict:
        other = unbound_other(cohort, s)
        prefix = "/s" if kind == "student" else "/family"
        return {"name": f"{s.label}-{kind}", "kind": kind, "label": s.label, "student_id": s.student_id, "pattern": s.pattern,
                "phone": phones[s.guardian_id], "path": f"{prefix}/{s.student_id}/star-map",
                "other_label": other.label, "other_student_id": other.student_id, "other_path": f"{prefix}/{other.student_id}/star-map"}
    return [shot(s, "student") for s in chosen] + [shot(chosen[0], "guardian")], phones


# ---------------- 对账（纯函数） ----------------
def evidence_nodes(nodes_csv: str) -> dict[tuple[str, str], set[str]]:
    """nodes.csv → {(student_id, subject): 证据节点集合}；没有能力点的证据（node_id = '-'）不计"""
    out: dict[tuple[str, str], set[str]] = {}
    for r in csv.DictReader(io.StringIO(nodes_csv)):
        if r["node_id"] != "-":
            out.setdefault((r["student_id"], r["subject"]), set()).add(r["node_id"])
    return out


def _stateful(star: dict) -> bool:
    return star["display"] != "unlit" or bool(star["retained"]) or bool(star["needs_verification"])


def _counts(items: list[dict]) -> dict:
    return {**{d: sum(1 for x in items if x["display"] == d) for d in ("unlit", "half", "lit")},
            "retained": sum(1 for x in items if x["retained"])}


def reconcile(result: dict, student_id: str, active_subjects: set[str], evidence: dict[tuple[str, str], set[str]],
              catalog: dict[str, dict[str, str]], *, require_neutral: bool = True) -> dict:
    """一张星图页（DOM 统计 + 同一会话的 BFF 星图 JSON + 跨学生 404）与该学生的库内事实对账；返回 {ok, problems, skies}。
    catalog：{科目: {local_id: label_code}}；active_subjects：库里该学生 status = 'active' 的选科。"""
    problems: list[str] = []
    dom, api, cross = result.get("dom", {}), result.get("api", {}), result.get("cross", {})
    if api.get("student_id") != student_id:
        problems.append("BFF 星图 JSON 的 student_id 不是这名学生")
    api_skies = {s["subject"]: s for s in api.get("skies", [])}
    dom_skies = {s["subject"]: s for s in dom.get("skies", [])}
    # 规则 1：库里 active 选科（有已登记能力点的）严格相等；页面与 JSON 的星空科目也一致、不重复
    expected = {s for s in active_subjects if catalog.get(s)}
    api_subjects = [s["subject"] for s in api.get("skies", [])]
    dom_subjects = [s["subject"] for s in dom.get("skies", [])]
    if len(set(api_subjects)) != len(api_subjects) or len(set(dom_subjects)) != len(dom_subjects):
        problems.append(f"星空科目重复：JSON {api_subjects}、页面 {dom_subjects}")
    if set(api_subjects) != expected:
        problems.append(f"星空科目 {sorted(set(api_subjects))} ≠ 库里 active 选科 {sorted(expected)}")
    if set(dom_subjects) != set(api_subjects):
        problems.append(f"页面星空 {sorted(set(dom_subjects))} ≠ BFF JSON 星空 {sorted(set(api_subjects))}")
    skies_out = []
    for subj in sorted(api_skies):
        stars = api_skies[subj].get("stars", [])
        nodes: list[str] = []
        for st in stars:
            prefix, _, local = st["skill"].partition(":")
            if prefix != subj or not local:
                problems.append(f"{subj}：节点 {st['skill']} 不属于这片星空")
            nodes.append(local)
        page = set(nodes)
        if len(page) != len(nodes):
            problems.append(f"{subj}：JSON 里有重复的节点")
        want = _counts(stars)
        js = api_skies[subj].get("summary", {})
        if any(js.get(d) != want[d] for d in ("unlit", "half", "lit")):
            problems.append(f"{subj}：BFF JSON 的摘要与星不一致")
        # 规则 2：页面 DOM 与 JSON 一致
        d = dom_skies.get(subj)
        if d is not None:
            rows = d["rows"]
            by_row, svg, summ = _counts(rows), d["svg"], d["summary"]
            if not (svg["total"] == len(rows) == len(stars)):
                problems.append(f"{subj}：SVG 星 {svg['total']}、列表 {len(rows)}、JSON {len(stars)} 不一致")
            for k in ("unlit", "half", "lit"):
                if not (svg[k] == by_row[k] == summ[k] == want[k]):
                    problems.append(f"{subj}：{k} 计数不一致（SVG {svg[k]}、列表 {by_row[k]}、摘要 {summ[k]}、JSON {want[k]}）")
            if not (svg["retained"] == by_row["retained"] == want["retained"]):
                problems.append(f"{subj}：保持计数不一致")
        # 规则 3：已登记能力点（节点身份以 local_id 为准，label_code 另按登记表核对，不假设二者相等）
        registered = catalog.get(subj, {})
        extra, missing = sorted(page - set(registered)), sorted(set(registered) - page)
        if extra:
            problems.append(f"{subj}：页面多出 {len(extra)} 个未登记节点（如 {extra[0]}）")
        if missing:
            problems.append(f"{subj}：页面缺 {len(missing)} 个已登记节点（如 {missing[0]}）")
        bad_label = sorted(n for st, n in zip(stars, nodes) if n in registered and st.get("label_code") != registered[n])
        if bad_label:
            problems.append(f"{subj}：{len(bad_label)} 个节点的 label_code 与登记表不一致（如 {bad_label[0]}）")
        # 规则 4、5、6
        ev = evidence.get((student_id, subj), set())
        not_on_page = sorted(ev - page)
        if not_on_page:
            problems.append(f"{subj}：{len(not_on_page)} 个证据节点不在页面上（如 {not_on_page[0]}）")
        stateful = {n for st, n in zip(stars, nodes) if _stateful(st)}
        foreign = sorted(stateful - ev)
        if foreign:
            problems.append(f"{subj}：{len(foreign)} 颗带状态的星不是该学生的证据节点（如 {foreign[0]}）")
        if require_neutral and stateful:
            problems.append(f"{subj}：有 {len(stateful)} 颗带状态的星；掌握未写库前应全部为中性底星")
        skies_out.append({"subject": subj, "stars_total": len(stars), **want,
                          "needs_verification": sum(1 for st in stars if st["needs_verification"]),
                          "registered_nodes": len(registered), "evidence_nodes": len(ev),
                          "evidence_nodes_on_page": len(ev & page), "stateful_stars": len(stateful)})
    # 规则 7：跨学生 404（页面与 BFF 各一）
    if cross.get("page_status") != 404:
        problems.append(f"打开未绑定学生的星图页得到 HTTP {cross.get('page_status')}，应为 404")
    if cross.get("bff_status") != 404:
        problems.append(f"BFF 星图接口读未绑定学生得到 HTTP {cross.get('bff_status')}，应为 404")
    return {"ok": not problems, "problems": problems, "skies": skies_out,
            "cross_student": {"page_status": cross.get("page_status"), "bff_status": cross.get("bff_status")}}


def same_page(a: dict, b: dict) -> bool:
    """家长视角与学生视角逐颗一致（BFF JSON 的星空与每颗星、页面 DOM 的计数与列表）"""
    def key(r: dict):
        return ([(s["subject"], s["stars"], s["summary"]) for s in r.get("api", {}).get("skies", [])],
                [(s["subject"], s["svg"], s["summary"], s["rows"]) for s in r.get("dom", {}).get("skies", [])])
    return key(a) == key(b)


def build_record(shots_plan: list[dict], results: list[dict], nodes_csv: str, facts: dict, n: int) -> dict:
    """截图结果 → starmap.json 的记录（对账失败即 status = failed）。
    facts：{"catalog": {科目: {local_id: label_code}}, "active": {student_id: [科目]}, "content_versions": {科目: [版本]}, "commit": …}"""
    ev = evidence_nodes(nodes_csv)
    got = {r["name"]: r for r in results}
    shots, problems = [], []
    for p in shots_plan:
        r = got.get(p["name"])
        if r is None:
            problems.append(f"{p['name']}：没有截图结果")
            continue
        rc = reconcile(r, p["student_id"], set(facts["active"].get(p["student_id"], [])), ev, facts["catalog"])
        if r.get("dom", {}).get("watermark") != WATERMARK:
            rc["problems"].append(f"水印不是“{WATERMARK}”")
        if p["kind"] == "guardian":
            twin = got.get(f"{p['label']}-student")
            if twin is not None and not same_page(r, twin):
                rc["problems"].append("家长视角与学生视角不一致")
        rc["ok"] = not rc["problems"]
        problems += [f"{p['name']}：{x}" for x in rc["problems"]]
        shots.append({"name": p["name"], "kind": p["kind"], "label": p["label"], "student_id": p["student_id"],
                      "pattern": p["pattern"], "page_path": p["path"], "file": r["file"], "skies": rc["skies"],
                      "cross_student": {"other_label": p["other_label"], "other_student_id": p["other_student_id"],
                                        "page_path": p["other_path"], **rc["cross_student"]},
                      "reconcile_ok": rc["ok"], "reconcile_problems": rc["problems"],
                      "volatile": {"bytes": r["bytes"], "file_sha256": r["file_sha256"], "pixel_sha256": r["pixel_sha256"],
                                   "pixel_size": r["pixel_size"]}})
    rec = base_record(n, facts.get("commit"))
    rec.update({"status": "captured" if not problems else "failed", "shots": shots,
                "registered_nodes": {"source": REGISTRY_SOURCE,
                                     "by_subject": {k: len(v) for k, v in sorted(facts["catalog"].items())},
                                     "content_versions": {k: sorted(v) for k, v in sorted(facts.get("content_versions", {}).items())}},
                "reconciliation": {"ok": not problems, "problems": problems}})
    if problems:
        rec["reason"] = "星图对账失败"
    return rec


def base_record(n: int, commit: dict | None = None) -> dict:
    return {
        "notice": "合成联调，不是内测结论",
        "page_version": PAGE_VERSION,
        "what_it_proves": WHAT_IT_PROVES,
        "captured_at_commit": commit or {"commit": "unknown", "worktree_dirty": None},
        "pages": {"student": "/s/{student_id}/star-map", "guardian": "/family/{student_id}/star-map",
                  "bff": "/bff/v0/students/{student_id}/star-map（同一会话）"},
        "selection_rule": SELECTION_RULE, "students_requested": n,
        "viewport": dict(VIEWPORT), "device_scale_factor": 1, "full_page": True, "image": dict(IMAGE),
        "browser": "本机 Chromium（PLAYWRIGHT_BROWSERS_PATH，不下载）",
        "login": "真实会话：+999 合成号码 → 短信捕获桩 → 家长首页；学生视角经家长“打开学生模式”",
        "node_identity": "BFF 星图 JSON 的 skill（科目:local_id）；页面 DOM 只核对行数、三态与保持计数",
        "reconciliation_rule": RULES,
        "volatile_fields": "shots[].volatile（截图字节、文件与像素 SHA-256、像素尺寸）不要求确定性；其余字段同一代码确定",
    }


def failed_record(n: int, reason: str, commit: dict | None = None) -> dict:
    rec = base_record(n, commit)
    rec.update({"status": "failed", "reason": reason, "shots": []})
    return rec


# ---------------- 证据包里的星图文件（有副作用，范围只限本工具写的文件） ----------------
IMAGE_PATTERNS = ("*-student.jpg", "*-student.png", "*-guardian.jpg", "*-guardian.png")
OWN_FILES = ("starmap.json", "provenance.md", *IMAGE_PATTERNS)


def clean(out_dir: Path) -> None:
    """重新截图前：只删本工具写过的文件，不碰目录里的其他东西"""
    d = out_dir / "starmap"
    if not d.is_dir():
        return
    for pat in OWN_FILES:
        for p in d.glob(pat):
            p.unlink()


def drop_images(out_dir: Path) -> list[str]:
    """status ≠ captured：删掉本轮已写的截图（失败的截图不留在证据包里）"""
    gone = []
    for pat in IMAGE_PATTERNS:
        for p in sorted((out_dir / "starmap").glob(pat)):
            p.unlink()
            gone.append(p.name)
    return gone


def write_provenance(out_dir: Path, current_commit: str) -> Path | None:
    """starmap/provenance.md：截图对应的页面版本、截取提交号与本证据包提交号；不同则标“截图沿用”，页面版本变了标“须重截”。
    不带 GATE0_STARMAP=1 重生证据包时保留已有截图，靠这个文件说明它们来自哪一次。"""
    rec_path = out_dir / "starmap" / "starmap.json"
    if not rec_path.exists():
        return None
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    captured = (rec.get("captured_at_commit") or {}).get("commit", "unknown")
    lines = ["# 星图截图来历", "",
             f"- 截图状态：{rec.get('status')}",
             f"- 截图对应的页面版本：{rec.get('page_version')}",
             f"- 截取提交号：{captured}",
             f"- 本证据包提交号：{current_commit}"]
    if captured != current_commit:
        lines.append("- **截图沿用**：截图不是本证据包这一次截取的（只在星图页面改版时重截，如 H5）。")
    if rec.get("page_version") != PAGE_VERSION:
        lines.append(f"- **页面版本已变（当前：{PAGE_VERSION}），须重截**。")
    lines += [f"- 说明：{WHAT_IT_PROVES}。", ""]
    p = out_dir / "starmap" / "provenance.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ---------------- 起栈与截图（有副作用） ----------------
def missing_prerequisites() -> str | None:
    # 前端目录里有 .env* 时拒绝：next build / next start 会自动加载它们，可能带进本机配置或凭据（只看文件名，不读内容）
    envs = sorted(p.name for p in WEB.glob(".env*"))
    if envs:
        return f"apps/web 下有 {', '.join(envs)}（next 会自动加载，截图拒绝运行；移走后重试）"
    if not shutil.which("node"):
        return "没有 node"
    if not (WEB / "node_modules/next").exists() or not (WEB / "node_modules/@playwright/test").exists():
        return "没有安装前端依赖（cd apps/web && npm ci）"
    if not (BFF / "node_modules/pg").exists():
        return "没有安装 BFF 依赖（cd services/bff && npm ci）"
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers or not any(Path(browsers).glob("chromium*")):
        return "本机没有 Playwright Chromium（PLAYWRIGHT_BROWSERS_PATH 未配置或为空；不在这里下载浏览器）"
    if (WEB / DIST_DIR).exists():
        return f"apps/web/{DIST_DIR} 已存在（另一个端到端或截图正在跑，或上次异常退出没清理）"
    return None


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(cond, what: str, timeout: float, proc: subprocess.Popen | None = None) -> None:
    deadline = time.time() + timeout
    while not cond():
        if proc is not None and proc.poll() is not None:
            raise StarmapError(f"{what}进程提前退出（退出码 {proc.returncode}）")
        if time.time() > deadline:
            raise StarmapError(f"{what}没有在 {timeout:.0f} 秒内就绪")
        time.sleep(0.25)


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310（本机回环）
            return r.status < 500
    except Exception:  # noqa: BLE001
        return False


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def current_commit() -> dict:
    return {"commit": _git("rev-parse", "HEAD"), "worktree_dirty": bool(_git("status", "--porcelain", "--untracked-files=no"))}


def _require_ephemeral(pg) -> None:
    """截图前置要在库里写登录号码与 bff_app 角色：只允许 pgharness.EphemeralPG 的一次性测试库（规则 9）"""
    from pgharness import EphemeralPG
    if not isinstance(pg, EphemeralPG) or getattr(pg, "db", None) != "starbuddy_test":
        raise StarmapError("星图截图只在 pgharness.EphemeralPG 的一次性库 starbuddy_test 上运行，拒绝写入其他库")


def seed_logins(pg, phones: dict[str, str]) -> None:
    """一次性库上：BFF 登录角色（只继承 starbuddy_bff）+ 合成家长的 +999 登录号码（同 db/seeds/dev/auth-synthetic.sql 的做法）"""
    _require_ephemeral(pg)
    values = ",\n".join(f"('{g}'::uuid, '{p}', now())" for g, p in sorted(phones.items(), key=lambda kv: kv[1]))
    pg.psql("CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff;\n"
            f"INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES\n{values};")


def load_facts(pg) -> dict:
    """管理员只读查询：已登记能力点（local_id → label_code）、各科 content_version、库里 active 选科"""
    catalog: dict[str, dict[str, str]] = {}
    versions: dict[str, set[str]] = {}
    for line in pg.psql("""SELECT n.subject::text, n.local_id, n.label_code, cv.content_version
                             FROM platform.skill_node n JOIN platform.curriculum_version cv ON cv.id = n.curriculum_version_id""").splitlines():
        if line.strip():
            subj, local, label, ver = line.split("|", 3)
            catalog.setdefault(subj, {})[local] = label
            versions.setdefault(subj, set()).add(ver)
    active: dict[str, list[str]] = {}
    for line in pg.psql("SELECT student_id::text, subject::text FROM platform.subject_enrollment WHERE status = 'active'").splitlines():
        if line.strip():
            sid, subj = line.split("|", 1)
            active.setdefault(sid, []).append(subj)
    return {"catalog": catalog, "content_versions": versions, "active": active}


def _base_env() -> dict:
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "PLAYWRIGHT_BROWSERS_PATH") if k in os.environ}


def _alive_group(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def stop_group(p: subprocess.Popen, grace: float = 10) -> None:
    """按进程组清理（npx → next → 子进程）：SIGTERM 整组，等到超时再 SIGKILL 整组"""
    pgid = p.pid  # start_new_session=True：进程组号 = 组长进程号
    if _alive_group(pgid):
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + grace
    while time.time() < deadline:
        if p.poll() is not None and not _alive_group(pgid):
            break
        time.sleep(0.1)
    if _alive_group(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _spawn(cmd: list[str], cwd: Path, env: dict, log: Path) -> subprocess.Popen:
    with open(log, "wb") as f:
        return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)


@contextmanager
def _proc(cmd: list[str], cwd: Path, env: dict, log: Path):
    p = _spawn(cmd, cwd, env, log)
    try:
        yield p
    finally:
        stop_group(p)


def _tail(log: Path, n: int = 15) -> str:
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


@contextmanager
def test_build(env: dict, work: Path, web: Path | None = None, timeout: float = 600):
    """测试构建到 .next-e2e（同 scripts/e2e.sh）。开头 os.mkdir 原子占位：已存在即失败（另一个端到端或截图在跑），
    只删自己建的目录；构建会改写 next-env.d.ts，结束时还原。构建进程按进程组清理。"""
    web = web or WEB
    dist = web / DIST_DIR
    try:
        os.mkdir(dist)
    except FileExistsError:
        raise StarmapError(f"apps/web/{DIST_DIR} 已存在（另一个端到端或截图正在跑），不占用也不删除") from None
    envdts = web / "next-env.d.ts"
    backup = envdts.read_bytes() if envdts.exists() else None
    try:
        log = work / "build.log"
        try:
            p = _spawn(["npx", "--no-install", "next", "build"], web, env, log)
        except OSError as e:
            raise StarmapError(f"无法启动前端测试构建：{e}") from None
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise StarmapError(f"前端测试构建超过 {timeout:.0f} 秒") from None
        finally:
            stop_group(p, grace=5)
        if rc != 0:
            print(_tail(log, 30), file=sys.stderr)
            raise StarmapError("前端测试构建失败（构建日志末尾见终端）")
        yield
    finally:
        if backup is not None:
            envdts.write_bytes(backup)
        else:
            envdts.unlink(missing_ok=True)
        shutil.rmtree(dist, ignore_errors=True)


test_build.__test__ = False  # 不是 pytest 用例


def run_capture(pg, plan: list[dict], phones: dict[str, str], out_dir: Path) -> list[dict]:
    """起 BFF 与前端、跑截图脚本；返回脚本输出的每张截图结果。任何一步失败抛 StarmapError。"""
    seed_logins(pg, phones)
    bff_port, web_port = _port(), _port()
    with tempfile.TemporaryDirectory(prefix="gate0-starmap.") as tmp:
        work = Path(tmp)
        inbox = work / "sms-inbox.jsonl"
        inbox.touch(mode=0o600)
        # 限流放宽的一份配置（同 scripts/e2e.sh：同一合成家长要登录多次）；只在 STARBUDDY_ENV=test 时 BFF 才认
        cfg = json.loads((BFF / "config/auth.json").read_text(encoding="utf-8"))
        for v in cfg["rate_limits"].values():
            v["limit"] = min(v["limit"] * 1000, 1000000)
        auth_cfg = work / "auth.json"
        auth_cfg.write_text(json.dumps(cfg), encoding="utf-8")
        bff_env = {**_base_env(), "STARBUDDY_ENV": "test", "PORT": str(bff_port),
                   "BFF_DATABASE_URL": f"postgresql://bff_app@/{pg.db}?host={pg.work}", "BFF_AUTH_CONFIG": str(auth_cfg),
                   "BFF_SHELL_ORIGIN": f"http://127.0.0.1:{web_port}", "BFF_SMS_CAPTURE_FILE": str(inbox)}
        web_env = {**_base_env(), "STARBUDDY_ENV": "test", "NEXT_TELEMETRY_DISABLED": "1", "STARBUDDY_NEXT_DIST_DIR": DIST_DIR,
                   "STARBUDDY_BFF_URL": f"http://127.0.0.1:{bff_port}"}
        bff_log, web_log = work / "bff.log", work / "web.log"
        with _proc(["node", "--no-warnings", "src/main.ts"], BFF, bff_env, bff_log) as bff:
            try:
                _wait(lambda: _port_open(bff_port), "BFF ", 30, bff)
            except StarmapError:
                print(_tail(bff_log), file=sys.stderr)
                raise
            with test_build(web_env, work), \
                 _proc(["npx", "--no-install", "next", "start", "-p", str(web_port), "-H", "127.0.0.1"], WEB, web_env, web_log) as web:
                try:
                    _wait(lambda: _http_ok(f"http://127.0.0.1:{web_port}/login"), "前端 ", 60, web)
                except StarmapError:
                    print(_tail(web_log), file=sys.stderr)
                    raise
                spec = {"base": f"http://127.0.0.1:{web_port}", "bff": f"http://127.0.0.1:{bff_port}", "inbox": str(inbox),
                        "out_dir": str(out_dir), "viewport": VIEWPORT, "image": IMAGE,
                        "shots": [{k: s[k] for k in ("name", "kind", "student_id", "phone", "path", "other_student_id", "other_path")}
                                  for s in plan]}
                plan_file = work / "plan.json"
                plan_file.write_text(json.dumps(spec), encoding="utf-8")
                r = subprocess.run(["node", "--no-warnings", SCRIPT, str(plan_file)], cwd=WEB, env=_base_env(),
                                   capture_output=True, text=True, timeout=600)
                if r.returncode != 0:
                    # BFF 与前端日志只打到终端帮助排查，不进证据包
                    print(f"--- BFF 日志（最后 15 行）\n{_tail(bff_log)}\n--- 前端日志（最后 15 行）\n{_tail(web_log)}", file=sys.stderr)
                    raise StarmapError(f"截图脚本失败：{r.stderr.strip()[-1500:]}")
                return json.loads(r.stdout.strip().splitlines()[-1])["shots"]


def capture(pg, report, nodes_csv: str, out_dir: Path, n: int = DEFAULT_STUDENTS) -> dict:
    """完整一步：返回 starmap.json 的记录（status = captured / failed，失败附原因）；截图写到 out_dir/starmap/。"""
    clean(out_dir)
    d = out_dir / "starmap"
    d.mkdir(parents=True, exist_ok=True)
    commit = current_commit()
    try:
        why = missing_prerequisites()
        if why:
            return failed_record(n, why, commit)
        _require_ephemeral(pg)
        plan, phones = plan_shots(report.cohort, n)
        facts = {**load_facts(pg), "commit": commit}
        results = run_capture(pg, plan, phones, d)
        return build_record(plan, results, nodes_csv, facts, n)
    except Exception as e:  # noqa: BLE001 —— 任何失败都记 failed 并附原因，不伪装成功
        return failed_record(n, f"{type(e).__name__}: {e}"[:4000], commit)
