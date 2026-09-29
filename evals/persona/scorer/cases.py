"""用例加载、合并与格式校验（specs/diandian-cases-v0-sprint1/spec.md 设计要点 1、2、6）。

用例文件按类别一个：文件级 defaults（scene、context、checks）与 positive_checks 合并进每条用例，再用 JSON Schema 校验。
另做仓库级检查：条数与分配、正负样例齐全、编号前缀、核心标记、留出集可复现、只含合成数据。
"""
from __future__ import annotations

import copy
import json
import random
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import jsonschema
import yaml

# 第 2 节的分配（spec 待人确认 #1 按默认）
EXPECTED_COUNTS = {"identity": 15, "forbidden": 35, "referral": 15, "starmap": 15, "handoff": 15,
                   "memory": 8, "worldview": 7, "drift": 5, "root_cause": 5}
CORE = {"identity", "forbidden", "referral", "starmap", "handoff"}
PREFIX = {"identity": ("ID",), "forbidden": ("FA", "FB", "FC", "FD", "FE"), "referral": ("RF",), "starmap": ("ST",),
          "handoff": ("HO",), "memory": ("ME",), "worldview": ("WO",), "drift": ("DR",), "root_cause": ("RC",)}
FORBIDDEN_SPLIT = {"FA": 5, "FB": 5, "FC": 5, "FD": 5, "FE": 15}
HOLDOUT_SEED = "diandian-cases-v0"
HOLDOUT_RATIO = 0.3
DRIFT_MIN_TURNS = 20

# 只含合成数据（规则 8）：手机号、身份证号、邮箱、学校名（含简称）、人名样式一律不许出现。
# 先做全角转半角（NFKC）并去掉空白与连字符再匹配，防止“138 1234 5678”“１３８…”“＠”这类写法绕过（安全审查应改 1）。
MAX_TEXT = 2000
PII_PATTERNS = {
    "手机号": re.compile(r"(?<!\d)(?:\+?86)?1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)"),
    "邮箱": re.compile(r"[\w.+]{1,64}@[\w]{1,63}\.[A-Za-z]{2,24}"),
    "学校名": re.compile(r"[\u4e00-\u9fa5]{2,8}(中学|附中|实验学校|外国语学校|小学|初中部)"
                      r"|[一二三四五六七八九十百零〇\d]{1,4}中(?![考文学间心午秋国等期])"
                      r"|南外|金陵汇文|师大附|树人学校|科利华|南师附"),
    "人名样式": re.compile(r"我叫[\u4e00-\u9fa5]{2,3}|(?:王|李|张|刘|陈|杨|赵|黄|周|吴|徐|孙|胡|朱|郭|马|罗|梁|宋|郑|谢|韩|唐|冯|于|董|萧|程|曹|袁|邓|许|傅|沈|曾|彭|吕|苏|卢|蒋|蔡|贾|丁|魏|薛|叶|阎|余|潘|杜|戴|夏|钟|汪|田|任|姜|范|方|石|姚|谭|廖|邹|熊|金|陆|郝|孔|白|崔|康|毛|邱|秦|江|史|顾|侯|邵|孟|龙|万|段|雷|钱|汤|尹|黎|易|常|武|乔|贺|赖|龚|文)"
                        r"[\u4e00-\u9fa5]{1,2}(?:同学|老师说|跟我|和我)"),
}


def normalize(text: str) -> str:
    return re.sub(r"[\s\-‐‑–—]", "", unicodedata.normalize("NFKC", text))[:MAX_TEXT]


def pii_hits(text: str) -> list[str]:
    t = normalize(text)
    return [name for name, pat in PII_PATTERNS.items() if pat.search(t)]


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in _strings(k) + _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


class CaseError(Exception):
    pass


def _merge(file_doc: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    defaults = file_doc.get("defaults") or {}
    case = {"category": file_doc["category"], "core": file_doc["core"]}
    for k in ("scene", "context"):
        if k in defaults:
            case[k] = copy.deepcopy(defaults[k])
    if "checks" not in raw:
        if raw.get("polarity") == "positive" and "positive_checks" in file_doc:
            case["checks"] = copy.deepcopy(file_doc["positive_checks"])
        elif "checks" in defaults:
            case["checks"] = copy.deepcopy(defaults["checks"])
    extra = list(raw.get("extra_checks") or [])
    if raw.get("polarity") == "negative" and "checks" not in raw:
        extra = copy.deepcopy(file_doc.get("negative_extra_checks") or []) + extra
    case.update(copy.deepcopy({k: v for k, v in raw.items() if k != "extra_checks"}))
    case["checks"] = case.get("checks", []) + copy.deepcopy(extra)
    return case


def load_cases(case_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(Path(case_dir).glob("*.yaml")):
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "category" not in doc or "cases" not in doc:
            raise CaseError(f"{p.name}: 缺少 category 或 cases")
        extra = set(doc) - {"category", "core", "defaults", "positive_checks", "negative_extra_checks", "cases"}
        if extra:
            raise CaseError(f"{p.name}: 未知的文件级键 {sorted(extra)}")
        for raw in doc["cases"]:
            out.append(_merge(doc, raw))
    return out


def holdout_ids(cases: list[dict[str, Any]]) -> list[str]:
    """按类别分层，固定种子抽 30% 作留出集（修订 Prompt 时只看开发集）"""
    chosen: list[str] = []
    by_cat: dict[str, list[str]] = {}
    for c in cases:
        by_cat.setdefault(c["category"], []).append(c["case_id"])
    for cat in sorted(by_cat):
        ids = sorted(by_cat[cat])
        rng = random.Random(f"{HOLDOUT_SEED}:{cat}")
        chosen += rng.sample(ids, max(1, round(len(ids) * HOLDOUT_RATIO)))
    return sorted(chosen)


def read_holdout(path: Path) -> list[str]:
    return [x.strip() for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]


def _texts(case: dict[str, Any]) -> list[str]:
    """用例里所有字符串：输入、上下文、检查点（含 value、describe）"""
    return _strings(case)


def validate(case_dir: Path, schema_path: Path, holdout_path: Path | None = None, extra_files: tuple[Path, ...] = ()) -> list[str]:
    """返回错误列表；空列表即通过"""
    errors: list[str] = []
    try:
        cases = load_cases(case_dir)
    except (CaseError, yaml.YAMLError) as e:
        return [str(e)]
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for c in cases:
        for err in validator.iter_errors(c):
            errors.append(f"{c.get('case_id', '?')}: {err.message}")
    if errors:
        return errors
    ids = [c["case_id"] for c in cases]
    for dup, n in Counter(ids).items():
        if n > 1:
            errors.append(f"{dup}: 编号重复")
    counts = Counter(c["category"] for c in cases)
    for cat, n in EXPECTED_COUNTS.items():
        if counts.get(cat, 0) != n:
            errors.append(f"{cat}: 应有 {n} 条，实有 {counts.get(cat, 0)} 条")
    if sum(counts.values()) != 120:
        errors.append(f"合计应为 120 条，实有 {sum(counts.values())} 条")
    split = Counter(c["case_id"][:2] for c in cases if c["category"] == "forbidden")
    for prefix, n in FORBIDDEN_SPLIT.items():
        if split.get(prefix, 0) != n:
            errors.append(f"禁语 {prefix}: 应有 {n} 条，实有 {split.get(prefix, 0)} 条")
    for c in cases:
        cid, cat = c["case_id"], c["category"]
        if cid[:2] not in PREFIX[cat]:
            errors.append(f"{cid}: 编号前缀与类别 {cat} 不符")
        if c["core"] != (cat in CORE):
            errors.append(f"{cid}: 核心标记与类别不符")
        if cat == "drift" and len(c["turns"]) < DRIFT_MIN_TURNS:
            errors.append(f"{cid}: 长对话漂移用例至少 {DRIFT_MIN_TURNS} 轮")
        if not any(ch["type"] != "review" for ch in c["checks"]):
            errors.append(f"{cid}: 至少要有一个可自动判定的检查点")
        # 负向用例只查“不出现什么”时，一句空话也能过：至少要有一个 match / equals / all_have_metric 或评审检查点
        if c["polarity"] == "negative" and all(ch.get("expect") == "no_match" for ch in c["checks"]):
            errors.append(f"{cid}: 负向用例只有“不出现”检查，敷衍也能通过；要加一个“做到了什么”的检查点")
        for ch in c["checks"]:
            if ch["type"] == "rule" and ch["rule"] == "forbidden" and not ch.get("classes"):
                errors.append(f"{cid}.{ch['id']}: 禁语检查点要写 classes")
        for text in _texts(c):
            for name in pii_hits(text):
                errors.append(f"{cid}: 疑似出现{name}（只允许合成数据，规则 8）")
    for cat in EXPECTED_COUNTS:
        pols = {c["polarity"] for c in cases if c["category"] == cat}
        if pols != {"positive", "negative"}:
            errors.append(f"{cat}: 正向与负向样例都要有")
    # 自检夹具与词表也扫（安全审查应改 2）
    for f in extra_files:
        for text in _strings(yaml.safe_load(Path(f).read_text(encoding="utf-8"))):
            for name in pii_hits(text):
                errors.append(f"{Path(f).name}: 疑似出现{name}（只允许合成数据，规则 8）")
    if holdout_path is not None:
        # holdout.txt 是冻结名单（契约审查应改 9）：增删用例不会让已有名单变动；新类别或比例偏离时才要求人处理
        have = read_holdout(Path(holdout_path))
        known = {c["case_id"]: c["category"] for c in cases}
        for x in have:
            if x not in known:
                errors.append(f"holdout.txt: {x} 不是现有用例")
        for cat, n in Counter(c["category"] for c in cases).items():
            k = sum(1 for x in have if known.get(x) == cat)
            if abs(k - round(n * HOLDOUT_RATIO)) > 1 or k == 0:
                errors.append(f"holdout.txt: {cat} 留出 {k} 条，偏离 30%（{round(n * HOLDOUT_RATIO)} 条）")
    return errors


def auto_judgable_ratio(cases: list[dict[str, Any]], core_only: bool = True) -> float:
    checks = [ch for c in cases if (c["core"] or not core_only) for ch in c["checks"]]
    return sum(ch["type"] != "review" for ch in checks) / max(1, len(checks))
