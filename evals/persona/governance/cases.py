"""人设治理回归用例（evals/persona/cases/）：加载、校验、展开变体、计算用例指纹。

目录下每个 YAML 文件一类：
  kind: family   check: <七类之一>   cases: [{case_id, persona_id, expect: pass|fail, turns: [...]}]
  kind: drift                        cases: [{case_id, persona_id, grade, expect: pass, rounds: [...], variants: [...]}]
  kind: leak                         cases: [{case_id, persona_id, expect, problem: {answer, answer_forms, reveal_level}, turns}]
  kind: recorded                     cases: [{case_id, persona_id, checks: [...], turns: [只含学生轮]}]   # 录制回放：角色轮来自录音
合成用例（mode 默认 synthetic）是检查器自身的正例与反例；recorded 用例要有对应录音才能跑。
这些都是从规格书示例摘录或手写的种子用例（source: spec-derived / synthetic），**不是金标准**，待教研确认。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .common import FAMILY_CHECKS, Turn, to_turns

KINDS = ("family", "drift", "leak", "recorded")
RECORDED_CHECKS = FAMILY_CHECKS[1:] + ("drift", "leak", "forbidden")


class CaseError(ValueError):
    pass


def load_cases(case_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not case_dir.is_dir():
        return out
    for p in sorted(case_dir.rglob("*.yaml")):
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        kind = doc.get("kind")
        if kind not in KINDS:
            raise CaseError(f"{p.name}: kind 必须是 {KINDS} 之一")
        if doc.get("not_persona_corpus") is not True:
            raise CaseError(f"{p.name}: 合成转写要标 not_persona_corpus: true（不是人设语料、不是金标准）")
        if doc.get("source") not in ("spec-derived", "synthetic"):
            raise CaseError(f"{p.name}: source 要写 spec-derived 或 synthetic（不冒充金标准）")
        for raw in doc.get("cases") or []:
            c = {"kind": kind, "file": p.name, "check": doc.get("check"), **copy.deepcopy(raw)}
            if kind == "family" and c.get("check") not in FAMILY_CHECKS:
                raise CaseError(f"{p.name} {c.get('case_id')}: family 用例的 check 必须是七类之一")
            for k in ("case_id", "persona_id"):
                if not c.get(k):
                    raise CaseError(f"{p.name}: 用例缺 {k}")
            if kind != "recorded" and c.get("expect") not in ("pass", "fail"):
                raise CaseError(f"{p.name} {c['case_id']}: expect 必须是 pass 或 fail")
            out.append(c)
            for v in raw.get("variants") or []:
                out.append(apply_variant(c, v))
    ids = [c["case_id"] for c in out]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise CaseError(f"用例编号重复：{dup}")
    return out


def apply_variant(case: dict[str, Any], v: dict[str, Any]) -> dict[str, Any]:
    """漂移用例的反例变体：按轮次（从 1 数，指角色的第 N 轮）替换文本、字段或上下文"""
    c = copy.deepcopy({k: val for k, val in case.items() if k != "variants"})
    c["case_id"] = f"{case['case_id']}{v['suffix']}"
    c["expect"] = v.get("expect", "fail")
    c["expect_check"] = v.get("expect_check")
    c["variant_of"] = case["case_id"]
    c["note"] = v.get("note")
    rounds = c.get("rounds") or []
    for patch in v.get("patch") or []:
        n = int(patch["round"]) - 1
        if not 0 <= n < len(rounds):
            raise CaseError(f"{c['case_id']}: 变体改的第 {n + 1} 轮不存在")
        for k in ("p", "s", "fields", "context", "segment", "speaker"):
            if k in patch:
                rounds[n][k] = copy.deepcopy(patch[k])
    if v.get("truncate") is not None:
        c["rounds"] = rounds[: int(v["truncate"])]
    if v.get("extend"):
        c["rounds"] = rounds + copy.deepcopy(v["extend"])
    return c


def turns_of(case: dict[str, Any]) -> list[Turn]:
    return to_turns(case.get("turns"), case.get("rounds"), case.get("persona_id"))


def student_turns(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"speaker": t.speaker, "text": t.text, "emotion_signal": t.emotion_signal, "asks_identity": t.asks_identity}
            for t in turns_of(case) if t.speaker == "student"]


def case_fingerprint(case: dict[str, Any]) -> str:
    """录制回放用的用例指纹：学生输入、场景、检查项、题目字段变了，旧录音就过期"""
    keep = {k: case.get(k) for k in ("case_id", "persona_id", "kind", "check", "checks", "scene", "grade", "problem")}
    keep["student_turns"] = student_turns(case)
    blob = json.dumps(keep, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
