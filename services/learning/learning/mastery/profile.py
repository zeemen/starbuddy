"""学情档案页（KGE-04 第二步 PR 4；第一步 spec 第 14 节、第二步 spec 第 5 节）：编译器、惰性编译 ensure 与读取。

定位：档案页是判断账本的可读视图，自身不产生新结论；不调任何模型（第一步 #19）；不依赖知识页面仓库的任何客户端（规则 13）。

- 读判断：以 starbuddy_learning 身份只读 insight.claim（0010 claim_agent_read：可见范围含 agent、未过期、模板仍有效的非终态判断），
  另在查询里显式写出同样的条件并要求模板已审核（与 RLS 重叠是有意的）；读不到的判断不进页。学习服务对 insight.claim 没有写权限。
- 六节（0037 的固定编码）：current_stage / mastered / working_on / root_cause / recent_progress / next_step。
  判断按模板键归节（配置 sections：固定一节，或按 value.gate 分节），配置里没有的模板不进页。
  陈述只来自“成立”“假设”的判断（规则 11），按判断模板渲染（render.render_text，与洞察服务 RenderedStatement 同一套规则）；
  渲染不出来的不作陈述；“待验证”只进 Agent 版待办（todo，只有引用）。
- 衔接语：只用人审过的固定衔接短语表（配置 connectives，每节一条，加载时核对它们在一致性检查的短语表里），只在该节有陈述时出现。
- 每次编译都跑第一步的陈述—判断一致性检查（consistency.finalize）：不过就降级为纯模板版本（check_status = blocked_fallback，没有衔接语），
  并写一条 platform.profile_page_check（只追加；禁用推断命中不存片段，片段去数字、截到 60 字）。
- 存储：每生每科一行（原地重编），只存引用（claim_id、模板键与版本）与衔接语，不存陈述文字；claims_digest、compiler_version，
  model_version 为空。
- 惰性编译 ensure(student, subject, trigger)：只由会话开始（编排层）与周报（洞察服务）触发；作答与掌握重算都不触发。
  claims_digest 覆盖“该生该科全部候选判断”（可读、模板在配置里的判断的 id、updated_at、状态、模板键与版本，外加编译器版本与配置指纹），
  所以判断新增、推翻、撤回、过期、修订都会让摘要变化；摘要没变就不重编。超出时间预算或失败时不写库，结果为 template_only。
- 读取 read(student, subject)：每次按判断现状重核（状态、可见范围、模板键与版本、渲染）后渲染：
  页的摘要等于现状 → 已编译版本（compiled），但被推翻、撤回、过期、转待验证的陈述消失，消失了陈述的那一节连同衔接语一起不显示衔接语；
  没有页或摘要已过时（判断变了还没重编）→ 由现有判断直接渲染纯模板版本（template_only，没有衔接语，不写库）。
  读取结果是 Agent 完整版（含待办），只给编排层；学生、家长视图由 BFF 经 platform.profile_page_statement_v 读（不含待办）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..store import learning_tx
from . import consistency
from .config import CONFIG_DIR, TEST_ENV, ConfigError
from .render import render_text

log = logging.getLogger("starbuddy.learning")

SCHEMA_VERSION = "kge-profile-page/0.1"
COMPILER_VERSION = "kge-profile@0.1.0"
SECTION_KEYS = ("current_stage", "mastered", "working_on", "root_cause", "recent_progress", "next_step")
STATEMENT_STATUSES = consistency.VISIBLE_STATUSES          # 成立、假设
TODO_STATUS = "to_verify"
TRIGGERS = ("session_start", "weekly")
SUBJECTS = ("aya", "zhixing", "wenli")                     # 英语搁置
GATES = ("unseen", "understand", "execute", "stable", "transfer", "retain")
TEMPLATE_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$")
CONFIG_VERSION = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,31}$")
FRAGMENT_MAX = 60
_FRAGMENT_DIGITS = re.compile(r"[0-9０-９]")

ConnectiveSource = Callable[[str, Sequence[consistency.Statement]], str]


class ProfileUnavailable(RuntimeError):
    """这一科没有档案页配置，或没有已发布的地图（不能编译）"""


# ---------------------------------------------------------------- 配置

@dataclass(frozen=True)
class SectionRule:
    section: str | None = None
    by_gate: Mapping[str, str] = field(default_factory=dict)
    default: str | None = None

    def section_of(self, value: Mapping[str, Any]) -> str | None:
        if self.section is not None:
            return self.section
        gate = value.get("gate")
        if isinstance(gate, str) and gate in self.by_gate:
            return self.by_gate[gate]
        return self.default


@dataclass(frozen=True)
class ProfileConfig:
    config_version: str
    synthetic: bool
    signed: bool
    subjects: frozenset[str]
    sections: Mapping[str, SectionRule]
    connectives: Mapping[str, str]
    max_per_section: int
    budgets: Mapping[str, float]       # 触发点 → 秒
    check: consistency.CheckConfig
    fingerprint: str


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise ConfigError(msg)


def _fingerprint_doc(d: Any) -> Any:
    """配置指纹只看生效的配置位：去掉 $comment 等注释字段（改注释不应让全部档案页重编）"""
    if isinstance(d, dict):
        return {k: _fingerprint_doc(v) for k, v in d.items() if not (isinstance(k, str) and k.startswith("$"))}
    if isinstance(d, list):
        return [_fingerprint_doc(v) for v in d]
    return d


def parse_profile_config(doc: Mapping[str, Any], check_doc: Mapping[str, Any], *, env: str,
                         forbidden: consistency.Forbidden | None = None) -> ProfileConfig:
    keys = {"schema_version", "config_version", "synthetic", "signed_by", "signed_at", "subjects", "check_config", "sections",
            "connectives", "max_per_section", "budget_ms"}
    _need(isinstance(doc, dict) and set(doc) - {"$comment"} == keys, f"档案页配置的配置位不对（应为 {sorted(keys)}）")
    _need(doc["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(isinstance(doc["config_version"], str) and bool(CONFIG_VERSION.fullmatch(doc["config_version"])), "config_version 格式不对")
    _need(isinstance(doc["synthetic"], bool), "synthetic 必须是布尔值")
    _need(isinstance(doc["signed_by"], str) and isinstance(doc["signed_at"], str), "signed_by / signed_at 必须是字符串")
    signed = bool(doc["signed_by"].strip() and doc["signed_at"].strip()) and not doc["synthetic"]
    if env != TEST_ENV and not signed:
        raise ConfigError("未签字或合成的档案页配置（归节与衔接短语表）只允许 LEARNING_ENV=test 加载（第一步待人确认 #20）")
    check = consistency.parse_check_config(check_doc, env=env)   # 词表本身也要签字（非 test）
    subjects = doc["subjects"]
    _need(isinstance(subjects, list) and len(subjects) >= 1 and set(subjects) <= set(SUBJECTS) and len(set(subjects)) == len(subjects),
          f"subjects 必须是 {SUBJECTS} 里的非空、不重复列表")
    secs = doc["sections"]
    _need(isinstance(secs, dict) and len(secs) >= 1, "sections 必须是非空对象（模板键 → 归节规则）")
    rules: dict[str, SectionRule] = {}
    for key, rule in secs.items():
        _need(isinstance(key, str) and bool(TEMPLATE_KEY.fullmatch(key)), f"sections 的模板键格式不对：{key!r}")
        _need(isinstance(rule, dict), f"sections.{key} 必须是对象")
        if set(rule) == {"section"}:
            _need(rule["section"] in SECTION_KEYS, f"sections.{key}.section 必须是六节之一")
            rules[key] = SectionRule(section=rule["section"])
        else:
            _need(set(rule) == {"by_gate", "default"} and isinstance(rule["by_gate"], dict) and len(rule["by_gate"]) >= 1,
                  f"sections.{key} 只能是 {{section}} 或 {{by_gate, default}}")
            _need(all(g in GATES and s in SECTION_KEYS for g, s in rule["by_gate"].items()), f"sections.{key}.by_gate 的门槛或节不对")
            _need(rule["default"] is None or rule["default"] in SECTION_KEYS, f"sections.{key}.default 必须是六节之一或 null")
            rules[key] = SectionRule(by_gate=dict(rule["by_gate"]), default=rule["default"])
    con = doc["connectives"]
    _need(isinstance(con, dict) and set(con) == set(SECTION_KEYS), f"connectives 必须恰好是六节 {list(SECTION_KEYS)}")
    forbidden = forbidden or consistency.default_forbidden_matcher()
    for k, phrase in con.items():
        _need(isinstance(phrase, str) and 1 <= len(phrase) <= 60 and not _FRAGMENT_DIGITS.search(phrase), f"connectives.{k} 必须是 1–60 字、不含数字")
        _need(consistency.normalize(phrase) in check.connective_phrases, f"connectives.{k} 不在人审过的固定衔接短语表里")
        _need(not forbidden(phrase), f"connectives.{k} 命中禁用推断词表（规则 12）")
    n = doc["max_per_section"]
    _need(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 30, "max_per_section 必须是 1–30 的整数（0037 每节最多 30 项）")
    b = doc["budget_ms"]
    _need(isinstance(b, dict) and set(b) == set(TRIGGERS), f"budget_ms 必须恰好是 {list(TRIGGERS)}")
    _need(all(isinstance(v, int) and not isinstance(v, bool) and 50 <= v <= 10000 for v in b.values()), "budget_ms 每项必须是 50–10000 的整数")
    fp = hashlib.sha256(json.dumps([_fingerprint_doc(doc), _fingerprint_doc(check_doc)], ensure_ascii=False,
                                   sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return ProfileConfig(doc["config_version"], doc["synthetic"], signed, frozenset(subjects), rules, dict(con), n,
                         {k: v / 1000 for k, v in b.items()}, check, fp)


def load_profile_config(path: str | Path, *, env: str) -> ProfileConfig:
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        doc = json.load(f)
    name = doc.get("check_config") if isinstance(doc, dict) else None
    if not isinstance(name, str) or "/" in name or "\\" in name or not name.endswith(".json"):
        raise ConfigError("check_config 必须是同目录下的 .json 文件名")
    with open(p.parent / name, encoding="utf-8") as f:
        check_doc = json.load(f)
    return parse_profile_config(doc, check_doc, env=env)


def synthetic_profile_config() -> ProfileConfig:
    return load_profile_config(CONFIG_DIR / "profile-page.synthetic.json", env=TEST_ENV)


# ---------------------------------------------------------------- 纯函数：候选、摘要、编译、重核

@dataclass(frozen=True)
class ClaimRow:
    """学习服务读到的一条判断（Agent 可见）与它的模板文本"""
    claim_id: str
    subject: str | None
    target_id: str
    template_key: str
    template_version: int
    status: str
    value: Mapping[str, Any]
    text_template: str | None
    updated_at: datetime


def candidates(rows: Sequence[ClaimRow], cfg: ProfileConfig) -> list[ClaimRow]:
    """候选判断：模板在归节配置里、状态是成立 / 假设 / 待验证；按 (目标, 模板, 判断 ID) 排序（确定性）"""
    keep = [r for r in rows if r.template_key in cfg.sections and (r.status in STATEMENT_STATUSES or r.status == TODO_STATUS)]
    return sorted(keep, key=lambda r: (r.target_id, r.template_key, r.template_version, r.claim_id))


def claims_digest(cands: Sequence[ClaimRow], cfg: ProfileConfig) -> str:
    """候选判断的摘要（sha256 hex）：判断新增、推翻、撤回、过期、修订（updated_at 变）都会变；配置或编译器升级也会变"""
    lines = [COMPILER_VERSION, cfg.fingerprint]
    lines += sorted(f"{r.claim_id}|{r.updated_at.isoformat()}|{r.status}|{r.template_key}@{r.template_version}" for r in cands)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _ref(r: ClaimRow) -> dict[str, Any]:
    return {"claim_id": r.claim_id, "template_key": r.template_key, "template_version": r.template_version}


def _fragment(v: consistency.Violation) -> str:
    """阻断记录的片段：禁用推断命中不存（规则 12）；含数字的不存（0037 约束）；截到 60 字"""
    if v.code == "forbidden_inference" or _FRAGMENT_DIGITS.search(v.fragment):
        return ""
    return v.fragment[:FRAGMENT_MAX]


@dataclass(frozen=True)
class Compiled:
    sections: list[dict[str, Any]]      # platform.profile_page.sections
    check_status: str                   # passed / blocked_fallback
    violations: list[dict[str, Any]]    # platform.profile_page_check.violations（空 = 没有阻断）


def compile_page(cands: Sequence[ClaimRow], cfg: ProfileConfig, forbidden: consistency.Forbidden,
                 connective: ConnectiveSource | None = None) -> Compiled:
    """编译：归节、模板渲染、固定衔接短语、一致性检查；不过即降级为纯模板版本。connective 只供测试注入（默认固定短语表）"""
    source = connective or (lambda key, stmts: cfg.connectives[key])
    stmts: dict[str, list[tuple[ClaimRow, str]]] = {k: [] for k in SECTION_KEYS}
    todo: dict[str, list[ClaimRow]] = {k: [] for k in SECTION_KEYS}
    for r in cands:
        sec = cfg.sections[r.template_key].section_of(r.value)
        if sec is None:
            continue
        if r.status == TODO_STATUS:
            if len(todo[sec]) < cfg.max_per_section:
                todo[sec].append(r)
            continue
        text = render_text(r.text_template, r.target_id, r.value, forbidden)
        if text is not None and len(stmts[sec]) < cfg.max_per_section:
            stmts[sec].append((r, text))
    check_secs = []
    for k in SECTION_KEYS:
        st = tuple(consistency.Statement(r.claim_id, text) for r, text in stmts[k])
        check_secs.append(consistency.Section(k, source(k, st) if st else "", st))
    views = {r.claim_id: consistency.ClaimView(r.claim_id, r.status, r.template_key) for r in cands}
    page = consistency.finalize(check_secs, views, cfg.check, forbidden)
    by_id = {r.claim_id: r for r in cands}
    sections = [{"key": s.key, "connective": s.connective, "statements": [_ref(by_id[st.claim_id]) for st in s.statements],
                 "todo": [_ref(r) for r in todo[s.key]]} for s in page.sections]
    violations = [{"code": v.code, "section": v.section, "claim_ids": [c for c in v.claim_ids if c in by_id][:30], "fragment": _fragment(v)}
                  for v in page.violations][:60]
    return Compiled(sections, page.check_status, violations)


def render_sections(stored: Sequence[Mapping[str, Any]], current: Mapping[str, ClaimRow], cfg: ProfileConfig,
                    forbidden: consistency.Forbidden) -> list[dict[str, Any]]:
    """读取时按判断现状重核后渲染（只用现状，不信页里存的状态）：
    陈述要求判断仍可读、仍是成立或假设、模板键与版本没变、模板仍在归节配置里、渲染得出来；待办要求仍是待验证。
    一节里有任何陈述消失（或该节已没有陈述）就不显示这一节的衔接语；最后再跑一遍一致性检查，不过就去掉全部衔接语（纯模板）。"""
    out: list[dict[str, Any]] = []
    by_key = {s.get("key"): s for s in stored if isinstance(s, Mapping)}
    for k in SECTION_KEYS:
        sec = by_key.get(k) or {"connective": "", "statements": [], "todo": []}
        kept, dropped = [], False
        for ref in sec.get("statements") or []:
            r = current.get(ref.get("claim_id"))
            text = None
            if (r is not None and r.status in STATEMENT_STATUSES and r.template_key == ref.get("template_key")
                    and r.template_version == ref.get("template_version") and r.template_key in cfg.sections):
                text = render_text(r.text_template, r.target_id, r.value, forbidden)
            if text is None:
                dropped = True
                continue
            kept.append({**_ref(r), "status": r.status, "text": text})
        todo = []
        for ref in sec.get("todo") or []:
            r = current.get(ref.get("claim_id"))
            if (r is not None and r.status == TODO_STATUS and r.template_key == ref.get("template_key")
                    and r.template_version == ref.get("template_version")):
                todo.append(_ref(r))
        conn = sec.get("connective") or ""
        out.append({"key": k, "connective": conn if kept and not dropped else "", "statements": kept, "todo": todo})
    check = consistency.check_page(
        [consistency.Section(s["key"], s["connective"], tuple(consistency.Statement(x["claim_id"], x["text"]) for x in s["statements"]))
         for s in out],
        {cid: consistency.ClaimView(cid, r.status, r.template_key) for cid, r in current.items()}, cfg.check, forbidden)
    if not check.passed:
        for s in out:
            s["connective"] = ""
    return out


# ---------------------------------------------------------------- 库

def _value(v: Any) -> Mapping[str, Any]:
    if isinstance(v, str):
        v = json.loads(v)
    return v if isinstance(v, dict) else {}


async def load_claims(conn, student: str, subject: str) -> list[ClaimRow]:
    """以 starbuddy_learning 身份读该生该科（及不分学科）的 Agent 可见判断；RLS（claim_agent_read）之外显式写出同样的条件，
    并要求模板已审核。读不到的判断不进页。"""
    rows = await conn.fetch(
        """SELECT c.id::text AS id, c.subject::text AS subject, c.target_id, c.template_key, c.template_version, c.status::text AS status,
                  c.value::text AS value, c.updated_at, t.text_template
             FROM insight.claim c
             JOIN insight.claim_template t ON t.template_key = c.template_key AND t.version = c.template_version AND t.status = 'approved'
            WHERE c.student_id = $1::uuid AND (c.subject = $2::platform.subject OR c.subject IS NULL)
              AND c.status IN ('hypothesis', 'established', 'to_verify') AND c.expires_at > now() AND 'agent' = ANY (c.visibility)
            ORDER BY c.id""", student, subject)
    return [ClaimRow(r["id"], r["subject"], r["target_id"], r["template_key"], r["template_version"], r["status"], _value(r["value"]),
                     r["text_template"], r["updated_at"]) for r in rows]


@dataclass(frozen=True)
class EnsureResult:
    outcome: str                  # unchanged / compiled / blocked_fallback / empty / template_only
    check_status: str | None = None
    compiled_at: datetime | None = None
    profile_page_id: str | None = None


class ProfilePages:
    """学习服务里的档案页入口：ensure（惰性编译）与 read（读取时重核后渲染）。stats 记各结果的次数（compiled = 实际编译次数）。"""

    def __init__(self, pool, cfg: ProfileConfig, *, forbidden: consistency.Forbidden | None = None) -> None:
        self.pool = pool
        self.cfg = cfg
        self.forbidden = forbidden or consistency.default_forbidden_matcher()
        self.stats: Counter[str] = Counter()

    def _check(self, student: str, subject: str) -> str:
        if subject not in self.cfg.subjects:
            raise ProfileUnavailable("no profile page configuration for this subject")
        return str(uuid.UUID(student))

    async def ensure(self, student: str, subject: str, trigger: str, *, budget: float | None = None,
                     connective: ConnectiveSource | None = None) -> EnsureResult:
        if trigger not in TRIGGERS:
            raise ValueError("trigger must be session_start or weekly")
        student = self._check(student, subject)
        try:
            return await asyncio.wait_for(self._ensure(student, subject, connective),
                                          self.cfg.budgets[trigger] if budget is None else budget)
        except asyncio.TimeoutError:
            # 超出时间预算：本次不写库（未提交的事务回滚），返回纯模板版本，不阻塞会话
            self.stats["template_only"] += 1
            self.stats["timeout"] += 1
            log.warning(json.dumps({"event": "profile_page_timeout", "subject": subject, "trigger": trigger}, ensure_ascii=False))
            return EnsureResult("template_only")
        except ProfileUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — 编译失败不阻塞会话与周报；日志不记学生、判断与异常消息
            self.stats["template_only"] += 1
            self.stats["failed"] += 1
            log.warning(json.dumps({"event": "profile_page_failed", "subject": subject, "trigger": trigger,
                                    "error_class": type(e).__name__[:48]}, ensure_ascii=False))
            return EnsureResult("template_only")

    async def _ensure(self, student: str, subject: str, connective: ConnectiveSource | None) -> EnsureResult:
        async with learning_tx(self.pool) as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"platform.profile_page:{student}:{subject}")
            # 已执行删除的学生（0035 student_profile.erased_at）：不再写任何个人层行（第二步 lessons 待人确认 I 的档案页部分）
            if await conn.fetchval("SELECT erased_at IS NOT NULL FROM platform.student_profile WHERE id = $1::uuid", student):
                self.stats["erased"] += 1
                return EnsureResult("empty")
            cands = candidates(await load_claims(conn, student, subject), self.cfg)
            digest = claims_digest(cands, self.cfg)
            page = await conn.fetchrow(
                """SELECT id::text AS id, claims_digest, check_status, compiled_at FROM platform.profile_page
                    WHERE student_id = $1::uuid AND subject = $2::platform.subject""", student, subject)
            if page is not None and page["claims_digest"] == digest:
                self.stats["unchanged"] += 1
                return EnsureResult("unchanged", page["check_status"], page["compiled_at"], page["id"])
            if page is None and not cands:
                self.stats["empty"] += 1
                return EnsureResult("empty")
            map_version = await conn.fetchval(
                "SELECT id::text FROM kge.map_version WHERE subject = $1::platform.subject AND status = 'published'", subject)
            if map_version is None:
                raise ProfileUnavailable("no published map for this subject")
            compiled = compile_page(cands, self.cfg, self.forbidden, connective)
            self.stats["compiled"] += 1
            row = await conn.fetchrow(
                """INSERT INTO platform.profile_page (student_id, subject, map_version_id, sections, claims_digest, compiler_version,
                                                      model_version, check_status)
                   VALUES ($1::uuid, $2::platform.subject, $3::uuid, $4::text::jsonb, $5, $6, NULL, $7)
                   ON CONFLICT (student_id, subject) DO UPDATE
                     SET map_version_id = EXCLUDED.map_version_id, sections = EXCLUDED.sections, claims_digest = EXCLUDED.claims_digest,
                         compiler_version = EXCLUDED.compiler_version, model_version = NULL, check_status = EXCLUDED.check_status
                   RETURNING id::text AS id, compiled_at, check_status""",
                student, subject, map_version, json.dumps(compiled.sections, ensure_ascii=False), digest, COMPILER_VERSION,
                compiled.check_status)
            if compiled.violations:
                await conn.execute(
                    """INSERT INTO platform.profile_page_check (student_id, subject, profile_page_id, compiler_version, violations)
                       VALUES ($1::uuid, $2::platform.subject, $3::uuid, $4, $5::text::jsonb)""",
                    student, subject, row["id"], COMPILER_VERSION, json.dumps(compiled.violations, ensure_ascii=False))
                self.stats["blocked"] += 1
            return EnsureResult("compiled" if compiled.check_status == "passed" else "blocked_fallback", row["check_status"],
                                row["compiled_at"], row["id"])

    async def read(self, student: str, subject: str) -> dict[str, Any]:
        """Agent 完整版（含待办）。只读，不写库、不编译。"""
        student = self._check(student, subject)
        async with learning_tx(self.pool) as conn:
            cands = candidates(await load_claims(conn, student, subject), self.cfg)
            page = await conn.fetchrow(
                """SELECT id::text AS id, sections::text AS sections, claims_digest, check_status, compiled_at, compiler_version::text AS cv
                     FROM platform.profile_page WHERE student_id = $1::uuid AND subject = $2::platform.subject""", student, subject)
        current = {r.claim_id: r for r in cands}
        if page is not None and page["claims_digest"] == claims_digest(cands, self.cfg):
            sections = render_sections(json.loads(page["sections"]), current, self.cfg, self.forbidden)
            return {"subject": subject, "variant": "compiled", "profile_page_id": page["id"], "check_status": page["check_status"],
                    "compiled_at": page["compiled_at"].isoformat(), "compiler_version": page["cv"], "sections": sections}
        # 没有页或判断已变（还没重编）：由现有判断直接渲染纯模板版本（不写库）
        bare = compile_page(cands, self.cfg, self.forbidden, lambda key, stmts: "")
        sections = render_sections(bare.sections, current, self.cfg, self.forbidden)
        return {"subject": subject, "variant": "template_only", "profile_page_id": None, "check_status": None, "compiled_at": None,
                "compiler_version": COMPILER_VERSION, "sections": sections}
