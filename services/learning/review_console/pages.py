"""编译页面线：托管平台合并记录与 kge.content_review 页面审核记录的对账（specs/release-gate-sprint4/spec.md 第 6 节、冲刺 3 段）。

纯函数，不连托管平台、不连库：输入是三份数据——
- 托管平台导出的合并记录（`kge-pages-merge-export/1`，格式见 services/learning/README.md“编译页面对账”）；
- 页面审核记录（库函数 kge.page_review_record() 的行，或同格式的 JSON 文件 `kge-page-review-records/1` 或 `/1.1`）；
- 托管平台用户名 → staff 账号的对应表（`kge-pages-staff-map/1`，审核台配置，由人维护，不含学生数据）。

键：页面 ID + 合并请求的头提交（page_commit 的口径：审核人批准时看到的那个提交；合并进主分支的合并提交在报告里对照给出）。
口径：
- 每个页面只按它在主分支上**最近一次合并**判定（merged_at 最晚的合并请求）；更早的合并只列在 superseded 里，不再判定；
- 一方有、一方没有 → 失败：最近一次合并的页面需要批准（中、高风险）而审核记录里没有（missing_review）；
- 批准人数不符 → 失败：合并请求的批准人（映射到 staff）与审核记录里批准的 staff 不是同一组（approver_mismatch）；
  批准人在对应表里找不到（approver_unmapped）；所需人数不够（insufficient_approvals：中、高风险两人，与 KGE-03 合并窗口
  services/knowledge/knowledge/compiler/window.py 的 REVIEWERS 一致，有测试核对；风险取合并记录与审核记录中较高的一方）；
  审核记录里有驳回（rejected_in_review）；合并请求作者（可选字段 author，映射到 staff）自己批准或签字（self_approval）；
- 低风险页面不需要事前签字（spec 第 2 节）：没有审核记录不算失败，托管平台上有批准人而审核台没有记录也不算；
  审核台有记录时才比对批准人；
- 低风险事后抽检（冲刺 4，spot_check.py）：依据为 spot_check 的低风险记录不是事前签字，不参与批准人比对、人数与孤儿判定；
  最近一次合并的头提交上有抽检驳回 → 失败（spot_check_rejected），扣页；
- 孤儿审核记录（页面提交不是导出里任何合并请求的头提交，no_merge）：只有该页面仍在本次导出范围内（导出里有改到它的合并请求）、
  且它最新的合并请求（有待审的取待审的，否则取最近一次合并）的头提交上还没有批准记录时才扣页面（blocking）；
  对最新头提交重新签字即解除；导出范围外的旧孤儿只列出，不扣页面。
输出确定（同样输入逐字节相同），只含页面 ID、合并请求编号、提交号与计数，不含用户名与账号；held_pages 供合并窗口与快照导出跳过。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

EXPORT_FORMAT = "kge-pages-merge-export/1"
STAFF_MAP_FORMAT = "kge-pages-staff-map/1"
RECORDS_FORMAT = "kge-page-review-records/1.1"  # 冲刺 4 起：每条可带 basis_code
RECORDS_FORMAT_V1 = "kge-page-review-records/1"  # 仍然接受，但不许出现 basis_code
RECORDS_FORMATS = (RECORDS_FORMAT_V1, RECORDS_FORMAT)
REPORT_FORMAT = "kge-pages-reconcile/1"

COMMIT = re.compile(r"^[0-9a-f]{40}$")
NODE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")  # 与 kge.node_id 域、迁移 0038 一致
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MR_ID = re.compile(r"^[1-9][0-9]{0,8}$")
REPO = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,99}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")
UUID_CANON = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RISKS = ("low", "medium", "high")
# 所需批准人数：阶段 0 没有语义体检，中风险按高风险两人（spec 待人确认 #7 默认；与 KGE-03 window.REVIEWERS 相等，测试核对）
REQUIRED = {"low": 0, "medium": 2, "high": 2}
MAX_MRS = 5000
MAX_PAGES_PER_MR = 500
MAX_RECORDS = 50000
MAX_FILE_BYTES = 10 * 1024 * 1024
RECORD_FIELDS = {"page_id", "page_commit", "risk_level", "decision", "signer"}
# 页面审核记录的依据：审核台在线签字记 mr_review，低风险事后抽检（spot_check.py）记 spot_check。
# 离线记录文件 /1.1 可选带 basis_code（只收这两个值）；/1 不许带
MR_REVIEW_BASIS = "mr_review"
SPOT_CHECK_BASIS = "spot_check"
RECORD_BASES = (MR_REVIEW_BASIS, SPOT_CHECK_BASIS)


class ReconcileInputError(ValueError):
    """输入格式不对：对账无法进行（退出码 2）。消息不回显输入内容。"""


def _keys(obj: Any, where: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(obj, dict):
        raise ReconcileInputError(f"{where} 必须是对象")
    keys = set(obj)
    if not required <= keys or not keys <= required | optional:
        raise ReconcileInputError(f"{where} 的字段不对（必须有 {'、'.join(sorted(required))}，多出的字段一律拒收）")
    return obj


def _match(pattern: re.Pattern, value: Any, where: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ReconcileInputError(f"{where} 写法不对")
    return value


def _time(value: Any, where: str) -> dt.datetime:
    _match(TIMESTAMP, value, where)
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ReconcileInputError(f"{where} 不是有效时间") from None


@dataclass(frozen=True)
class MergeRequest:
    mr_id: str
    state: str  # merged | open
    risk: str
    approvers: tuple[str, ...]  # 托管平台用户名
    pages: tuple[str, ...]
    head_commit: str
    merge_commit: str | None
    merged_at: dt.datetime | None
    author: str | None = None  # 提交方（合并请求作者）的托管平台用户名；可选字段，缺失时审核台拒绝在线签字（判不出从严）


@dataclass(frozen=True)
class MergeExport:
    repository: str
    branch: str
    exported_at: str
    merge_requests: tuple[MergeRequest, ...]


@dataclass(frozen=True)
class ReviewRecord:
    page_id: str
    page_commit: str
    risk_level: str
    decision: str
    signer: str  # staff 账号 UUID（小写字符串）
    spot_check: bool = False  # 低风险事后抽检的记录（依据 spot_check）：不是事前签字，不计入批准人比对


def parse_export(raw: Any) -> MergeExport:
    d = _keys(raw, "合并记录", {"format", "repository", "branch", "exported_at", "merge_requests"})
    if d["format"] != EXPORT_FORMAT:
        raise ReconcileInputError(f"合并记录的 format 必须是 {EXPORT_FORMAT}")
    repo = _match(REPO, d["repository"], "repository")
    branch = _match(BRANCH, d["branch"], "branch")
    _time(d["exported_at"], "exported_at")
    items = d["merge_requests"]
    if not isinstance(items, list) or len(items) > MAX_MRS:
        raise ReconcileInputError(f"merge_requests 必须是数组（最多 {MAX_MRS} 个）")
    mrs, seen_ids, seen_keys = [], set(), set()
    for i, m in enumerate(items):
        where = f"merge_requests[{i}]"
        m = _keys(m, where, {"mr_id", "state", "risk", "approvers", "pages", "head_commit"}, {"merge_commit", "merged_at", "author"})
        author = _match(USERNAME, m["author"], f"{where}.author") if "author" in m else None
        mr_id = _match(MR_ID, m["mr_id"], f"{where}.mr_id")
        if mr_id in seen_ids:
            raise ReconcileInputError(f"{where}.mr_id 重复")
        seen_ids.add(mr_id)
        state = m["state"]
        if state not in ("merged", "open"):
            raise ReconcileInputError(f"{where}.state 只能是 merged 或 open")
        if m["risk"] not in RISKS:
            raise ReconcileInputError(f"{where}.risk 只能是 {'、'.join(RISKS)}")
        approvers = m["approvers"]
        if not isinstance(approvers, list) or len(approvers) > 16:
            raise ReconcileInputError(f"{where}.approvers 必须是数组（最多 16 人）")
        approvers = [_match(USERNAME, a, f"{where}.approvers") for a in approvers]
        if len(set(approvers)) != len(approvers):
            raise ReconcileInputError(f"{where}.approvers 有重复")
        pages = m["pages"]
        if not isinstance(pages, list) or not pages or len(pages) > MAX_PAGES_PER_MR:
            raise ReconcileInputError(f"{where}.pages 必须是非空数组（最多 {MAX_PAGES_PER_MR} 页）")
        pages = [_match(NODE_ID, p, f"{where}.pages") for p in pages]
        if len(set(pages)) != len(pages):
            raise ReconcileInputError(f"{where}.pages 有重复")
        head = _match(COMMIT, m["head_commit"], f"{where}.head_commit")
        merge_commit, merged_at = m.get("merge_commit"), m.get("merged_at")
        if state == "merged":
            merge_commit = _match(COMMIT, merge_commit, f"{where}.merge_commit")
            merged_at = _time(merged_at, f"{where}.merged_at")
        elif merge_commit is not None or merged_at is not None:
            raise ReconcileInputError(f"{where}：待审合并请求不能有 merge_commit、merged_at")
        for p in pages:
            if (p, head) in seen_keys:
                raise ReconcileInputError(f"{where}：同一页面同一提交出现在两个合并请求里")
            seen_keys.add((p, head))
        mrs.append(MergeRequest(mr_id, state, m["risk"], tuple(approvers), tuple(pages), head, merge_commit, merged_at, author))
    return MergeExport(repo, branch, d["exported_at"], tuple(mrs))


def parse_staff_map(raw: Any) -> dict[str, str]:
    d = _keys(raw, "staff 对应表", {"format", "users"})
    if d["format"] != STAFF_MAP_FORMAT:
        raise ReconcileInputError(f"staff 对应表的 format 必须是 {STAFF_MAP_FORMAT}")
    users = d["users"]
    if not isinstance(users, dict) or len(users) > 500:
        raise ReconcileInputError("users 必须是对象（最多 500 人）")
    out: dict[str, str] = {}
    for name, acct in users.items():
        _match(USERNAME, name, "users 的用户名")
        out[name] = _match(UUID_CANON, acct, "users 的值（staff 账号 UUID，小写规范写法）")
    # 两个用户名对应同一个 staff，会让一个人算两次批准
    if len(set(out.values())) != len(out):
        raise ReconcileInputError("staff 对应表里有两个用户名对应同一个 staff 账号")
    return out


def parse_records(raw: Any) -> list[ReviewRecord]:
    """离线审核记录文件：严格——/1 每条只允许 5 个字段；/1.1 另可带 basis_code（mr_review 或 spot_check，省略即事前签字）；
    signer 只收小写规范 UUID。"""
    d = _keys(raw, "审核记录", {"format", "records"})
    if d["format"] not in RECORDS_FORMATS:
        raise ReconcileInputError(f"审核记录的 format 必须是 {' 或 '.join(RECORDS_FORMATS)}")
    optional = {"basis_code"} if d["format"] == RECORDS_FORMAT else set()
    if not isinstance(d["records"], list) or len(d["records"]) > MAX_RECORDS:
        raise ReconcileInputError("records 必须是数组")
    for i, r in enumerate(d["records"]):
        _keys(r, f"records[{i}]", RECORD_FIELDS, optional)
        _match(UUID_CANON, r["signer"], f"records[{i}].signer")
        if "basis_code" in r and r["basis_code"] not in RECORD_BASES:
            raise ReconcileInputError(f"records[{i}].basis_code 只能是 {'、'.join(RECORD_BASES)}（省略即事前签字）")
    return records_from_rows(d["records"])


def records_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[ReviewRecord]:
    """库函数 kge.page_review_record() 的行 → 审核记录（宽松：多出的列忽略，signer 可以是 uuid 对象）。"""
    out = []
    for i, r in enumerate(rows):
        where = f"records[{i}]"
        if not isinstance(r, Mapping) or not RECORD_FIELDS <= set(r):
            raise ReconcileInputError(f"{where} 缺字段")
        page = _match(NODE_ID, r["page_id"], f"{where}.page_id")
        commit = _match(COMMIT, r["page_commit"], f"{where}.page_commit")
        if r["risk_level"] not in RISKS or r["decision"] not in ("approved", "rejected"):
            raise ReconcileInputError(f"{where} 的风险或结论取值不对")
        try:
            signer = str(uuid.UUID(str(r["signer"])))
        except ValueError:
            raise ReconcileInputError(f"{where}.signer 不是账号 UUID") from None
        # 只有低风险的才按抽检对待（审核台只对低风险合并发起抽检；别的风险级别即使写了这个依据，也按事前签字比对，偏严）
        spot = r.get("basis_code") == SPOT_CHECK_BASIS and r["risk_level"] == "low"
        out.append(ReviewRecord(page, commit, r["risk_level"], r["decision"], signer, spot))
    return out


def _max_risk(*risks: str) -> str:
    return max(risks, key=RISKS.index)


def _merge_order(mr: MergeRequest) -> tuple:
    return (mr.merged_at, int(mr.mr_id))


def reconcile(export: MergeExport, records: Iterable[ReviewRecord], staff_map: Mapping[str, str]) -> dict[str, Any]:
    """返回对账报告（确定性：键与列表都排序；exported_at 原样带上，下游据此判断报告是否过期）。"""
    by_key: dict[tuple[str, str], list[ReviewRecord]] = {}
    spot_by_key: dict[tuple[str, str], list[ReviewRecord]] = {}
    for r in records:
        # 抽检记录（低风险事后抽检）不是事前签字：单独放，不参与批准人比对与人数（冲刺 4，spot_check.py）
        (spot_by_key if r.spot_check else by_key).setdefault((r.page_id, r.page_commit), []).append(r)
    merged_by_page: dict[str, list[MergeRequest]] = {}
    open_by_page: dict[str, list[MergeRequest]] = {}
    known: set[tuple[str, str]] = set()
    for mr in export.merge_requests:
        for page in mr.pages:
            known.add((page, mr.head_commit))
            (merged_by_page if mr.state == "merged" else open_by_page).setdefault(page, []).append(mr)

    def entry(mr: MergeRequest, page: str) -> dict[str, Any]:
        recs = by_key.get((page, mr.head_commit), [])
        approved = {r.signer for r in recs if r.decision == "approved"}
        risk = _max_risk(mr.risk, *(r.risk_level for r in recs))
        return {"page_id": page, "mr_id": mr.mr_id, "head_commit": mr.head_commit, "merge_commit": mr.merge_commit, "risk": risk,
                "required": REQUIRED[risk], "approvals_platform": len(mr.approvers), "approvals_review": len(approved)}

    merged, superseded = [], []
    for page, mrs in merged_by_page.items():
        mrs = sorted(mrs, key=_merge_order)
        latest = mrs[-1]
        superseded += [{"page_id": page, "mr_id": m.mr_id, "head_commit": m.head_commit, "merge_commit": m.merge_commit} for m in mrs[:-1]]
        e = entry(latest, page)
        recs = by_key.get((page, latest.head_commit), [])
        approved = {r.signer for r in recs if r.decision == "approved"}
        mapped = [staff_map.get(a) for a in latest.approvers]
        platform_staff = {m for m in mapped if m is not None}
        required = e["required"]
        problems = []
        if None in mapped:
            problems.append("approver_unmapped")
        if not recs and required > 0:
            problems.append("missing_review")
        if any(r.decision == "rejected" for r in recs):
            problems.append("rejected_in_review")
        # 审核台有记录、或需要事前签字时才比对批准人（低风险无事前签字：托管平台多出的批准不算失败）
        if (recs or required > 0) and platform_staff != approved and "missing_review" not in problems:
            problems.append("approver_mismatch")
        if min(len(platform_staff), len(approved)) < required:
            problems.append("insufficient_approvals")
        # 提交方不能自签（spec 设计要点 3）：作者（映射到 staff）出现在托管平台批准人或审核记录的批准人里 → 失败。
        # 作者缺失或不在对应表里时对账判不出，不在这里扣页；审核台在线签字处另行拒绝（service.check_not_author）
        author_staff = staff_map.get(latest.author) if latest.author else None
        if latest.author is not None and (latest.author in latest.approvers or (author_staff is not None and author_staff in approved)):
            problems.append("self_approval")
        # 事后抽检驳回：合并窗口跳过该页，直到人处理（改好后的新合并按新的头提交重新判定）
        if any(s.decision == "rejected" for s in spot_by_key.get((page, latest.head_commit), [])):
            problems.append("spot_check_rejected")
        merged.append({**e, "status": "failed" if problems else "ok", "problems": sorted(problems)})
    pending = [entry(mr, page) for page, mrs in open_by_page.items() for mr in mrs]

    def latest_head(page: str) -> str | None:
        if page in open_by_page:
            return max(open_by_page[page], key=lambda m: int(m.mr_id)).head_commit
        if page in merged_by_page:
            return max(merged_by_page[page], key=_merge_order).head_commit
        return None

    orphans = []
    for (p, c), recs in sorted(by_key.items()):
        if (p, c) in known:
            continue
        head = latest_head(p)
        resigned = head is not None and any(r.decision == "approved" for r in by_key.get((p, head), []))
        orphans.append({"page_id": p, "page_commit": c, "records": len(recs), "problems": ["no_merge"],
                        "blocking": head is not None and not resigned})
    merged.sort(key=lambda e: (e["page_id"], e["mr_id"]))
    pending.sort(key=lambda e: (e["page_id"], e["mr_id"]))
    superseded.sort(key=lambda e: (e["page_id"], e["mr_id"]))
    held = sorted({e["page_id"] for e in merged if e["status"] == "failed"} | {o["page_id"] for o in orphans if o["blocking"]})
    return {"format": REPORT_FORMAT, "repository": export.repository, "branch": export.branch, "exported_at": export.exported_at,
            "ok": not held, "merged": merged, "superseded": superseded, "pending": pending, "orphans": orphans, "held_pages": held}


def load_json(path: str) -> Any:
    """读 JSON：最大 10 MB；拒绝 NaN / Infinity 与重复键。"""
    def _pairs(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ReconcileInputError("JSON 有重复键")
            d[k] = v
        return d

    def _const(_):
        raise ReconcileInputError("JSON 不能含 NaN 或 Infinity")
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ReconcileInputError(f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB")
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_const)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ReconcileInputError("文件读不了或不是 JSON") from None


def dumps(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def mr_url(template: str, mr_id: str) -> str:
    """托管平台合并请求的网页地址：配置给出模板（含且仅含一个 {mr_id}，且只能在路径里），编号只有数字。"""
    _match(MR_ID, mr_id, "mr_id")
    return template.replace("{mr_id}", mr_id)


# 主机名、端口之后必须先有 “/” 才能出现 {mr_id}：编号不能拼进主机名、顶级域或端口
_MR_URL = re.compile(r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/(?:[A-Za-z0-9._~%-]+/)*\{mr_id\}[A-Za-z0-9._~%/-]*")


def check_mr_url_template(template: str) -> str:
    if not isinstance(template, str) or not _MR_URL.fullmatch(template) or template.count("{mr_id}") != 1:
        raise ValueError("合并请求网页地址模板必须是 https 地址，{mr_id} 只能在路径里（前面是“/”）且只出现一次，不带查询串")
    return template
