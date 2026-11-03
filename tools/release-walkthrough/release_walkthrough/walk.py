"""走查本体：一次性本地 Postgres（services/learning/tests/pgharness.EphemeralPG：Unix socket、不监听 TCP、结束即删除）上，
两名合成教研经审核台后端（进程内，默认的 staff 登录会话认证，真实口令 + TOTP 登录）走完：

  1. 地图版本：合成数学地图 1.0.0（知识服务身份 starbuddy_kge 建草稿）→ 两名 staff 看差异、签字 → 发布；
  2. 内容版本登记：阿鸦内容包的登记 SQL（adapters/aya/scripts/register-content.ts，以学习服务身份 starbuddy_learning 执行，只增不改），
     草稿指向第 1 步的地图版本；
  3. 内容版本签字与发布（审核台接口，签字人回传看到的摘要）；
  4. 准入变更：5 道题经“签字 → 执行”走到 admitted / pending_review（来源已核与未核各一）/ rejected，以及 admitted 后降为 rejected；
  5. 导出版本记录（审核台 /review/v0/records，含 PR D 的 content_version、admission 行）；
  6. 导出阿鸦准入决定文件（python -m review_console.admission_export，审核台读身份 + 迁移 0042 只读函数）；
  7. 阿鸦离线重建内容包（adapters/aya：npm run rebuild:pack 同一脚本，输出到产物目录，不改仓库里的内容包）；
  8. 核对重建后的内容包逐题不比平台宽（admission_export verify）。

只用合成 staff、合成地图、合成来源编号（SYN-SRC-*）；题目是阿鸦内容包里的公共题号（不含学生数据，规则 8、13）。
只连本机一次性实例（规则 9），不调模型。产物写到 out 目录（原样）；normalized/ 下是去掉每次运行会变的值之后的样例。
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
AYA = ROOT / "adapters/aya"
LEARNING = ROOT / "services/learning"
VENV_PY = LEARNING / ".venv/bin/python"

# 固定的合成 ID（规则 8）：两名合成教研、合成数学地图版本
T1 = uuid.UUID("5f7a0000-0000-4000-8000-0000000000a1")
T2 = uuid.UUID("5f7a0000-0000-4000-8000-0000000000a2")
MAP_ID = uuid.UUID("c0a70000-0000-4000-8000-0000000000a7")
STAFF = {T1: ("syn.walk.t1", "synthetic-walk-password-t1", bytes(range(31, 51))),
         T2: ("syn.walk.t2", "synthetic-walk-password-t2", bytes(range(61, 81)))}
FIXED_IDS = {str(T1), str(T2), str(MAP_ID)}
KGE_LOGIN, SIGN_LOGIN = "walk_kge_app", "walk_sign_app"

MAP_SQL = f"""
SET ROLE starbuddy_kge;
INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ('{MAP_ID}', 'aya', 2022, '1.0.0');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES
  ('{MAP_ID}', 'MATH.SYNWT', 'domain', NULL, NULL, '合成走查领域', '{{}}', '{{}}', '{{}}', '{{}}'),
  ('{MAP_ID}', 'MATH.SYNWT.T', 'topic', 'MATH.SYNWT', NULL, '合成走查主题', '{{}}', '{{}}', '{{}}', '{{}}'),
  ('{MAP_ID}', 'MATH.SYNWT.T.A', 'node', 'MATH.SYNWT.T', 'kp', '合成走查节点', '{{合成包含}}', '{{合成不包含}}', '{{合成题例}}', '{{understand}}');
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
  VALUES ('{MAP_ID}', 'MATH.SYNWT.T.A', 'syn-2024', 2024, 'v', 'c', 1, 1);
RESET ROLE;
"""


def totp(secret: bytes, step: int) -> str:
    """RFC 6238 的 6 位码（与 services/learning/tests/test_review_console.py 同一实现；库里由 platform.staff_totp 核对）。"""
    h = hmac.new(secret, step.to_bytes(8, "big"), hashlib.sha1).digest()
    o = h[19] & 15
    return f"{(int.from_bytes(h[o:o + 4], 'big') & 0x7FFFFFFF) % 1_000_000:06d}"


def missing_prerequisites() -> str | None:
    if not VENV_PY.exists():
        return "没有学习服务依赖（见 services/learning/README.md）"
    if not (AYA / "node_modules/ajv").exists():
        return "没有安装阿鸦适配器依赖（cd adapters/aya && npm ci）"
    return None


def admission_plan(item_ids: list[str]) -> list[dict[str, Any]]:
    """按题号排序取前 5 道，确定性地安排准入变化（签字人与执行人交替；来源编号全部是合成编号）。"""
    a, b, c, d, e = sorted(item_ids)[:5]
    src = lambda n: {"source_ref": f"SYN-SRC-WALK-{n:04d}", "source_type": "self_authored", "license_status": "cleared"}  # noqa: E731
    return [
        {"item_id": a, "to": "pending_review", "review_level": "Q1", **src(1), "signer": T1, "applier": T2},
        {"item_id": a, "to": "admitted", "review_level": "Q2", "license_status": "cleared", "signer": T2, "applier": T1},
        {"item_id": b, "to": "pending_review", "review_level": "Q1", **src(2), "signer": T1, "applier": T2},
        {"item_id": c, "to": "rejected", "review_level": None, "source_type": "unknown", "license_status": "denied", "signer": T2, "applier": T1},
        {"item_id": d, "to": "pending_review", "review_level": None, "signer": T1, "applier": T2},  # 来源未核：导出时回到缺省
        {"item_id": e, "to": "pending_review", "review_level": "Q1", **src(5), "signer": T2, "applier": T1},
        {"item_id": e, "to": "admitted", "review_level": "Q3", "license_status": "cleared", "signer": T1, "applier": T2},
        {"item_id": e, "to": "rejected", "review_level": None, "basis_code": "content_error", "signer": T2, "applier": T1},  # 降级
    ]


class Walk:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.steps: list[dict[str, Any]] = []
        self.tokens: dict[uuid.UUID, str] = {}

    def step(self, name: str, **facts: Any) -> None:
        self.steps.append({"step": name, **facts})
        print(f"  {name}: {json.dumps(facts, ensure_ascii=False, default=str)}", file=sys.stderr)

    def h(self, who: uuid.UUID) -> dict[str, str]:
        return {"x-review-session": self.tokens[who]}

    async def call(self, c, method: str, path: str, who: uuid.UUID | None = None, expect: int = 200, **kw) -> Any:
        r = await c.request(method, path, headers=self.h(who) if who else None, **kw)
        if r.status_code != expect:
            raise RuntimeError(f"{method} {path} → {r.status_code}（期望 {expect}）：{r.text[:300]}")
        return r.json()


def _seed(pg) -> None:
    lines = [f"CREATE ROLE {KGE_LOGIN} LOGIN IN ROLE starbuddy_kge;", f"CREATE ROLE {SIGN_LOGIN} LOGIN IN ROLE starbuddy_review;"]
    for acc, (login, password, secret) in STAFF.items():
        lines.append(f"INSERT INTO platform.account (id, kind) VALUES ('{acc}', 'staff');")
        lines.append(f"SELECT platform.staff_enroll('{acc}', '{login}', '{password}', '\\x{secret.hex()}'::bytea);")
    pg.psql("\n".join(lines))


def _register(pg) -> None:
    sql = subprocess.run(["node", "--no-warnings", "scripts/register-content.ts"], cwd=AYA, check=True, capture_output=True).stdout.decode()
    pg.psql("SET ROLE starbuddy_learning;\n" + sql)
    # 草稿指向已发布的合成地图版本（学习服务对草稿的 map_version_id 有列级 UPDATE，0039）
    pg.psql(f"SET ROLE starbuddy_learning;\nUPDATE platform.curriculum_version SET map_version_id = '{MAP_ID}'"
            " WHERE subject = 'aya' AND status = 'draft';")


def _db_url(pg, user: str) -> str:
    return f"postgresql://{user}@/{pg.db}?host={pg.work}"


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} … 退出码 {r.returncode}：{r.stderr.strip()[-600:]}")
    return r


async def _review_flow(pg, w: Walk) -> dict[str, Any]:
    import asyncpg
    import httpx

    from review_console.__main__ import write_records
    from review_console.app import create_app

    kp = await asyncpg.create_pool(**pg.dsn(KGE_LOGIN), min_size=1, max_size=4)
    rp = await asyncpg.create_pool(**pg.dsn(SIGN_LOGIN), min_size=1, max_size=2)
    try:
        app = create_app(pools=(kp, rp))  # 默认认证：staff 登录会话（不注入测试认证器）
        async with app.router.lifespan_context(app), \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            for acc, (login, password, secret) in STAFF.items():
                tok = await w.call(c, "POST", "/review/v0/auth/login",
                                   json={"login_name": login, "password": password, "code": totp(secret, int(time.time()) // 30)})
                w.tokens[acc] = tok["token"]
            w.step("staff_login", staff=[str(a) for a in STAFF])

            # 1. 地图版本：差异 → 两人签字（首次发布是结构性，需两名不同 staff）→ 发布
            pg.psql(MAP_SQL)
            d = await w.call(c, "GET", f"/review/v0/map-versions/{MAP_ID}/diff", T1)
            for who in (T1, T2):
                await w.call(c, "POST", f"/review/v0/map-versions/{MAP_ID}/sign", who,
                             json={"decision": "approved", "expected_digest": d["digest"]})
            pub = await w.call(c, "POST", f"/review/v0/map-versions/{MAP_ID}/publish", T2, json={"expected_digest": d["digest"]})
            w.step("map_publish", map_version_id=str(MAP_ID), version="1.0.0", required_signers=d["summary"]["required_signers"],
                   validation=sorted({(i["check_name"], i["severity"]) for i in d["validation"]}), published=bool(pub["published_at"]))

            # 2. 内容版本登记（草稿）
            _register(pg)
            cvs = (await w.call(c, "GET", "/review/v0/content-versions", T1, params={"subject": "aya"}))["items"]
            cv = next(x for x in cvs if x["status"] == "draft")
            w.step("content_register", six_segment=cv["six_segment"], skill_nodes=cv["skill_nodes"], items=cv["items"],
                   map_version_id=str(cv["map_version_id"]))

            # 3. 内容版本签字与发布（签字人回传看到的摘要；发布人与签字人不同）
            cd = await w.call(c, "GET", f"/review/v0/content-versions/{cv['id']}/diff", T1)
            blocking = [x["check_name"] for x in cd["checks"] if x["severity"] == "error"]
            if blocking:
                raise RuntimeError(f"内容版本发布前提不满足：{blocking}")
            await w.call(c, "POST", f"/review/v0/content-versions/{cv['id']}/sign", T1,
                         json={"decision": "approved", "expected_digest": cd["digest"]})
            await w.call(c, "POST", f"/review/v0/content-versions/{cv['id']}/publish", T2, json={"expected_digest": cd["digest"]})
            w.step("content_publish", six_segment=cv["six_segment"], checks=[(x["check_name"], x["severity"]) for x in cd["checks"]],
                   changes=len(cd["changes"]), signer=str(T1), publisher=str(T2))

            # 4. 准入变更：每一步“签字（回传看到的题目登记摘要）→ 另一名 staff 执行”
            plan = admission_plan([i["item_id"] for i in cd["items"]])
            since = None
            for p in plan:
                items = {i["item_id"]: i for i in (await w.call(c, "GET", f"/review/v0/content-versions/{cv['id']}/diff", T1))["items"]}
                before = items[p["item_id"]]
                body = {"subject": "aya", "item_id": p["item_id"], "to_admission": p["to"], "decision": "approved",
                        "review_level": p.get("review_level"), "source_ref": p.get("source_ref"), "source_type": p.get("source_type"),
                        "license_status": p.get("license_status"), "expected_digest": before["admission_digest"],
                        "basis_code": p.get("basis_code", "walkthrough")}
                rid = (await w.call(c, "POST", "/review/v0/admission/sign", p["signer"], json=body))["review_id"]
                ap = await w.call(c, "POST", "/review/v0/admission/apply", p["applier"], json={"review_ids": [rid]})
                since = since or ap["recompute_since"]
                w.step("admission_change", item_id=p["item_id"], **{"from": before["admission"]}, to=p["to"], applied=ap["applied"])

            # 5. 版本记录导出（审核台接口与命令行同一函数 service.version_record）
            recs = (await w.call(c, "GET", "/review/v0/records", T1, params={"subject": "aya"}))["items"]
            (w.out / "version-record.json").write_text(json.dumps(recs, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
            buf = io.StringIO()
            write_records(recs, "csv", buf)
            (w.out / "version-record.csv").write_text(buf.getvalue(), encoding="utf-8")
            lines = sorted({(r["line"], r["action"]) for r in recs})
            w.step("version_record", rows=len(recs), line_actions=[list(x) for x in lines])
            for want in (("map", "review"), ("map", "publish"), ("content_version", "review"), ("content_version", "publish"),
                         ("admission", "review"), ("admission", "apply")):
                if want not in lines:
                    raise RuntimeError(f"版本记录里缺少 {want} 行")
            return {"content_version_id": cv["id"], "recompute_since": since}
    finally:
        await kp.close()
        await rp.close()


def _export_rebuild_verify(pg, w: Walk) -> dict[str, Any]:
    env = {"REVIEW_CONSOLE_ENV": "test", "REVIEW_CONSOLE_KGE_DATABASE_URL": _db_url(pg, KGE_LOGIN), "PYTHONDONTWRITEBYTECODE": "1"}
    dec, rep = w.out / "admission-decisions.json", w.out / "admission-export-report.json"
    r = _run([str(VENV_PY), "-m", "review_console.admission_export", "export", "--pack", str(AYA / "content/pack.json"),
              "--out", str(dec), "--report", str(rep)], LEARNING, env)
    report = json.loads(rep.read_text(encoding="utf-8"))
    w.step("admission_export", decisions=report["decisions"], exported=report["exported"],
           pending_review_as_default=report["pending_review_as_default"], note=r.stderr.strip().splitlines()[-1])

    rebuilt, c9 = w.out / "pack.rebuilt.json", w.out / "c9-report.json"
    r = _run(["node", "--no-warnings", "scripts/rebuild-content-pack.ts", "--pack", str(AYA / "content/pack.json"),
              "--decisions", str(dec), "--report", str(c9), "--out", str(rebuilt)], AYA, {"PYTHON": str(VENV_PY)})
    pack = json.loads(rebuilt.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for it in pack["items"]:
        counts[it["admission"]] = counts.get(it["admission"], 0) + 1
    w.step("pack_rebuild", pack_version=pack["pack_version"], admission=dict(sorted(counts.items())),
           admission_decisions=pack["admission_decisions"], hint_scan_flagged=len(pack["hint_scan"]["flagged_items"]))

    v = _run([str(VENV_PY), "-m", "review_console.admission_export", "verify", "--pack", str(rebuilt)], LEARNING, env)
    verify = json.loads(v.stdout)
    (w.out / "admission-verify.json").write_text(v.stdout, encoding="utf-8")
    w.step("pack_verify", ok=verify["ok"], same=verify["same"], stricter=verify["stricter"], violations=verify["violation_count"])
    if not verify["ok"]:
        raise RuntimeError("重建后的内容包有比平台宽的准入")
    summary = {k: pack[k] for k in ("format", "pack_version", "admission_decisions", "curriculum")}
    summary["admission"] = dict(sorted(counts.items()))
    summary["items_with_decision"] = [{k: it.get(k) for k in ("id", "admission", "source", "admission_review")}
                                      for it in sorted(pack["items"], key=lambda i: i["id"]) if "admission_review" in it]
    summary["hint_scan"] = {k: pack["hint_scan"][k] for k in ("mode", "flagged_items")}
    (w.out / "pack-rebuild-summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return summary


async def run(out: Path) -> dict[str, Any]:
    for p in (LEARNING, LEARNING / "tests"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from pgharness import EphemeralPG

    out.mkdir(parents=True, exist_ok=True)
    w = Walk(out)
    pg = EphemeralPG()
    try:
        pg.start()
        _seed(pg)
        flow = await _review_flow(pg, w)
        _export_rebuild_verify(pg, w)
    finally:
        pg.stop()
    result = {"format": "release-walkthrough/1", "subject": "aya", "steps": w.steps, "content_version_id": flow["content_version_id"],
              "note": "合成走查：合成 staff、合成数学地图、合成来源编号；一次性本地库，不是 pilot 发布记录"}
    (out / "walkthrough.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=1, default=str) + "\n", encoding="utf-8")
    return result


NORMALIZED = ("walkthrough.json", "version-record.json", "version-record.csv", "admission-decisions.json", "admission-export-report.json",
              "admission-verify.json", "pack-rebuild-summary.json")


def normalize_dir(out: Path) -> dict[str, str]:
    """按固定顺序规范化产物（占位符按第一次出现编号，顺序固定才可比）；返回 文件名 → 规范化文本。"""
    from .normalize import Normalizer
    n = Normalizer(FIXED_IDS)
    res: dict[str, str] = {}
    for name in NORMALIZED:
        text = (out / name).read_text(encoding="utf-8")
        if name.endswith(".csv"):
            rows = list(csv.reader(io.StringIO(text)))
            buf = io.StringIO()
            csv.writer(buf, lineterminator="\n").writerows([[n.text(x) for x in r] for r in rows])
            res[name] = buf.getvalue()
        else:
            res[name] = json.dumps(n.value(json.loads(text)), ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    return res


def main_run(out: Path) -> int:
    why = missing_prerequisites()
    if why:
        print(f"release-walkthrough: 无法运行（{why}）", file=sys.stderr)
        return 2
    asyncio.run(run(out))
    return 0
