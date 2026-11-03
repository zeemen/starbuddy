"""人设治理回归（冲刺 3-F）的公共部分：路径、登记表与词表的加载、转写的规范化。"""
from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]          # evals/persona
CODE_ROOT = HERE.parents[1]                          # 本份代码所在的仓库根
for p in (CODE_ROOT / "packages", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from personas import GUARD_VERSION, PersonaConfigError  # noqa: E402,F401
from personas.guard import Context, load_context  # noqa: E402
from personas.registry import personas_by_id  # noqa: E402

TEACHER_TYPES = ("resident", "examiner")
EVALUATOR_CATEGORIES = ("identity", "forbidden", "referral", "protocol", "starmap", "cross_subject", "metaphor")
EVALUATOR_LABELS = {"identity": "身份披露", "forbidden": "禁语 A–G", "referral": "转介话术", "protocol": "出场协议",
                    "starmap": "星图话术", "cross_subject": "跨科静默", "metaphor": "比喻隔离"}
FAMILY_CHECKS = ("contract", "role_types", "cross_subject", "naming", "metaphor", "mode", "referral")
FAMILY_LABELS = {"contract": "合同符合性", "role_types": "角色三型边界", "cross_subject": "跨科静默", "naming": "命名格式",
                 "metaphor": "比喻隔离", "mode": "模式一致", "referral": "转介一致"}
CATEGORIES = EVALUATOR_CATEGORIES + tuple(f"family_{c}" for c in FAMILY_CHECKS) + ("drift", "leak")


@dataclass
class Env:
    """一次运行的上下文：登记表与词表（来自 root），以及用例、录音目录"""
    root: Path
    ctx: Context
    registry_rel: str

    @property
    def reg(self) -> dict[str, Any]:
        return self.ctx.registry

    @property
    def personas(self) -> dict[str, dict[str, Any]]:
        return personas_by_id(self.ctx.registry)

    def eval_path(self, key: str, default: str) -> Path:
        return self.root / str((self.reg.get("eval") or {}).get(key) or default)

    def subject_names(self, subject: str | None) -> list[str]:
        s = (self.reg.get("subjects") or {}).get(subject or "") or {}
        return list(s.get("aliases") or [])

    def all_subject_names(self) -> dict[str, list[str]]:
        return {k: list(v.get("aliases") or []) for k, v in (self.reg.get("subjects") or {}).items()}

    def names_of(self, pid: str) -> list[str]:
        """一个角色的所有叫法：正式名 + 别人对它的称谓"""
        p = self.personas[pid]
        return list(dict.fromkeys([p["display_name"], *[str(v) for v in (p.get("address_terms") or {}).values()]]))

    def pattern(self, name: str) -> re.Pattern[str]:
        return self.ctx.lexicon.pattern(name)


def load_env(root: Path | str, registry_rel: str) -> Env:
    root = Path(root).resolve()
    return Env(root=root, ctx=load_context(root, registry_rel), registry_rel=registry_rel)


@dataclass
class Turn:
    speaker: str
    text: str
    fields: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] | None = None
    segment: str | None = None
    emotion_signal: bool = False
    asks_identity: bool = False


def to_turns(raw: list[dict[str, Any]] | None = None, rounds: list[dict[str, Any]] | None = None, persona_id: str | None = None) -> list[Turn]:
    """转写两种写法：turns（逐条，带 speaker），或 rounds（每轮 s = 学生、p = 角色，外加 fields / context / segment）"""
    out: list[Turn] = []
    for t in raw or []:
        out.append(Turn(str(t["speaker"]), str(t.get("text") or ""), dict(t.get("fields") or {}),
                        None if t.get("context") is None else dict(t["context"]), t.get("segment"),
                        bool(t.get("emotion_signal")), bool(t.get("asks_identity"))))
    for r in rounds or []:
        if r.get("s") is not None:
            out.append(Turn("student", str(r["s"]), emotion_signal=bool(r.get("emotion_signal")), asks_identity=bool(r.get("asks_identity"))))
        if r.get("p") is not None:
            out.append(Turn(str(r.get("speaker") or persona_id), str(r["p"]), dict(r.get("fields") or {}),
                            None if r.get("context") is None else dict(r["context"]), r.get("segment")))
    return out


def norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def finding(check: str, persona_id: str, reason: str, *, turn: int | None = None, method: str = "rule", rule_id: str | None = None,
            case_id: str | None = None) -> dict[str, Any]:
    f = {"check": check, "persona_id": persona_id, "reason": reason, "method": method}
    if turn is not None:
        f["turn"] = turn
    if rule_id:
        f["rule_id"] = rule_id
    if case_id:
        f["case_id"] = case_id
    return f
