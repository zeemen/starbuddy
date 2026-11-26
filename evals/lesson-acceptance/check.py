"""讲评课教研验收清单的完整性检查（specs/lesson-acceptance-checklist 第二节第 3 条；待人确认 #1–#5 按默认）。

只查“清单写得全不全”“记录填得全不全”，不判断讲评课好不好：结论由测试者按清单的 expect 逐条给出，不按关键词计分，不用模型判。
验收结论由本脚本按规则算出，测试者不手填：
  - 清单未签字 → trial（试跑，不能作验收结论）；
  - 已签字，且必过项全部 pass → passed；
  - 已签字，任何必过项 fail 或 blocked → failed（blocked 也算不通过，#2）。观察项不影响结论。

用法：
  python check.py                     校验清单与仓库里的合成示例记录（make lesson-acceptance-check）
  python check.py <记录.yaml> ...     校验验收记录并打印结论（真实记录放教研台账，不进本仓库，#4）
退出码：0 全部合格；1 有问题（逐条列出）；2 用法或文件错误。
依赖：PyYAML（与 packages/contracts 的检查同一环境）。
"""
from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CHECKLIST = HERE / "checklist.yaml"
SAMPLE = HERE / "examples" / "sample-record.yaml"

CHECKLIST_SCHEMA = "lesson-acceptance/0.1"
RECORD_SCHEMA = "lesson-acceptance-record/0.1"
EVIDENCE_KINDS = ("independent", "hinted", "transfer", "delayed_retest")   # 迁移 0017 的 evidence_kind
OUTCOMES = ("pass", "fail", "blocked", "n_a")
ENVIRONMENTS = ("staging", "local")                                      # 不连生产（规则 9）
ITEM_FIELDS = {"id", "section", "must", "title", "setup", "steps", "expect", "refs"}
ITEM_OPTIONAL = {"evidence_kind_expect"}
CHECKLIST_FIELDS = {"schema_version", "checklist_version", "status", "signed_by", "signed_at", "sections", "items"}
RECORD_FIELDS = {"schema_version", "checklist_version", "checklist_sha256", "tested_versions", "environment", "tester", "date",
                 "synthetic_inputs", "items"}
VERSION_KEYS = ("dsl", "adapter", "persona_prompt", "model_route", "contract")
ENTRY_FIELDS = {"result", "outcome"}
ENTRY_OPTIONAL = {"evidence_kind_actual", "repro", "n_a_reason", "attachments"}
ID_RE = re.compile(r"^[A-Z][0-9]{1,2}$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_TEXT = 4   # 实际结果、复现、理由至少要写几个字（只防空着不填，不是质量门槛）

# 真实数据的形状（规则 8）：手机号、身份证号、邮箱与 packages/contracts/scripts/check_contract.py 的 PII_PATTERNS 同一口径；
# 学校全名按校名收尾的形状拦，带“合成”二字的占位放行
PII_PATTERNS = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "邮箱": re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    # 只认“……中学 / 小学 / 外国语学校 / 实验学校”这种校名的收尾；“中学生”“初中学段”“回到学校”不算。仍可能误拦（如“学生在中学”），
    # 误拦时在这段文字里写明“合成”即可
    "学校全名": re.compile(r"[\u4e00-\u9fff]{2,10}(?:中学|小学|外国语学校|实验学校)(?![生段年级部])"),
}
SYNTHETIC_MARK = "合成"
# 附件只写相对路径或编号，不嵌原图、不放链接
ATTACHMENT_RE = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9_./\-]{1,128}$")   # 不收冒号：链接与 data: 一律不合格


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)

    def add(self, where: str, msg: str) -> None:
        self.problems.append(f"{where}：{msg}")

    @property
    def ok(self) -> bool:
        return not self.problems


def _load(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _text(v: Any) -> bool:
    return isinstance(v, str) and len(v.strip()) >= MIN_TEXT


def _pii(where: str, value: Any, rep: Report) -> None:
    """递归扫描全部字符串；命中只报类别，不回显原文"""
    if isinstance(value, dict):
        for k, v in value.items():
            _pii(f"{where}.{k}", v, rep)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _pii(f"{where}[{i}]", v, rep)
    elif isinstance(value, str):
        for name, pat in PII_PATTERNS.items():
            for m in pat.finditer(value):
                if name == "学校全名" and SYNTHETIC_MARK in m.group(0):
                    continue
                rep.add(where, f"疑似真实数据（{name}）：测试输入只能是测试者构造的合成数据（规则 8）")
                break


def is_signed(cl: dict) -> bool:
    return bool(str(cl.get("signed_by") or "").strip()) and bool(DATE.match(str(cl.get("signed_at") or "")))


def check_checklist(cl: Any, rep: Report, root: Path = ROOT) -> list[dict]:
    """校验清单本身；返回合格的条目（有问题的条目也返回，只要有 id）"""
    if not isinstance(cl, dict):
        rep.add("清单", "不是映射")
        return []
    missing, extra = CHECKLIST_FIELDS - set(cl), set(cl) - CHECKLIST_FIELDS
    if missing:
        rep.add("清单", f"缺字段 {sorted(missing)}")
    if extra:
        rep.add("清单", f"多余字段 {sorted(extra)}")
    if cl.get("schema_version") != CHECKLIST_SCHEMA:
        rep.add("清单.schema_version", f"必须是 {CHECKLIST_SCHEMA}")
    if not SEMVER.match(str(cl.get("checklist_version", ""))):
        rep.add("清单.checklist_version", "必须是 x.y.z")
    signed = is_signed(cl)
    if cl.get("status") not in ("draft", "signed") or (cl.get("status") == "signed") != signed:
        rep.add("清单.status", "draft 时签字栏为空；signed 时必须有签字人与日期（YYYY-MM-DD）")
    if str(cl.get("signed_by") or "").strip() and not signed:
        rep.add("清单.signed_at", "有签字人就必须有签字日期（YYYY-MM-DD）")
    sections = cl.get("sections")
    if not (isinstance(sections, dict) and sections
            and all(isinstance(k, str) and re.fullmatch(r"[A-Z]", k) and isinstance(v, str) and v.strip() for k, v in sections.items())):
        rep.add("清单.sections", "必须是“大写字母 → 节名”的映射")
        sections = {}
    items = cl.get("items")
    if not isinstance(items, list) or not items:
        rep.add("清单.items", "必须是非空列表")
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for i, it in enumerate(items):
        where = f"清单.items[{i}]"
        if not isinstance(it, dict):
            rep.add(where, "不是映射")
            continue
        iid = it.get("id")
        if isinstance(iid, str):
            where = f"清单 {iid}"
        if set(it) - ITEM_FIELDS - ITEM_OPTIONAL:
            rep.add(where, f"多余字段 {sorted(set(it) - ITEM_FIELDS - ITEM_OPTIONAL)}")
        if ITEM_FIELDS - set(it):
            rep.add(where, f"缺字段 {sorted(ITEM_FIELDS - set(it))}")
        if not (isinstance(iid, str) and ID_RE.match(iid)):
            rep.add(where, "id 必须是“大写字母 + 1–2 位数字”")
            continue
        if iid in seen:
            rep.add(where, "id 重复")
        seen.add(iid)
        if it.get("section") not in sections or not iid.startswith(str(it.get("section"))):
            rep.add(where, "section 必须是 sections 里的节，且与 id 的字母一致")
        if not isinstance(it.get("must"), bool):
            rep.add(where, "must 必须是 true 或 false")
        for k in ("title", "setup", "steps", "expect"):
            if not _text(it.get(k)):
                rep.add(where, f"{k} 必须写清楚（至少 {MIN_TEXT} 个字）")
        if "evidence_kind_expect" in it and it["evidence_kind_expect"] not in EVIDENCE_KINDS:
            rep.add(where, f"evidence_kind_expect 只能是 {EVIDENCE_KINDS}")
        refs = it.get("refs")
        if not (isinstance(refs, list) and refs and all(isinstance(r, str) for r in refs)):
            rep.add(where, "refs 必须是非空的路径列表")
        else:
            for r in refs:
                if r.startswith("/") or ".." in r or not (root / r).exists():
                    rep.add(where, f"refs 指向的文件不存在：{r}")
        out.append(it)
    _pii("清单", cl, rep)
    return out


def checklist_digest(path: Path = CHECKLIST) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_record(rec: Any, cl: dict, items: list[dict], digest: str, rep: Report) -> str | None:
    """校验一份记录；合格时返回结论 trial / passed / failed，不合格返回 None"""
    start = len(rep.problems)
    if not isinstance(rec, dict):
        rep.add("记录", "不是映射")
        return None
    missing, extra = RECORD_FIELDS - set(rec), set(rec) - RECORD_FIELDS
    if missing:
        rep.add("记录", f"缺字段 {sorted(missing)}")
    if extra:
        rep.add("记录", f"多余字段 {sorted(extra)}")
    if rec.get("schema_version") != RECORD_SCHEMA:
        rep.add("记录.schema_version", f"必须是 {RECORD_SCHEMA}")
    if rec.get("checklist_version") != cl.get("checklist_version") or rec.get("checklist_sha256") != digest:
        rep.add("记录.checklist", "清单版本或指纹与当前清单不一致：记录必须对应它所用的那份清单")
    tv = rec.get("tested_versions")
    if not (isinstance(tv, dict) and set(tv) == set(VERSION_KEYS) and all(isinstance(v, str) and v.strip() for v in tv.values())):
        rep.add("记录.tested_versions", f"必须写齐 {list(VERSION_KEYS)}")
    if rec.get("environment") not in ENVIRONMENTS:
        rep.add("记录.environment", f"只能是 {ENVIRONMENTS}（不连生产，规则 9）")
    if not (isinstance(rec.get("tester"), str) and rec["tester"].strip()):
        rep.add("记录.tester", "必须写测试者")
    if not DATE.match(str(rec.get("date", ""))):
        rep.add("记录.date", "必须是 YYYY-MM-DD")
    if rec.get("synthetic_inputs") is not True:
        rep.add("记录.synthetic_inputs", "必须声明测试输入均为测试者构造的合成数据（true）")
    entries = rec.get("items")
    if not isinstance(entries, dict):
        rep.add("记录.items", "必须是“条目 id → 记录”的映射")
        return None
    want = {it["id"] for it in items}
    for iid in sorted(want - set(entries)):
        rep.add(f"记录 {iid}", "缺这一条：每条都要记")
    for iid in sorted(set(entries) - want):
        rep.add(f"记录 {iid}", "清单里没有这一条")
    must_ok = True
    for it in items:
        iid = it["id"]
        e = entries.get(iid)
        if e is None:
            continue
        where = f"记录 {iid}"
        if not isinstance(e, dict):
            rep.add(where, "不是映射")
            continue
        if ENTRY_FIELDS - set(e):
            rep.add(where, f"缺字段 {sorted(ENTRY_FIELDS - set(e))}")
        if set(e) - ENTRY_FIELDS - ENTRY_OPTIONAL:
            rep.add(where, f"多余字段 {sorted(set(e) - ENTRY_FIELDS - ENTRY_OPTIONAL)}")
        outcome = e.get("outcome")
        if outcome not in OUTCOMES:
            rep.add(where, f"outcome 只能是 {OUTCOMES}")
        if not _text(e.get("result")):
            rep.add(where, "result 必须写实际观察到的结果")
        if outcome in ("fail", "blocked") and not _text(e.get("repro")):
            rep.add(where, "fail / blocked 必须写复现：按钮、输入、动作、结果")
        if outcome == "n_a" and not _text(e.get("n_a_reason")):
            rep.add(where, "n_a 必须写理由")
        expect = it.get("evidence_kind_expect")
        actual = e.get("evidence_kind_actual")
        if expect is not None and outcome in ("pass", "fail"):
            if actual not in EVIDENCE_KINDS:
                rep.add(where, f"这一条要记系统实际记下的证据情境（{EVIDENCE_KINDS}）")
            elif actual != expect and outcome == "pass":
                rep.add(where, f"实际证据情境 {actual} 与期望 {expect} 不同，不能记 pass")
        elif actual is not None and actual not in EVIDENCE_KINDS:
            rep.add(where, f"evidence_kind_actual 只能是 {EVIDENCE_KINDS}")
        att = e.get("attachments")
        if att is not None and not (isinstance(att, list) and all(isinstance(a, str) and ATTACHMENT_RE.match(a) for a in att)):
            rep.add(where, "attachments 只写相对路径或编号，不放链接、绝对路径或内嵌数据")
        if it.get("must") is True and outcome != "pass":
            must_ok = False   # n_a 的必过项同样不算通过：要么测，要么写理由后由教研改清单
    _pii("记录", rec, rep)
    if len(rep.problems) > start:
        return None
    if not is_signed(cl):
        return "trial"
    return "passed" if must_ok else "failed"


VERDICT_LABEL = {"trial": "试跑（清单未签字，不能作验收结论）", "passed": "通过（必过项全部 pass）", "failed": "不通过（有必过项未 pass）"}


def main(argv: list[str]) -> int:
    rep = Report()
    try:
        cl = _load(CHECKLIST)
    except (OSError, yaml.YAMLError) as e:
        print(f"lesson-acceptance-check：读不了清单（{type(e).__name__}）")
        return 2
    items = check_checklist(cl, rep)
    digest = checklist_digest()
    records = [Path(a) for a in argv] or [SAMPLE]
    verdicts: list[tuple[Path, str | None]] = []
    for p in records:
        try:
            rec = _load(p)
        except (OSError, yaml.YAMLError) as e:
            print(f"lesson-acceptance-check：读不了记录 {p}（{type(e).__name__}）")
            return 2
        sub = Report()
        v = check_record(rec, cl if isinstance(cl, dict) else {}, items, digest, sub)
        rep.problems.extend(f"{p.name} {x}" for x in sub.problems)
        verdicts.append((p, v))
    for x in rep.problems:
        print(f"  {x}")
    if not rep.ok:
        print(f"lesson-acceptance-check：失败（{len(rep.problems)} 处问题）")
        return 1
    must = sum(1 for it in items if it.get("must") is True)
    print(f"lesson-acceptance-check：清单 {cl['checklist_version']} 合格（{len(items)} 条，必过 {must} 条，"
          f"{'已签字' if is_signed(cl) else '未签字：只能试跑'}）")
    for p, v in verdicts:
        print(f"lesson-acceptance-check：{p.name} 合格；结论：{VERDICT_LABEL[v]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
