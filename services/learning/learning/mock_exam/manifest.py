"""卷清单（paper manifest）格式与校验（PLT-18 spec 范围第 1 条）。纯函数：输入是清单文档与两份只读快照，不读库。

清单字段：format、paper_id、subject（平台学科码）、version、full_score、duration_minutes、source（synthetic / curated）、
synthetic、notice、sections[{name, items[{item_id, paper_score（卷面分值）, question_type_code}]}]。JSON Schema 草案见
config/mock_exam/paper-manifest.schema.json（结构）；这里另做跨字段与对快照的校验：
- 题目 ID 全卷唯一；各题卷面分值之和 = 满分；
- 满分与限时与考情配置快照（policy.subject_config 的满分、时长）一致；
- 题型码属于该科的题型码词表（工程占位词表：考情库里没有题型码字段，词表未定，待人确认）；
- 每道题在准入清单快照（platform.item 中 admission = admitted 的 (学科, 题号) 及其题型码）里，且清单题型码与准入快照一致。
分值按 0.01 分计（对齐 kge.scoring_point.score 的 numeric(5,2)），内部用整数“百分之一分”运算，不用浮点比较。
注意：policy.subject_config.full_score 是 numeric(5,1)、插件契约 ExamConfig.full_score 是 integer，满分精度三处不一致，这里不改（待人确认）。
学科码映射在 config/mock_exam/subjects.json（待人确认，学科目录建成后改外键）。
合成清单（synthetic = true）只允许 LEARNING_ENV=test / dev。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

FORMAT = "starbuddy-mock-paper/1"
ALLOWED_SYNTHETIC_ENVS = ("test", "dev")
SOURCES = ("synthetic", "curated")
SUBJECTS_PATH = Path(__file__).resolve().parents[2] / "config" / "mock_exam" / "subjects.json"
KEYS = frozenset({"format", "paper_id", "subject", "version", "full_score", "duration_minutes", "source", "synthetic", "notice", "sections"})
SECTION_KEYS = frozenset({"name", "items"})
ITEM_KEYS = frozenset({"item_id", "paper_score", "question_type_code"})
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
CODE_RE = re.compile(r"^[a-z0-9_]{1,48}$")
MAX_SECTIONS, MAX_ITEMS = 12, 200


class ManifestError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _load_subjects() -> dict[str, str]:
    doc = json.loads(SUBJECTS_PATH.read_text(encoding="utf-8"))
    rows = doc["subjects"]
    out = {k: v["policy_subject"] for k, v in rows.items()}
    if not out or not all(isinstance(k, str) and isinstance(v, str) and CODE_RE.fullmatch(v) for k, v in out.items()):
        raise ManifestError("SUBJECTS_CONFIG_INVALID", "学科码映射配置不合规")
    return out


# 平台学科码 → 考情库学科码（policy.subject_code）。第一步只有数学与道法
POLICY_SUBJECT = _load_subjects()


@dataclass(frozen=True)
class PaperItem:
    item_id: str
    paper_cents: int                 # 卷面分值（百分之一分）
    question_type_code: str
    section: str


@dataclass(frozen=True)
class Section:
    name: str
    item_ids: tuple[str, ...]
    full_cents: int


@dataclass(frozen=True)
class Paper:
    paper_id: str
    subject: str
    version: str
    full_cents: int
    duration_minutes: int
    source: str
    synthetic: bool
    sections: tuple[Section, ...]
    items: tuple[PaperItem, ...]

    def item(self, item_id: str) -> PaperItem | None:
        for it in self.items:
            if it.item_id == item_id:
                return it
        return None


@dataclass(frozen=True)
class ExamSnapshot:
    """一科的只读输入：policy.subject_config 的满分、时长（考情配置投影）+ 该科题型码词表（工程占位，不是考情配置）。"""
    policy_subject: str
    full_cents: int
    duration_minutes: int
    question_type_codes: frozenset[str]


def _fail(code: str, detail: str) -> None:
    raise ManifestError(code, detail)


def _need(ok: bool, code: str, detail: str) -> None:
    if not ok:
        _fail(code, detail)


def cents(v: Any) -> int | None:
    """非负数，最多两位小数 → 百分之一分；不合规返回 None（布尔、NaN、无穷、三位及以上小数都不收）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    if not d.is_finite() or d < 0:
        return None
    t = d * 100
    if t != t.to_integral_value():
        return None
    return int(t)


def exam_snapshot_from(doc: Mapping[str, Any], subject: str) -> ExamSnapshot:
    """快照文档 {"subjects": {policy_subject: {full_score, duration_minutes, question_type_codes}}} → 一科快照。
    full_score、duration_minutes 是 policy.subject_config 的只读投影；question_type_codes 是工程占位词表。"""
    policy = POLICY_SUBJECT.get(subject)
    _need(policy is not None, "SUBJECT_UNSUPPORTED", f"学科 {subject!r} 没有考情映射")
    row = (doc.get("subjects") or {}).get(policy) if isinstance(doc, Mapping) else None
    _need(isinstance(row, Mapping), "EXAM_CONFIG_MISSING", f"考情配置快照里没有 {policy}")
    full = cents(row.get("full_score"))
    dur = row.get("duration_minutes")
    codes = row.get("question_type_codes")
    _need(full is not None and full > 0, "EXAM_CONFIG_INVALID", "考情配置没有满分（非计分科目不能组模考卷）")
    _need(isinstance(dur, int) and not isinstance(dur, bool) and 10 <= dur <= 300, "EXAM_CONFIG_INVALID", "考情配置限时不合规")
    _need(isinstance(codes, list) and codes and all(isinstance(c, str) and CODE_RE.fullmatch(c) for c in codes),
          "EXAM_CONFIG_INVALID", "题型码词表为空或不合规")
    return ExamSnapshot(policy, full, dur, frozenset(codes))


def admitted_from(doc: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    """准入清单快照 {"items": [{subject, item_id, admission, question_type_code}]} → admitted 的 (学科, 题号) → 题型码。"""
    rows = doc.get("items") if isinstance(doc, Mapping) else None
    _need(isinstance(rows, list), "ADMISSION_SNAPSHOT_INVALID", "准入清单快照必须有 items 列表")
    out: dict[tuple[str, str], str] = {}
    for r in rows:
        _need(isinstance(r, Mapping) and isinstance(r.get("subject"), str) and isinstance(r.get("item_id"), str)
              and isinstance(r.get("question_type_code"), str) and CODE_RE.fullmatch(r["question_type_code"]) is not None,
              "ADMISSION_SNAPSHOT_INVALID", "准入清单快照行缺学科、题号或题型码")
        if r.get("admission") == "admitted":
            out[(r["subject"], r["item_id"])] = r["question_type_code"]
    return out


def parse(doc: Mapping[str, Any], *, exam: ExamSnapshot, admitted: Mapping[tuple[str, str], str], env: str) -> Paper:
    _need(isinstance(doc, Mapping), "MANIFEST_INVALID", "清单必须是对象")
    _need(set(doc) == KEYS, "MANIFEST_INVALID", "清单键不对（多了或少了）")
    _need(doc["format"] == FORMAT, "MANIFEST_INVALID", "清单格式不认识")
    _need(isinstance(doc["paper_id"], str) and ID_RE.fullmatch(doc["paper_id"]) is not None, "MANIFEST_INVALID", "paper_id 不合规")
    subject = doc["subject"]
    _need(subject in POLICY_SUBJECT, "SUBJECT_UNSUPPORTED", f"学科只能取 {tuple(POLICY_SUBJECT)}")
    _need(POLICY_SUBJECT[subject] == exam.policy_subject, "EXAM_CONFIG_MISMATCH", "考情配置快照不是这一科的")
    _need(isinstance(doc["version"], str) and VERSION_RE.fullmatch(doc["version"]) is not None, "MANIFEST_INVALID", "version 必须是 x.y.z")
    _need(doc["source"] in SOURCES, "MANIFEST_INVALID", f"source 只能取 {SOURCES}")
    _need(isinstance(doc["synthetic"], bool), "MANIFEST_INVALID", "synthetic 必须是布尔值")
    _need(doc["source"] != "synthetic" or doc["synthetic"] is True, "MANIFEST_INVALID", "合成来源的卷必须标 synthetic = true")
    _need(not doc["synthetic"] or env in ALLOWED_SYNTHETIC_ENVS, "SYNTHETIC_NOT_ALLOWED", "合成卷只允许 LEARNING_ENV=test / dev")
    _need(isinstance(doc["notice"], str) and len(doc["notice"]) <= 500, "MANIFEST_INVALID", "notice 必须是不超过 500 字的字符串")
    full = cents(doc["full_score"])
    _need(full is not None and full > 0, "MANIFEST_INVALID", "full_score 必须是正数（最多两位小数）")
    dur = doc["duration_minutes"]
    _need(isinstance(dur, int) and not isinstance(dur, bool) and 1 <= dur <= 300, "MANIFEST_INVALID", "duration_minutes 必须是 [1, 300] 内的整数")
    _need(full == exam.full_cents, "FULL_SCORE_MISMATCH", "满分与考情配置不一致")
    _need(dur == exam.duration_minutes, "DURATION_MISMATCH", "限时与考情配置不一致")

    secs = doc["sections"]
    _need(isinstance(secs, list) and 0 < len(secs) <= MAX_SECTIONS, "MANIFEST_INVALID", f"sections 必须有 1–{MAX_SECTIONS} 节")
    seen: set[str] = set()
    names: set[str] = set()
    sections: list[Section] = []
    items: list[PaperItem] = []
    for s in secs:
        _need(isinstance(s, Mapping) and set(s) == SECTION_KEYS, "MANIFEST_INVALID", "分节键不对")
        name = s["name"]
        _need(isinstance(name, str) and 0 < len(name) <= 40, "MANIFEST_INVALID", "节名必须是 1–40 字")
        _need(name not in names, "SECTION_DUPLICATE", f"节名重复：{name}")
        names.add(name)
        rows = s["items"]
        _need(isinstance(rows, list) and len(rows) > 0, "MANIFEST_INVALID", f"分节 {name} 没有题")
        ids: list[str] = []
        sec_total = 0
        for r in rows:
            _need(isinstance(r, Mapping) and set(r) == ITEM_KEYS, "MANIFEST_INVALID", "题目键不对")
            iid = r["item_id"]
            _need(isinstance(iid, str) and ID_RE.fullmatch(iid) is not None, "MANIFEST_INVALID", "item_id 不合规")
            _need(iid not in seen, "ITEM_DUPLICATE", f"题目重复：{iid}")
            seen.add(iid)
            sc = cents(r["paper_score"])
            _need(sc is not None and sc > 0, "MANIFEST_INVALID", f"题 {iid} 卷面分值必须是正数（最多两位小数）")
            code = r["question_type_code"]
            _need(isinstance(code, str) and code in exam.question_type_codes, "QUESTION_TYPE_UNKNOWN",
                  f"题 {iid} 的题型码不在该科题型码词表里")
            _need((subject, iid) in admitted, "ITEM_NOT_ADMITTED", f"题 {iid} 不在准入清单内")
            _need(admitted[(subject, iid)] == code, "QUESTION_TYPE_MISMATCH", f"题 {iid} 的题型码与准入快照不一致")
            ids.append(iid)
            sec_total += sc
            items.append(PaperItem(iid, sc, code, name))
        sections.append(Section(name, tuple(ids), sec_total))
    _need(len(items) <= MAX_ITEMS, "MANIFEST_INVALID", f"全卷最多 {MAX_ITEMS} 题")
    _need(sum(it.paper_cents for it in items) == full, "SCORE_SUM_MISMATCH", "各题卷面分值之和不等于满分")
    return Paper(doc["paper_id"], subject, doc["version"], full, dur, doc["source"], doc["synthetic"], tuple(sections), tuple(items))


def load(path: str | Path, *, exam_doc: Mapping[str, Any], admission_doc: Mapping[str, Any], env: str) -> Paper:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ManifestError("MANIFEST_INVALID", "清单文件读不到或不是合法 JSON") from None
    subject = doc.get("subject") if isinstance(doc, dict) else None
    return parse(doc, exam=exam_snapshot_from(exam_doc, subject), admitted=admitted_from(admission_doc), env=env)
