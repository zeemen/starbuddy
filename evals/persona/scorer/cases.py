"""用例加载、合并与格式校验（specs/diandian-cases-v0-sprint1/spec.md 设计要点 1、2、6）。

用例文件按类别一个：文件级 defaults（scene、context、checks）与 positive_checks 合并进每条用例，再用 JSON Schema 校验。
另做仓库级检查：条数与分配、正负样例齐全、编号前缀、核心标记、留出集可复现、只含合成数据。
"""
from __future__ import annotations

import copy
import json
import random
import re
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

# 只含合成数据（规则 8）：手机号、身份证号、邮箱、具体学校名一律不许出现
PII_PATTERNS = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "邮箱": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "学校名": re.compile(r"[一-龥]{2,8}(中学|附中|实验学校|外国语学校|小学)"),
}


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
    case.update(copy.deepcopy(raw))
    return case


def load_cases(case_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(Path(case_dir).glob("*.yaml")):
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "category" not in doc or "cases" not in doc:
            raise CaseError(f"{p.name}: 缺少 category 或 cases")
        extra = set(doc) - {"category", "core", "defaults", "positive_checks", "cases"}
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


def _texts(case: dict[str, Any]) -> list[str]:
    texts = list(case["turns"])
    ctx = case.get("context") or {}
    texts += [h["text"] for h in ctx.get("history") or []]
    texts.append(json.dumps(ctx, ensure_ascii=False))
    return texts


def validate(case_dir: Path, schema_path: Path, holdout_path: Path | None = None) -> list[str]:
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
        for ch in c["checks"]:
            if ch["type"] == "rule" and ch["rule"] == "forbidden" and not ch.get("classes"):
                errors.append(f"{cid}.{ch['id']}: 禁语检查点要写 classes")
        for text in _texts(c):
            for name, pat in PII_PATTERNS.items():
                if pat.search(text):
                    errors.append(f"{cid}: 疑似出现{name}（只允许合成数据，规则 8）")
    for cat in EXPECTED_COUNTS:
        pols = {c["polarity"] for c in cases if c["category"] == cat}
        if pols != {"positive", "negative"}:
            errors.append(f"{cat}: 正向与负向样例都要有")
    if holdout_path is not None:
        want = holdout_ids(cases)
        have = [x.strip() for x in Path(holdout_path).read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]
        if have != want:
            errors.append("holdout.txt 与固定种子重新抽样的结果不一致（运行 scorer holdout --write 重新生成）")
    return errors


def auto_judgable_ratio(cases: list[dict[str, Any]], core_only: bool = True) -> float:
    checks = [ch for c in cases if (c["core"] or not core_only) for ch in c["checks"]]
    return sum(ch["type"] != "review" for ch in checks) / max(1, len(checks))
