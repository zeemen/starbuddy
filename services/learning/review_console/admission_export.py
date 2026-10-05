"""阿鸦准入决定文件的平台导出（specs/release-gate-sprint4 第 7 节、待人确认 #9；第二部分 PR E）。

准入的唯一真相源是 platform.item.admission（0003、0039）；阿鸦内容包里的准入是副本。本模块从平台导出“准入决定文件”
（格式 aya-admission-decisions/1，阿鸦格式 3 已定，见 adapters/aya/README.md“内容包格式 3”），由人执行阿鸦的离线重建：

  REVIEW_CONSOLE_ENV=dev REVIEW_CONSOLE_KGE_DATABASE_URL=... \\
    python -m review_console.admission_export export --pack ../../adapters/aya/content/pack.json --out <决定文件> [--report <报告>] [--replace]
  cd adapters/aya && npm run rebuild:pack -- --decisions <决定文件>

  REVIEW_CONSOLE_ENV=dev REVIEW_CONSOLE_KGE_DATABASE_URL=... \\
    python -m review_console.admission_export verify --pack <重建后的内容包>

**发布门禁**：重建内容包之后必须跑 verify 且退出码为 0，才能发布（提交、合入、部署）重建后的内容包；未跑 verify 不得发布。

读库只用审核台已有的读身份 starbuddy_kge（连上后核对只继承这一个应用角色）与迁移 0042 的只读函数
（platform.content_version_list / content_version_snapshot / release_record_export），在一个只读、可重复读的事务里读，
不新增任何写入路径，不放宽任何授权；适配器不连库（规则 1）。只连本机数据库（规则 9）：连接前用审核台命令行的 local_only
核对连接串里的全部主机，连上后用 check_server_local 核对服务端地址（与时政下架任务同一套核对，迁移 0043 / PR D）。

退出码：0 成功（verify：逐题不比平台宽）；1 平台数据不能安全导出，或 verify 有比平台宽的题；
2 输入不对（内容包、平台快照的形状或枚举，输出文件已存在等）、不是本机库、角色不对或库拒绝。只输出简短说明，不打堆栈。

导出规则——副本只能比平台更严（spec 第 7 节“共享核对库保证副本落后时只会更严”）：
- 只导出与内容包六段标识相同、且在平台上“已发布”的内容版本；草稿或已撤下的版本拒绝导出（草稿里的 admitted 题对学生不可见，
  副本不能先于平台生效）。
- admitted：须有最近一次准入执行记录（release_record，action = apply）且目标就是 admitted、审核等级 ≥ Q2、来源已核；缺任何一项
  是平台数据不自洽，整份拒绝导出，不降级、不猜。
- rejected：须有目标为 rejected 的最近执行记录；缺就拒绝导出（缺省值 source_unverified 不比 rejected 严，不能用省略代替）。
- pending_review：阿鸦格式 3 要求来源已核与审核记录，平台（0039）不要求来源已核。两者都满足时照原样导出；
  否则不写这道题，重建时回到缺省 source_unverified（与 pending_review 一样不被当作已准入，更严），报告里逐题列出。
- source_unverified：不写（就是缺省值）。
- 审核记录编号写所依据的准入审核记录 ID（执行记录的 applies_review_id），不写签字人；文件只含题号、编号与枚举（spec 待人确认 #15）。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

FORMAT = "aya-admission-decisions/1"
PACK_FORMAT = "aya-content-pack/3"
REPORT_FORMAT = "aya-admission-export-report/1"
VERIFY_FORMAT = "aya-admission-verify/1"
SUBJECT = "aya"
CURRICULUM_KEYS = ("region", "exam_year", "grade", "textbook_version", "subject", "content_version")
REVIEW_LEVELS = ("Q0", "Q1", "Q2", "Q3", "Q4", "Q5")
MIN_ADMITTED_REVIEW_LEVEL = "Q2"  # 与阿鸦 admission.ts、迁移 0039 platform.admission_min_review_rank 一致（单测核对）
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$")  # 与阿鸦 REF_PATTERN、0039 source_ref 一致
SOURCE_TYPES = ("unknown", "self_authored", "adapted", "textbook", "exam_paper", "reference_book")
LICENSE_STATUSES = ("unverified", "cleared", "denied")
# 从严到宽：rejected < source_unverified < pending_review < admitted。副本的准入不能比平台宽
LENIENCY = {"rejected": 0, "source_unverified": 1, "pending_review": 2, "admitted": 3}
VERSION_STATUSES = ("draft", "published", "retired")  # 0039 curriculum_version.status
INT_KEYS = ("exam_year", "grade")
MAX_LISTED = 50
LOCAL_ONLY = "准入决定导出只连本机数据库（localhost、回环地址或 Unix socket）"


class ExportError(Exception):
    """平台数据不能安全地导出（不自洽，或会让副本比平台宽）。problems 是逐条说明（只含题号与枚举）。main 退出码 1。"""

    def __init__(self, summary: str, problems: Iterable[str] = ()) -> None:
        self.problems = list(problems)
        super().__init__(summary)


class InputError(ExportError):
    """输入的形状或枚举不对（内容包、平台快照、输出路径）：在比较、读库之前就拒。main 退出码 2，只给简短说明。"""


class NotLocalError(Exception):
    """连接串或连上的服务端不是本机（规则 9）。main 退出码 2。"""


def _check_curriculum(c: Any, where: str) -> None:
    if not isinstance(c, dict):
        raise InputError(f"{where} 缺 curriculum（六段标识）")
    missing = [k for k in CURRICULUM_KEYS if k not in c]
    if missing:
        raise InputError(f"{where} 的六段标识缺 {', '.join(missing)}")
    for k in CURRICULUM_KEYS:
        v = c[k]
        ok = (isinstance(v, int) and not isinstance(v, bool)) if k in INT_KEYS else (isinstance(v, str) and v != "")
        if not ok:
            raise InputError(f"{where} 的六段标识 {k} 类型不对")


def _check_items(items: Any, where: str, id_key: str) -> None:
    if not isinstance(items, list):
        raise InputError(f"{where} 缺题目列表")
    seen: set[str] = set()
    for n, it in enumerate(items, 1):
        iid = it.get(id_key) if isinstance(it, dict) else None
        if not isinstance(iid, str) or not iid:
            raise InputError(f"{where} 第 {n} 道题缺题号（{id_key}）")
        if iid in seen:
            raise InputError(f"{where} 题号重复：{iid}")
        seen.add(iid)
        if "admission" not in it:
            raise InputError(f"{where} 题目 {iid} 缺 admission")
        if it["admission"] not in LENIENCY:
            raise InputError(f"{where} 题目 {iid} 的 admission 不是已知取值（{'、'.join(LENIENCY)}）")


def validate_pack(pack: Any, where: str = "内容包") -> dict[str, Any]:
    """阿鸦内容包格式 3 里导出与核对要用的部分：格式、六段标识（科目须是阿鸦）、题号唯一、准入是已知取值。不对就抛 InputError。"""
    if not isinstance(pack, dict) or pack.get("format") != PACK_FORMAT:
        raise InputError(f"{where} 不是阿鸦内容包格式 3")
    _check_curriculum(pack.get("curriculum"), where)
    if pack["curriculum"]["subject"] != SUBJECT:
        raise InputError(f"{where} 不是阿鸦（数学）内容包")
    _check_items(pack.get("items"), where, "id")
    return pack


def validate_snapshot(snapshot: Any) -> dict[str, Any]:
    """平台快照（0042 content_version_snapshot）里要用的部分：内容版本 ID、六段标识、状态、题号与准入都是已知取值。"""
    v = snapshot.get("version") if isinstance(snapshot, dict) else None
    if not isinstance(v, dict) or v.get("id") is None:
        raise InputError("平台快照缺内容版本")
    _check_curriculum(v, "平台内容版本")
    if v.get("status") not in VERSION_STATUSES:
        raise InputError(f"平台内容版本的状态不是已知取值（{'、'.join(VERSION_STATUSES)}）")
    _check_items(snapshot.get("items"), "平台快照", "item_id")
    return snapshot


def _verified(item: dict[str, Any]) -> bool:
    return item.get("source_ref") is not None and item.get("source_type") not in (None, "unknown") and item.get("license_status") == "cleared"


def _rank(level: str | None) -> int:
    return REVIEW_LEVELS.index(level) if level in REVIEW_LEVELS else -1


def six_segment(c: dict[str, Any]) -> str:
    return "/".join(str(c[k]) for k in CURRICULUM_KEYS)


def pick_version(versions: list[dict[str, Any]], curriculum: dict[str, Any]) -> dict[str, Any]:
    """按内容包的六段标识找平台内容版本（六段组合在平台唯一，0003）。"""
    want = {k: curriculum[k] for k in CURRICULUM_KEYS}
    hit = [v for v in versions if {k: (v[k] if k != "subject" else str(v[k])) for k in CURRICULUM_KEYS} == want]
    if not hit:
        raise ExportError(f"平台上没有六段标识为 {six_segment(want)} 的内容版本")
    return hit[0]


def latest_applies(records: list[dict[str, Any]], cv_id: str) -> dict[str, dict[str, Any]]:
    """每道题最近一条准入执行记录（按执行时间排，不依赖输入顺序）。同一道题有两条执行时间相同的记录时分不出哪条在后
    （ID 是随机 UUID，不表示先后），整份拒绝导出，不猜。"""
    rows = [r for r in records if r.get("line") == "admission" and r.get("action") == "apply" and str(r.get("curriculum_version_id")) == cv_id]
    if any(r.get("decided_at") is None or r.get("item_id") is None for r in rows):
        raise ExportError("准入执行记录缺执行时间或题号")
    rows.sort(key=lambda r: (r["decided_at"], str(r["id"])))
    out: dict[str, dict[str, Any]] = {}
    ties: list[str] = []
    for r in rows:
        iid = str(r["item_id"])
        prev = out.get(iid)
        if prev is not None and prev["decided_at"] == r["decided_at"] and iid not in ties:
            ties.append(iid)
        out[iid] = r
    if ties:
        raise ExportError(f"有 {len(ties)} 道题的准入执行记录执行时间相同、分不出先后",
                          [f"题目 {x} 有多条执行时间相同的准入执行记录" for x in sorted(ties)])
    return out


def build_decisions(snapshot: dict[str, Any], records: list[dict[str, Any]], curriculum: dict[str, Any], pack_item_ids: set[str],
                    exported_at: dt.datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    """纯函数：平台快照（0042 content_version_snapshot）+ 发布记录（0042 release_record_export）→（决定文件, 导出报告）。
    任何会让副本比平台宽、或平台数据不自洽的情况都抛 ExportError，不输出半份文件。"""
    validate_snapshot(snapshot)
    _check_curriculum(curriculum, "内容包")
    v = snapshot["version"]
    seg = {k: v[k] for k in CURRICULUM_KEYS}
    if str(seg["subject"]) != SUBJECT:
        raise ExportError("只导出阿鸦（数学）内容版本")
    if seg != {k: curriculum[k] for k in CURRICULUM_KEYS}:
        raise ExportError(f"平台内容版本 {six_segment(seg)} 与内容包 {six_segment(curriculum)} 的六段标识不一致")
    if v["status"] != "published":
        raise ExportError(f"内容版本 {six_segment(seg)} 在平台上是 {v['status']}，只有已发布的内容版本才导出准入决定（副本不能先于平台生效）")
    cv_id = str(v["id"])
    applies = latest_applies(records, cv_id)
    problems: list[str] = []
    as_default: list[str] = []
    decisions: list[dict[str, Any]] = []
    platform_counts: dict[str, int] = {}
    items = sorted(snapshot["items"], key=lambda i: i["item_id"])
    unknown = [i["item_id"] for i in items if i["item_id"] not in pack_item_ids]
    if unknown:
        problems += [f"题目 {x} 在平台登记里、不在内容包里（内容包与平台登记不是同一份）" for x in unknown]
    for it in items:
        iid, adm = it["item_id"], it["admission"]
        platform_counts[adm] = platform_counts.get(adm, 0) + 1
        if adm == "source_unverified":
            continue
        if it.get("source_type") not in SOURCE_TYPES or it.get("license_status") not in LICENSE_STATUSES or \
                (it.get("source_ref") is not None and not REF.match(it["source_ref"])):
            problems.append(f"题目 {iid} 的来源字段不合格式")
            continue
        a = applies.get(iid)
        consistent = a is not None and a.get("to_admission") == adm and a.get("applies_review_id") is not None
        level = a.get("review_level") if a else None
        if adm == "pending_review":
            if not (consistent and _verified(it)):
                as_default.append(iid)  # 阿鸦格式 3 表达不了（来源未核或没有执行记录）：回到缺省，更严
                continue
        elif adm == "admitted":
            if not consistent:
                problems.append(f"题目 {iid} 在平台上是 admitted，但最近一条准入执行记录不是升为 admitted")
                continue
            if not _verified(it):
                problems.append(f"题目 {iid} 在平台上是 admitted，但来源未核")
                continue
            if _rank(level) < _rank(MIN_ADMITTED_REVIEW_LEVEL):
                problems.append(f"题目 {iid} 在平台上是 admitted，但执行记录的审核等级低于 {MIN_ADMITTED_REVIEW_LEVEL}")
                continue
        elif adm == "rejected":
            if not consistent:
                problems.append(f"题目 {iid} 在平台上是 rejected，但最近一条准入执行记录不是改为 rejected")
                continue
        else:
            problems.append(f"题目 {iid} 的准入状态未知")
            continue
        if level is not None and level not in REVIEW_LEVELS:
            problems.append(f"题目 {iid} 的审核等级未知")
            continue
        decisions.append({"item_id": iid, "admission": adm, "source_ref": it.get("source_ref"), "source_type": it["source_type"],
                          "license_status": it["license_status"], "review_level": level,
                          "review_ids": [str(a["applies_review_id"])]})  # type: ignore[index]
    if problems:
        raise ExportError(f"平台准入数据不能安全导出（{len(problems)} 处）", problems)
    at = exported_at.astimezone(dt.timezone.utc).replace(microsecond=0)
    exported_at_s = at.strftime("%Y-%m-%dT%H:%M:%SZ")
    export_ref = f"AYA-ADM-{at.strftime('%Y%m%dT%H%M%SZ')}"
    file = {"format": FORMAT, "export_ref": export_ref, "exported_at": exported_at_s,
            "curriculum": {k: curriculum[k] for k in CURRICULUM_KEYS}, "decisions": decisions}
    exported_counts: dict[str, int] = {}
    for d in decisions:
        exported_counts[d["admission"]] = exported_counts.get(d["admission"], 0) + 1
    report = {"format": REPORT_FORMAT, "export_ref": export_ref, "exported_at": exported_at_s, "content_version_id": cv_id,
              "six_segment": six_segment(seg), "status": v["status"], "map_version_id": _s(v.get("map_version_id")),
              "platform_items": len(items), "platform_admission": dict(sorted(platform_counts.items())),
              "exported": dict(sorted(exported_counts.items())), "decisions": len(decisions),
              "pending_review_as_default": as_default[:MAX_LISTED], "pending_review_as_default_count": len(as_default)}
    return file, report


def check_not_more_lenient(pack: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """重建后的内容包与平台逐题比较：内容包里每道题的准入都不能比平台宽（平台没登记的题按 source_unverified 算，
    也就是内容包里不能是 pending_review 或 admitted）。平台内容版本不是已发布（草稿、已撤下）时，学生看不到它的准入，
    内容包里比 source_unverified 宽的题一律不合格（reason = version_not_published）。返回报告；violations 非空即不合格。
    比较前先校验两边的形状与枚举（InputError）。"""
    validate_pack(pack)
    validate_snapshot(snapshot)
    seg = snapshot["version"]
    if {k: seg[k] for k in CURRICULUM_KEYS} != {k: pack["curriculum"][k] for k in CURRICULUM_KEYS}:
        raise InputError(f"平台内容版本 {six_segment(seg)} 与内容包 {six_segment(pack['curriculum'])} 的六段标识不一致")
    published = seg["status"] == "published"
    floor = LENIENCY["source_unverified"]
    platform = {i["item_id"]: i["admission"] for i in snapshot["items"]}
    violations, stricter, same = [], 0, 0
    for it in sorted(pack["items"], key=lambda i: i["id"]):
        p = platform.get(it["id"], "source_unverified")
        a = it["admission"]
        if not published and LENIENCY[a] > floor:
            violations.append({"item_id": it["id"], "pack": a, "platform": p, "reason": "version_not_published"})
        elif LENIENCY[a] > LENIENCY[p]:
            violations.append({"item_id": it["id"], "pack": a, "platform": p, "reason": "more_lenient_than_platform"})
        elif a == p:
            same += 1
        else:
            stricter += 1
    note = None if published else (f"内容版本在平台上是 {seg['status']}，不是已发布：比 source_unverified 宽的题"
                                   "（pending_review、admitted）一律不合格")
    return {"format": VERIFY_FORMAT, "six_segment": six_segment(seg), "status": seg["status"], "note": note,
            "pack_version": pack.get("pack_version"), "items": len(pack["items"]), "same": same, "stricter": stricter,
            "violations": violations[:MAX_LISTED], "violation_count": len(violations), "ok": not violations}


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def dumps(obj: Any) -> str:
    """与阿鸦内容包同一种规范化写法：键排序、缩进 1、结尾换行。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def load_pack(path: str) -> dict[str, Any]:
    """读内容包并校验（validate_pack）；JSON 解析不了也算输入不对。"""
    try:
        with open(path, encoding="utf-8") as f:
            pack = json.load(f)
    except ValueError:
        raise InputError(f"内容包 {path} 不是合法的 JSON") from None
    return validate_pack(pack, f"内容包 {path}")


# ---------- 读库：审核台的读身份 starbuddy_kge + 0042 只读函数 ----------
async def read_platform(conn, curriculum: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """一个只读、可重复读事务里读快照与发布记录（两次读看到同一时刻的平台）。conn 须已核对角色。"""
    from .store import KgeStore
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        kge = KgeStore(conn)  # type: ignore[arg-type]
        v = pick_version(await kge.content_versions(SUBJECT), curriculum)
        snap = await kge.content_snapshot(v["id"])
        recs = await kge.release_records(SUBJECT)
    return snap, recs


def _env_url() -> str:
    """与 python -m review_console record 相同的门槛：只在 dev / test 环境，只用审核台的读连接串。"""
    from .config import ALLOWED_ENVS
    if os.environ.get("REVIEW_CONSOLE_ENV") not in ALLOWED_ENVS:
        raise SystemExit(f"REVIEW_CONSOLE_ENV 必须是 {ALLOWED_ENVS} 之一")
    url = os.environ.get("REVIEW_CONSOLE_KGE_DATABASE_URL")
    if not url:
        raise SystemExit("缺少 REVIEW_CONSOLE_KGE_DATABASE_URL")
    return url


def _not_local(e: SystemExit) -> NotLocalError:
    """PR D 的核对函数以 SystemExit 报错（说明以时政下架任务的口吻写）；换成本命令的说明，保留具体原因。"""
    from .__main__ import NOT_LOCAL
    text = str(e.code)
    return NotLocalError(LOCAL_ONLY + text[len(NOT_LOCAL):] if text.startswith(NOT_LOCAL) else text)


def check_local_url(url: str) -> None:
    """连接前：连接串里的全部主机都必须是本机（review_console.__main__.local_only，PR D）。"""
    from .__main__ import local_only
    try:
        local_only(url)
    except SystemExit as e:
        raise _not_local(e) from None


async def _connect(url: str):
    import asyncpg

    from .__main__ import check_server_local
    from .store import expect_role, role_facts
    check_local_url(url)
    conn = await asyncpg.connect(url)
    try:
        try:
            await check_server_local(conn)  # 连上之后：服务端地址为空（Unix socket）或回环地址
        except SystemExit as e:
            raise _not_local(e) from None
        expect_role(await role_facts(conn), "starbuddy_kge")
    except BaseException:
        await conn.close()
        raise
    return conn


async def _export(pack_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    url = _env_url()
    pack = load_pack(pack_path)
    conn = await _connect(url)
    try:
        snap, recs = await read_platform(conn, pack["curriculum"])
    finally:
        await conn.close()
    return build_decisions(snap, recs, pack["curriculum"], {i["id"] for i in pack["items"]}, dt.datetime.now(dt.timezone.utc))


async def _verify(pack_path: str) -> dict[str, Any]:
    url = _env_url()
    pack = load_pack(pack_path)
    conn = await _connect(url)
    try:
        snap, _ = await read_platform(conn, pack["curriculum"])
    finally:
        await conn.close()
    return check_not_more_lenient(pack, snap)


def check_outputs(out: str, report: str | None, replace: bool) -> None:
    """读库之前先核对输出路径：已存在且没加 --replace 即拒（FileExistsError），决定文件与报告不能是同一个文件，所在目录须已存在。"""
    paths = [x for x in (out, report) if x]
    if report and Path(report).resolve() == Path(out).resolve():
        raise InputError("--out 与 --report 不能是同一个文件")
    for x in paths:
        q = Path(x)
        if q.is_dir():
            raise InputError(f"输出路径 {x} 是目录")
        if not q.parent.is_dir():
            raise InputError(f"输出路径 {x} 所在目录不存在")
        if (q.exists() or q.is_symlink()) and not replace:
            raise FileExistsError(x)


def _write(path: str, text: str, replace: bool) -> None:
    """不加 replace：独占创建（已存在即 FileExistsError），写失败删掉这次建的文件。
    加 replace：同目录临时文件写完再原子替换；无论成败，finally 里删掉还在的临时文件（不留半份文件）。"""
    target = Path(path)
    if not replace:
        with open(target, "x", encoding="utf-8") as f:
            try:
                f.write(text)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        return
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def main(argv: list[str] | None = None) -> int:
    import asyncpg
    p = argparse.ArgumentParser(prog="review_console.admission_export")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="从平台导出阿鸦准入决定文件（aya-admission-decisions/1）")
    e.add_argument("--pack", required=True, help="阿鸦内容包（取六段标识与题号）")
    e.add_argument("--out", required=True, help="决定文件（已存在时须加 --replace）")
    e.add_argument("--report", help="导出报告（aya-admission-export-report/1；已存在时须加 --replace）")
    e.add_argument("--replace", action="store_true", help="覆盖已有的决定文件与报告（各自原子替换）")
    v = sub.add_parser("verify", help="核对重建后的内容包没有比平台宽的准入（发布门禁：重建后未跑 verify 不得发布）")
    v.add_argument("--pack", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "export":
            check_outputs(a.out, a.report, a.replace)  # 读库之前：输出路径不合要求就不连库
            file, report = asyncio.run(_export(a.pack))
            if a.report:  # 先写报告：决定文件（重建的输入）存在时，它的报告一定已经在
                _write(a.report, dumps(report), a.replace)
            _write(a.out, dumps(file), a.replace)
            print(f"准入决定导出：{report['six_segment']}（{report['status']}）平台 {report['platform_items']} 道题 → "
                  f"{report['decisions']} 条决定 {json.dumps(report['exported'], ensure_ascii=False)}；"
                  f"pending_review 回到缺省 {report['pending_review_as_default_count']} 道；{report['export_ref']}", file=sys.stderr)
            return 0
        out = asyncio.run(_verify(a.pack))
        sys.stdout.write(dumps(out))
        return 0 if out["ok"] else 1
    except InputError as x:  # 输入的形状或枚举不对：比较、读库之前就拒
        print(f"准入决定导出：输入不对（{x}）", file=sys.stderr)
        return 2
    except ExportError as x:
        print(f"准入决定导出：{x}", file=sys.stderr)
        for line in x.problems[:20]:
            print(f"  {line}", file=sys.stderr)
        return 1
    except NotLocalError as x:  # 规则 9：不连非本机库
        print(f"准入决定导出：{x}", file=sys.stderr)
        return 2
    except RuntimeError as x:  # 登录角色核对不过（store.expect_role）：不读任何数据
        print(f"准入决定导出：{x}", file=sys.stderr)
        return 2
    except FileExistsError:
        print("准入决定导出：输出文件已存在（覆盖决定文件与报告须加 --replace）", file=sys.stderr)
        return 2
    except OSError as x:
        print(f"准入决定导出：读写文件失败（{x.__class__.__name__}）", file=sys.stderr)
        return 2
    except ValueError as x:
        print(f"准入决定导出：输入不对（{x.__class__.__name__}）", file=sys.stderr)
        return 2
    except asyncpg.PostgresError as x:
        print(f"准入决定导出：库拒绝（{getattr(x, 'sqlstate', '') or x.__class__.__name__}）", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
