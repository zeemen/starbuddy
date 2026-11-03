"""点点后处理 v0（冲刺 3-I，specs/diandian-consistency-r2-sprint3/spec.md 设计要点 1）。版本 diandian-post@0.1.0，确定性。

对一轮回复（正文 + 结构字段）依次做三件事，每件事只用规格原文或占位，不造句：
1. 情感信号（学生这一轮的输入命中 signals.yaml 的规则近似，依据备考搭子 V1.0 §4.9）→ 整段回复换成 §4.9 转介原句，结构字段 referral = true；
2. 否则，回复命中点点适用的禁语 A–E 类（经 3-F 的 guard 分类器，只用规则层）→ 整段回复换成 safe_reply（规格没有原句，占位 {{SAFE_REPLY}}）；
3. 首轮对话（用例上下文 first_contact 且第 0 轮）回复里没有 AI 身份披露（与评分器同一条 identity_disclosure 模式）→ 在最前面加 §4.1 披露原句。

替代句全部来自 replacements.yaml：verbatim 条目加载时逐字核对规格原文（找不到就拒绝加载，fail closed），placeholder 条目只能是 {{NAME}}。
动作记录只含动作名、规则编号与信号编号，不含回复原文。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
if str(ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(ROOT / "packages"))

from personas.guard import classify, load_context  # noqa: E402

VERSION = "diandian-post@0.1.0"
NONE = "none"  # 修订记录里“不用后处理”的写法
PERSONA_ID = "diandian"
CLASSES = ("A", "B", "C", "D", "E")  # 点点适用的禁语类（登记表 forbidden_classes）
REPLACEMENT_KEYS = ("disclosure", "referral", "safe_reply")
PLACEHOLDER = re.compile(r"^\{\{[A-Z_]+\}\}$")
PLACEHOLDER_NOTE = "替代句待人设负责人提供"
SOURCE_DIRS = (("docs", "personas"), ("docs", "subjects"))  # 替代句的出处只能是规格文档（只读）
REPLACEMENTS_PATH = HERE / "replacements.yaml"
SIGNALS_PATH = HERE / "signals.yaml"


class PostprocConfigError(ValueError):
    """替代句或信号规则不合规（fail closed）"""


@dataclass(frozen=True)
class Replacement:
    key: str
    text: str
    origin: str          # verbatim | placeholder
    source: str          # 出处（verbatim：文件 + 章节；placeholder：说明）

    @property
    def pending(self) -> bool:
        return self.origin == "placeholder"


@dataclass(frozen=True)
class Signal:
    id: str
    name: str
    rx: re.Pattern[str]
    source: str


def load_replacements(path: Path = REPLACEMENTS_PATH, root: Path = ROOT) -> dict[str, Replacement]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw = doc.get("replacements")
    if not isinstance(raw, dict) or set(raw) != set(REPLACEMENT_KEYS):
        raise PostprocConfigError(f"替代句必须恰好是 {list(REPLACEMENT_KEYS)}")
    out: dict[str, Replacement] = {}
    for key in REPLACEMENT_KEYS:
        r = raw[key] or {}
        text, origin = r.get("text"), r.get("origin")
        if not isinstance(text, str) or not text:
            raise PostprocConfigError(f"{key}: 缺 text")
        if origin == "verbatim":
            rel = r.get("source_file")
            if not isinstance(rel, str) or not r.get("section"):
                raise PostprocConfigError(f"{key}: verbatim 条目必须写 source_file 与 section")
            parts = Path(rel).parts
            # 出处只能是仓库里的规格文档（docs/personas 或 docs/subjects），不许绝对路径或 ..
            if Path(rel).is_absolute() or ".." in parts or parts[:2] not in SOURCE_DIRS:
                raise PostprocConfigError(f"{key}: 出处只能是 docs/personas/ 或 docs/subjects/ 下的规格文档：{rel}")
            src = root / rel
            if not src.is_file():
                raise PostprocConfigError(f"{key}: 出处文件不存在：{rel}")
            # 逐字核对：替代句必须是规格原文里的一段（不造句）
            if text not in src.read_text(encoding="utf-8"):
                raise PostprocConfigError(f"{key}: 替代句不是出处文件里的原文（{rel}）")
            out[key] = Replacement(key, text, origin, f"{rel} {r['section']}")
        elif origin == "placeholder":
            if not PLACEHOLDER.match(text):
                raise PostprocConfigError(f"{key}: 占位必须是 {{{{NAME}}}} 形式")
            out[key] = Replacement(key, text, origin, str(r.get("note") or PLACEHOLDER_NOTE))
        else:
            raise PostprocConfigError(f"{key}: origin 只能是 verbatim 或 placeholder")
    return out


def load_signals(path: Path = SIGNALS_PATH) -> tuple[Signal, ...]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: list[Signal] = []
    seen: set[str] = set()
    for s in doc.get("signals") or []:
        sid = s.get("id")
        if not isinstance(sid, str) or sid in seen:
            raise PostprocConfigError(f"信号编号缺失或重复：{sid!r}")
        seen.add(sid)
        try:
            rx = re.compile(s["pattern"])
        except (KeyError, re.error) as e:
            raise PostprocConfigError(f"{sid}: 模式不合法：{e}") from None
        pos, neg = s.get("positive") or [], s.get("negative") or []
        if not pos or not neg:
            raise PostprocConfigError(f"{sid}: 必须有合成正例与反例")
        if not all(rx.search(x) for x in pos) or any(rx.search(x) for x in neg):
            raise PostprocConfigError(f"{sid}: 正反例与模式不一致")
        out.append(Signal(sid, str(s.get("name") or ""), rx, str(s.get("source") or "")))
    if not out:
        raise PostprocConfigError("没有情感信号规则")
    return tuple(out)


class PostProcessor:
    """确定性后处理。一个实例对应一个版本；process 不改入参"""

    version = VERSION

    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.replacements = load_replacements(root=root)
        self.signals = load_signals()
        self._ctx = load_context(root)
        self._disclosure_rx = self._ctx.lexicon.pattern("identity_disclosure")

    def detect_signals(self, student_text: str) -> list[str]:
        return [s.id for s in self.signals if s.rx.search(student_text or "")]

    def forbidden_hits(self, text: str) -> list[dict[str, Any]]:
        return classify(PERSONA_ID, text, ctx=self._ctx, classes=CLASSES)

    def process(self, text: str, fields: dict[str, Any] | None, *, student_text: str, turn_index: int,
                first_contact: bool) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """返回（处理后的正文，处理后的结构字段，动作列表）。动作不含原文"""
        out_fields = dict(fields or {})
        actions: list[dict[str, Any]] = []
        signals = self.detect_signals(student_text)
        if signals:
            text = self.replacements["referral"].text
            out_fields["referral"] = True
            actions.append({"action": "referral", "signals": signals})
        else:
            hits = self.forbidden_hits(text)
            if hits:
                text = self.replacements["safe_reply"].text
                actions.append({"action": "block", "classes": sorted({h["class"] for h in hits}),
                                "rule_ids": sorted({h["rule_id"] for h in hits})})
        if first_contact and turn_index == 0 and not self._disclosure_rx.search(text):
            text = self.replacements["disclosure"].text + text
            actions.append({"action": "disclosure"})
        return text, out_fields, actions

    def describe(self) -> dict[str, Any]:
        """写进报告的后处理说明：版本、每条替代句的来源与是否待人提供（不含句子以外的内容）"""
        return {"version": self.version,
                "replacements": [{"key": r.key, "origin": r.origin, "source": r.source, "pending": r.pending,
                                  "text": r.text} for r in self.replacements.values()],
                "pending": [r.key for r in self.replacements.values() if r.pending],
                "signals": [{"id": s.id, "name": s.name, "source": s.source} for s in self.signals]}


VERSIONS = (VERSION,)


def forbidden_hits(text: str) -> list[dict[str, Any]]:
    """点点适用的禁语 A–E 类命中（guard 规则层）；报告的“禁语触发率”与后处理的拦截用同一个判定"""
    return classify(PERSONA_ID, text, ctx=load_context(ROOT), classes=CLASSES)


def get(version: str | None, root: Path = ROOT) -> PostProcessor | None:
    """按版本号取后处理；None 或 "none" 表示不用后处理"""
    if version in (None, NONE):
        return None
    if version != VERSION:
        raise PostprocConfigError(f"未知的后处理版本 {version!r}（已有：{', '.join(VERSIONS)}）")
    return PostProcessor(root)


__all__ = ["NONE", "PLACEHOLDER_NOTE", "PostProcessor", "PostprocConfigError", "Replacement", "Signal", "VERSION", "VERSIONS",
           "forbidden_hits", "get", "load_replacements", "load_signals"]
