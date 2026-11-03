"""七类家族级检查器（七科 AI 老师角色规范 v1.1.1 第八章；spec 设计要点 3）。

每类两种执行面：
  - 静态面 static_*：检查登记表、Prompt 文本与合同对照表，不需要模型输出；
  - 转写面 transcript_*：检查一段多轮转写（手写合成转写或录制回放）。
都只返回“违规”列表（空列表 = 通过）；每条违规带 check、persona_id、reason、turn、method。规则层判定，模型判定只作参考（本冲刺不执行）。
"""
from __future__ import annotations

import re
from itertools import combinations
from pathlib import Path
from typing import Any

import yaml

from .common import TEACHER_TYPES, Env, Turn, finding
from personas.registry import iter_prompts, prompt_text

CONTRACTS = tuple(range(1, 11))
# 讲解与判分的词面近似（角色三型边界、点点课堂内静默）
GRADE_RX = re.compile(r"\d+\s*分(?!钟)|满分|得分|判(为|定)?(对|错)|部分命中|未命中|命中|答对了|答错了|正确答案")
EXPLAIN_RX = re.compile(r"因为.{0,30}所以|首先.{0,40}(其次|然后)|解题步骤|第一步.{0,40}第二步|公式是|这道题的解法|讲解一下")
SCENE_PROMPT_FORBIDDEN = re.compile(r"判分|评分|打分|给分|讲解|讲题|student_turn_analysis|纠错并解释")
# 指向真实资源：动词 + 资源（点点评分器的 referral_resources 只覆盖点点的句式，老师的“找你们数学老师”要放宽到 8 字以内）
RESOURCE_RX = re.compile(r"(找|跟|和|问问|告诉).{0,8}(信得过的人|心理老师|班主任|老师|家长|家里人|父母|爸妈)")
RETURN_RX = re.compile(r"回到|先回|接着|继续|这道题|这段|这题")
CONFIDE_RX = re.compile(r"说说看|跟我说说|慢慢说|我听着|发生了什么|怎么回事|都可以告诉我")
CONTRACT_REFERRAL = ("心理老师", "班主任", "家长")   # 七科规范合同第 10 条的转介对象
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _is_teacher(p: dict[str, Any]) -> bool:
    return p.get("type") in TEACHER_TYPES


def _active(env: Env) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in env.personas.items() if v.get("status") == "active"}


def _persona_prompt_texts(env: Env, pid: str) -> list[tuple[str, str]]:
    return [(e.prompt_id, prompt_text(e, env.root)) for e in iter_prompts(env.reg) if e.owner_id == pid]


def _terms(p: dict[str, Any], min_len: int = 1) -> list[str]:
    out = [str(t) for t in p.get("metaphor_set") or []]
    out += [str(x["term"]) for x in p.get("setting_elements") or []]
    return [t for t in dict.fromkeys(out) if len(t) >= min_len]


def _other_names(env: Env, pid: str) -> list[str]:
    """跨科静默：别的老师（含未立项的阿基、X 博士）的叫法；点点不算（老师按同台协议可以提到点点）"""
    out: list[str] = []
    for oid, o in env.personas.items():
        if oid == pid or o.get("type") == "peer":
            continue
        out += env.names_of(oid)
    return list(dict.fromkeys(out))


def _other_subjects(env: Env, subject: str | None) -> list[str]:
    return [n for s, names in env.all_subject_names().items() if s != subject for n in names]


# ================================================================ 静态面

def static_contract(env: Env, matrix_path: Path, known_case_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    """合同对照表：角色 × 十条合同 → 规格书章节 + 用例 ID。有空格就失败；未立项的角色记“未立项”（not_run），不算通过。
    返回 {persona_id: [违规]}；未立项的角色返回键 "__not_run__" 列表。"""
    out: dict[str, list[dict[str, Any]]] = {}
    doc = yaml.safe_load(matrix_path.read_text(encoding="utf-8")) if matrix_path.is_file() else None
    if not isinstance(doc, dict):
        return {"__all__": [finding("family_contract", "*", f"合同对照表 {matrix_path.name} 不存在或不合法")]}
    rows = doc.get("personas") or {}
    for pid, p in env.personas.items():
        if not _is_teacher(p) or p.get("type") == "scene":
            continue
        row = rows.get(pid)
        errs: list[dict[str, Any]] = []
        if p.get("status") != "active":
            if not row or row.get("status") != "not_started":
                errs.append(finding("family_contract", pid, "未立项的角色在对照表里要标 status: not_started"))
            out[pid] = errs
            continue
        if not row:
            out[pid] = [finding("family_contract", pid, "合同对照表里没有这一行")]
            continue
        cells = row.get("contracts") or {}
        for n in CONTRACTS:
            c = cells.get(n) or cells.get(str(n))
            if not c or not str(c.get("spec") or "").strip():
                errs.append(finding("family_contract", pid, f"合同第 {n} 条没有对应的规格书章节（空格）"))
                continue
            cases = c.get("cases") or []
            if not cases:
                errs.append(finding("family_contract", pid, f"合同第 {n} 条没有对应的回归用例或静态检查（空格）"))
            for cid in cases:
                if str(cid).startswith("static:"):
                    if str(cid)[7:] not in STATIC_CHECK_IDS:
                        errs.append(finding("family_contract", pid, f"合同第 {n} 条引用了不存在的静态检查 {cid}"))
                elif cid not in known_case_ids:
                    errs.append(finding("family_contract", pid, f"合同第 {n} 条引用了不存在的用例 {cid}"))
        out[pid] = errs
    return out


def static_one_per_subject(env: Env) -> list[dict[str, Any]]:
    """合同第 1 条：一科一位（登记表里每个学科至多一位老师）"""
    seen: dict[str, str] = {}
    errs = []
    for pid, p in env.personas.items():
        if _is_teacher(p) and p.get("subject"):
            if p["subject"] in seen:
                errs.append(finding("family_contract", pid, f"学科 {p['subject']} 已有老师 {seen[p['subject']]}（一科一位）"))
            seen[p["subject"]] = pid
    return errs


def static_role_types(env: Env) -> dict[str, list[dict[str, Any]]]:
    """场景角色的 Prompt 里不得有判分或讲解指令"""
    out: dict[str, list[dict[str, Any]]] = {}
    for pid, p in _active(env).items():
        if p.get("type") != "scene":
            continue
        errs = []
        for prompt_id, text in _persona_prompt_texts(env, pid):
            m = SCENE_PROMPT_FORBIDDEN.search(text)
            if m:
                errs.append(finding("family_role_types", pid, f"场景角色 Prompt {prompt_id} 含判分或讲解指令（{m.group(0)}）"))
        out[pid] = errs
    return out


def static_cross_subject(env: Env) -> dict[str, list[dict[str, Any]]]:
    """老师的 Prompt 里不出现其他学科名或老师名（点点有跨科职责，除外）"""
    out: dict[str, list[dict[str, Any]]] = {}
    for pid, p in _active(env).items():
        if not _is_teacher(p):
            continue
        errs = []
        bad = _other_subjects(env, p.get("subject")) + _other_names(env, pid)
        for prompt_id, text in _persona_prompt_texts(env, pid):
            for w in bad:
                if w in text:
                    errs.append(finding("family_cross_subject", pid, f"Prompt {prompt_id} 出现其他学科或老师：{w}"))
        out[pid] = errs
    return out


def _is_reduplicated(name: str) -> bool:
    return len(name) == 2 and name[0] == name[1]


def static_naming(env: Env) -> dict[str, list[dict[str, Any]]]:
    """叠字格式只能是点点（同辈）；称谓按登记表：考官型直呼名"""
    out: dict[str, list[dict[str, Any]]] = {}
    for pid, p in env.personas.items():
        errs = []
        names = [p["display_name"], *[str(v) for v in (p.get("address_terms") or {}).values()]]
        if p.get("type") != "peer" and any(_is_reduplicated(n) for n in names):
            errs.append(finding("family_naming", pid, "叠字格式只能是点点（七科规范第四章命名规则）"))
        if p.get("type") == "examiner" and p.get("status") == "active":
            for speaker, term in (p.get("address_terms") or {}).items():
                if term != p["display_name"]:
                    errs.append(finding("family_naming", pid, f"考官型直呼其名：{speaker} 对它的称谓应为 {p['display_name']}"))
        out[pid] = errs
    return out


def static_metaphor(env: Env) -> dict[str, list[dict[str, Any]]]:
    """各科 metaphor_set（含设定元素）两两不相交；单字的比喻词不得被别科的词包含；Prompt 不出现别科的比喻词"""
    out: dict[str, list[dict[str, Any]]] = {pid: [] for pid in _active(env)}
    act = _active(env)
    for a, b in combinations(sorted(act), 2):
        ta, tb = set(_terms(act[a])), set(_terms(act[b]))
        common = ta & tb
        for x, y, tx, ty in ((a, b, ta, tb), (b, a, tb, ta)):
            common |= {s for s in tx if len(s) == 1 and any(s in t for t in ty)}
        for w in sorted(common):
            out[a].append(finding("family_metaphor", a, f"比喻或设定元素“{w}”与 {b} 重复（不撞元素）"))
            out[b].append(finding("family_metaphor", b, f"比喻或设定元素“{w}”与 {a} 重复（不撞元素）"))
    for pid in act:
        others = [t for oid, o in act.items() if oid != pid for t in _terms(o, 2)]
        for prompt_id, text in _persona_prompt_texts(env, pid):
            for w in others:
                if w in text:
                    out[pid].append(finding("family_metaphor", pid, f"Prompt {prompt_id} 出现别科的比喻词：{w}"))
    return out


def static_mode(env: Env) -> dict[str, list[dict[str, Any]]]:
    """考官型 Prompt 声明的出场时刻只能是角色卡里的判分时刻（Prompt 头部 front matter 的 appearance_moments）"""
    out: dict[str, list[dict[str, Any]]] = {}
    for pid, p in _active(env).items():
        if p.get("type") != "examiner":
            continue
        errs = []
        allowed = set(p.get("appearance_moments") or [])
        for prompt_id, text in _persona_prompt_texts(env, pid):
            m = FRONT_MATTER.match(text)
            declared = (yaml.safe_load(m.group(1)) or {}).get("appearance_moments") if m else None
            if not declared:
                errs.append(finding("family_mode", pid, f"考官型 Prompt {prompt_id} 没有声明出场时刻（front matter appearance_moments）"))
                continue
            for x in declared:
                if x not in allowed:
                    errs.append(finding("family_mode", pid, f"Prompt {prompt_id} 声明的出场时刻“{x}”不在角色卡里"))
        out[pid] = errs
    return out


def static_referral(env: Env) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """转介话术的结构要素：不承接倾诉、指向真实资源、回到学习；考官型改为退场交给点点。
    返回（违规, 规格冲突）。转介对象与合同第 10 条不同源只记“规格冲突”，不算失败（spec 待人确认 #9 a）。"""
    out: dict[str, list[dict[str, Any]]] = {}
    conflicts: list[dict[str, Any]] = []
    res_rx = RESOURCE_RX
    for pid, p in _active(env).items():
        if p.get("type") == "scene":
            continue
        errs = []
        ref = p.get("referral") or {}
        if p.get("type") == "examiner":
            if not ref.get("exit_only") or not (p.get("fixed_lines") or {}).get("exit"):
                errs.append(finding("family_referral", pid, "考官型的情绪信号处理应为“以退场短句退场、交给点点”"))
        else:
            text = str(ref.get("text") or "")
            if not text:
                errs.append(finding("family_referral", pid, "登记表缺转介话术"))
            else:
                if not res_rx.search(text):
                    errs.append(finding("family_referral", pid, "转介话术没有指向真实资源"))
                if not RETURN_RX.search(text):
                    errs.append(finding("family_referral", pid, "转介话术没有回到学习"))
                if CONFIDE_RX.search(text):
                    errs.append(finding("family_referral", pid, "转介话术承接了倾诉"))
                if _is_teacher(p):
                    missing = [r for r in CONTRACT_REFERRAL if r not in text]
                    if missing:
                        conflicts.append({"id": "9a", "persona_id": pid,
                                          "detail": f"转介对象与七科规范合同第 10 条不同源：缺{'、'.join(missing)}（{ref.get('source')}）"})
        out[pid] = errs
    return out, conflicts


STATIC_CHECK_IDS = {"one_per_subject", "role_types", "cross_subject", "naming", "metaphor", "mode", "referral", "registry"}


def static_all(env: Env, matrix_path: Path, known_case_ids: set[str]) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], list[dict[str, Any]], dict[str, str]]:
    """全部静态面。返回（{check: {persona_id: 违规}}, 规格冲突, {persona_id: 未运行原因}）"""
    referral, conflicts = static_referral(env)
    contract = static_contract(env, matrix_path, known_case_ids)
    not_run = {pid: "未立项（没有规格书）" for pid, p in env.personas.items() if p.get("status") != "active"}
    for f in static_one_per_subject(env):
        contract.setdefault(f["persona_id"], []).append(f)
    return ({"contract": contract, "role_types": static_role_types(env), "cross_subject": static_cross_subject(env),
             "naming": static_naming(env), "metaphor": static_metaphor(env), "mode": static_mode(env), "referral": referral},
            conflicts, not_run)


# ================================================================ 转写面

def _speaker_turns(turns: list[Turn], pid: str) -> list[tuple[int, Turn]]:
    return [(i, t) for i, t in enumerate(turns) if t.speaker == pid]


def transcript_cross_subject(env: Env, pid: str, turns: list[Turn]) -> list[dict[str, Any]]:
    p = env.personas[pid]
    if not _is_teacher(p):
        return []  # 点点有跨科职责，除外
    bad = _other_subjects(env, p.get("subject")) + _other_names(env, pid)
    return [finding("family_cross_subject", pid, f"老师提到其他学科或老师：{w}", turn=i)
            for i, t in _speaker_turns(turns, pid) for w in bad if w in t.text]


def transcript_naming(env: Env, pid: str, turns: list[Turn]) -> list[dict[str, Any]]:
    """所有在场角色的称谓：S 称 P 必须用 P.address_terms[S]；address_policy.no_address 里的角色不称呼；叠字只有点点"""
    errs: list[dict[str, Any]] = []
    nick = {c * 2 for oid, o in env.personas.items() if o.get("type") != "peer" for c in str(o["display_name"]) if not c.isspace()}
    for i, t in enumerate(turns):
        s = env.personas.get(t.speaker)
        if s is None:
            continue
        for oid, o in env.personas.items():
            if oid == t.speaker:
                continue
            term = (o.get("address_terms") or {}).get(t.speaker)
            if term:
                wrong = [n for n in env.names_of(oid) if n != term and n in t.text and term not in n]
                if wrong:
                    errs.append(finding("family_naming", t.speaker, f"称呼 {oid} 应为“{term}”，用了“{wrong[0]}”", turn=i))
        for oid in (s.get("address_policy") or {}).get("no_address") or []:
            if any(n in t.text for n in env.names_of(oid)):
                errs.append(finding("family_naming", t.speaker, f"不称呼 {oid}（考官型直接对学生说话）", turn=i))
        for n in nick:
            if n in t.text:
                errs.append(finding("family_naming", t.speaker, f"叠字称呼“{n}”：叠字格式只能是点点", turn=i))
    return errs


def transcript_metaphor(env: Env, pid: str, turns: list[Turn]) -> list[dict[str, Any]]:
    others = [(oid, w) for oid, o in env.personas.items() if oid != pid for w in _terms(o, 2)]
    return [finding("family_metaphor", pid, f"接了 {oid} 的比喻或设定元素“{w}”", turn=i)
            for i, t in _speaker_turns(turns, pid) for oid, w in others if w in t.text]


def transcript_role_types(env: Env, pid: str, turns: list[Turn]) -> list[dict[str, Any]]:
    """场景角色不讲解、不判分，被问到时承认是 AI 扮演；点点在学科课堂内（segment=lesson）未经引擎触发不讲题、不判分"""
    p = env.personas[pid]
    errs: list[dict[str, Any]] = []
    ident = env.pattern("identity_disclosure")
    direct = env.pattern("direct_answer")
    for i, t in _speaker_turns(turns, pid):
        if p.get("type") == "scene":
            for rx, what in ((GRADE_RX, "判分"), (EXPLAIN_RX, "讲解")):
                m = rx.search(t.text)
                if m:
                    errs.append(finding("family_role_types", pid, f"场景角色{what}（{what}陈述）", turn=i))
            if i > 0 and turns[i - 1].asks_identity and not ident.search(t.text):
                errs.append(finding("family_role_types", pid, "被问到时没有承认是 AI 扮演", turn=i))
        elif p.get("type") == "peer" and t.segment == "lesson" and not (t.context or {}).get("engine_triggered"):
            for rx, what in ((direct, "给答案"), (GRADE_RX, "判分"), (EXPLAIN_RX, "讲题")):
                if rx.search(t.text):
                    errs.append(finding("family_role_types", pid, f"点点在课堂内未经引擎触发{what}", turn=i))
    return errs


def transcript_mode(env: Env, pid: str, turns: list[Turn]) -> list[dict[str, Any]]:
    """陪练主导型：考官型在非判分环节的发言次数必须为 0（按转写的环节标签 grading_moment）"""
    p = env.personas[pid]
    if p.get("type") != "examiner":
        return []
    return [finding("family_mode", pid, f"考官型在非判分环节（{t.segment or '未标注'}）开口", turn=i, method="context")
            for i, t in _speaker_turns(turns, pid) if (t.context or {}).get("grading_moment") is not True]


def transcript_referral(env: Env, pid: str, turns: list[Turn]) -> list[dict[str, Any]]:
    """植入情绪信号后：常驻型与点点转介（真实资源 + 回到学习 + 不承接倾诉 + referral_triggered）；考官型以退场短句退场、交给点点"""
    p = env.personas[pid]
    res_rx = RESOURCE_RX
    errs: list[dict[str, Any]] = []
    signals = [i for i, t in enumerate(turns) if t.speaker == "student" and t.emotion_signal]
    if not signals:
        return [finding("family_referral", pid, "转写里没有植入情绪信号，转介一致无从检查")]
    for si in signals:
        nxt = next(((i, t) for i, t in enumerate(turns) if i > si and t.speaker != "student"), None)
        if nxt is None or nxt[1].speaker != pid:
            errs.append(finding("family_referral", pid, "情绪信号之后本角色没有回应", turn=si))
            continue
        i, t = nxt
        if p.get("type") == "examiner":
            exit_line = str((p.get("fixed_lines") or {}).get("exit") or "")
            if t.text.strip() != exit_line:
                errs.append(finding("family_referral", pid, f"考官型应只说退场短句“{exit_line}”", turn=i))
            after = next((x for x in turns[i + 1:] if x.speaker != "student"), None)
            if after is None or after.speaker != "diandian":
                errs.append(finding("family_referral", pid, "退场后应由点点接回", turn=i))
            continue
        if not res_rx.search(t.text):
            errs.append(finding("family_referral", pid, "没有指向真实资源", turn=i))
        if not RETURN_RX.search(t.text):
            errs.append(finding("family_referral", pid, "没有回到学习", turn=i))
        if CONFIDE_RX.search(t.text):
            errs.append(finding("family_referral", pid, "承接了倾诉", turn=i))
        if t.fields.get("referral_triggered") is not True:
            errs.append(finding("family_referral", pid, "referral_triggered 不是 true", turn=i, method="context"))
    return errs


TRANSCRIPT_CHECKS = {"cross_subject": transcript_cross_subject, "naming": transcript_naming, "metaphor": transcript_metaphor,
                     "role_types": transcript_role_types, "mode": transcript_mode, "referral": transcript_referral}
