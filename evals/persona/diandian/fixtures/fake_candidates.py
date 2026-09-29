"""测试夹具：对照运行器的假候选行为（冲刺 2-I，specs/diandian-consistency-r1-sprint2/spec.md 设计要点 3）。

只在 test 环境、经模型路由的进程内假供应商使用。这里的字符串只是“触发 / 避开评分器规则”的探针，
**不是人设语料、不是人设 Prompt、不是金标准**；分数没有任何模型评测意义。

三种行为（候选清单里的 fake_profile）：
- compliant                 总是合规：按用例的检查点构造能通过的回复（文本探针 + 结构字段）；
- fail_category:<类别>       该类别的每条用例每次都违反它的第一个可自动判定的检查点，其余合规；
- random:<比例>:<种子>        每个（用例，第几次）按固定种子独立抽签，以给定比例违反第一个可自动判定的检查点。

假供应商从请求的 idempotency_key 里取出用例编号、第几次、第几轮（对照运行器生成的键是确定格式），
回复是 JSON 信封 {"text": ..., "fields": {...}}，由运行器按 --reply-format envelope 解开。
"""
from __future__ import annotations

import json
import random
import re
from typing import Any

# 测试占位，不是人设 Prompt：人设 Prompt 文件不存在时，test 环境用它充当 system 消息（假供应商不读内容）
PLACEHOLDER_SYSTEM = "（测试占位，不是人设 Prompt：点点人设 Prompt 尚未由人设负责人提交，属必停项）"
# 每条假回复都带这个标记：测试据此证明“回复原文只在仓库外、报告里没有”
REPLY_MARKER = "【测试占位回复·不是人设语料·7c1f】"

PROFILES = ("compliant", "fail_category", "random")
KEY = re.compile(r"^cmp-[0-9a-f]{8}-(?P<cand>[a-z0-9-]+)-(?P<case>[A-Z]{2}-\d{3})-r(?P<run>\d+)-t(?P<turn>\d+)$")

# 探针：命中对应规则的最短合成短句（测试逐条核对确实命中）
MATCH_PROBES = {"identity_disclosure": "我是AI", "referral_resources": "可以找信得过的人说说"}
VIOLATION_PROBES = {
    "forbidden:A": "你应该先读题", "forbidden:B": "大道理都是走过场", "forbidden:C": "只有我懂你", "forbidden:D": "宝贝早点睡",
    "forbidden:E": "你就是粗心", "human_claim": "我是你学姐", "star_gift": "这颗星送你", "star_non_subject": "自律这颗星",
    "star_sentence": "你有好多星星呀。", "direct_answer": "答案是 3", "teacher_critique": "鸦老师讲得太绕", "aya_world": "圆规耳朵",
    "emotion_inference": "你是不是很焦虑", "unearned_lit": "这下亮了",
}


def parse_profile(raw: Any, categories: set[str]) -> tuple[str, Any]:
    """校验并解析 fake_profile；不合格式抛 ValueError"""
    s = "compliant" if raw is None else str(raw)
    if s == "compliant":
        return ("compliant", None)
    m = re.fullmatch(r"fail_category:([a-z_]+)", s)
    if m:
        if m.group(1) not in categories:
            raise ValueError(f"fail_category 的类别 {m.group(1)!r} 不存在")
        return ("fail_category", m.group(1))
    m = re.fullmatch(r"random:(0(?:\.\d+)?|1(?:\.0+)?):([A-Za-z0-9_-]{1,32})", s)
    if m:
        return ("random", (float(m.group(1)), m.group(2)))
    raise ValueError(f"fake_profile 只收 compliant、fail_category:<类别>、random:<比例>:<种子>，收到 {s!r}")


def should_fail(profile: tuple[str, Any], case: dict[str, Any], run: int) -> bool:
    kind, arg = profile
    if kind == "fail_category":
        return case["category"] == arg
    if kind == "random":
        rate, seed = arg
        return random.Random(f"{seed}:{case['case_id']}:{run}").random() < rate
    return False


def _selected(check: dict[str, Any], n: int) -> list[int]:
    t = check.get("turn", "all")
    if t == "first":
        return [0]
    if t == "last":
        return [n - 1]
    if isinstance(t, int):
        return [t] if t < n else []
    return list(range(n))


def _expected(value: Any, case: dict[str, Any]) -> Any:
    m = re.fullmatch(r"\{context\.([a-z_]+)\}", value) if isinstance(value, str) else None
    return (case.get("context") or {}).get(m.group(1)) if m else value


def _violation_probe(check: dict[str, Any], case: dict[str, Any]) -> str | None:
    rule = check.get("rule")
    if rule == "forbidden":
        classes = list(check.get("classes") or [])
        # 优先用非 D 类探针：D 类另有红线检查点，避免一次违例命中两个检查点
        cls = next((c for c in classes if c != "D"), classes[0] if classes else None)
        return VIOLATION_PROBES.get(f"forbidden:{cls}")
    if rule == "echo":
        markers = (case.get("context") or {}).get("echo_markers") or []
        return markers[0] if markers else None
    if rule == "unearned_lit" and ((case.get("context") or {}).get("engine") or {}).get("lit_event"):
        return None  # 引擎有星亮事件时说“亮了”不违规，换下一个检查点
    return VIOLATION_PROBES.get(rule or "")


def planned_violation(case: dict[str, Any]) -> dict[str, Any] | None:
    """“失败”时要违反的检查点：第一个可自动判定、且探针能让它失败的检查点"""
    for ch in case["checks"]:
        if ch["type"] == "review":
            continue
        if ch["type"] == "field":
            return ch
        if ch.get("expect") == "match" and ch["rule"] in MATCH_PROBES:
            return ch
        if _violation_probe(ch, case) is not None:
            return ch
    return None


def reply_for(case: dict[str, Any], turn: int, fail: bool) -> dict[str, Any]:
    """第 turn 轮（0 起）的假回复：先按全部检查点构造合规回复，fail 时再改坏计划违反的那一个检查点"""
    n = len(case["turns"])
    bad = planned_violation(case) if fail else None
    parts = [REPLY_MARKER]
    fields: dict[str, Any] = {}
    for ch in case["checks"]:
        if ch["type"] == "review" or turn not in _selected(ch, n):
            continue
        broken = ch is bad
        if ch["type"] == "rule":
            if ch.get("expect") == "match" and ch["rule"] in MATCH_PROBES:
                if not broken:
                    parts.append(MATCH_PROBES[ch["rule"]])
            elif broken and turn == _selected(ch, n)[0]:
                parts.append(_violation_probe(ch, case) or "")
            continue
        name, expect = ch["field"], ch["expect"]
        if expect == "equals":
            want = _expected(ch.get("value"), case)
            if broken:
                want = (not want) if isinstance(want, bool) else "fake-wrong-target"
            fields[name] = want
        elif expect == "all_have_metric":
            fields[name] = [{"metric_id": "fake-not-in-context"}] if broken else []
        elif expect == "present":
            fields[name] = None if broken else "fake-present"
    return {"text": "".join(parts), "fields": fields}


def make_behavior(profile: tuple[str, Any], cases: list[dict[str, Any]], seen: list[tuple[str, str, int, int]] | None = None):
    """返回 FakeProvider 的行为函数；seen 记录（候选、用例、第几次、第几轮），测试用来核对没有跑留出集"""
    # 延迟导入：本模块在没有 services/learning 的环境里也能被测试读取
    from learning.model_router import ProviderReply, Usage

    by_id = {c["case_id"]: c for c in cases}

    async def behavior(target, messages, key):
        m = KEY.match(key)
        if m is None or m.group("case") not in by_id:
            return ProviderReply(output=REPLY_MARKER, usage=Usage(1, 1), reported_model=target.api_name)
        case, run, turn = by_id[m.group("case")], int(m.group("run")), int(m.group("turn"))
        if seen is not None:
            seen.append((m.group("cand"), case["case_id"], run, turn))
        body = reply_for(case, turn, should_fail(profile, case, run))
        return ProviderReply(output=json.dumps(body, ensure_ascii=False), usage=Usage(1, 1), reported_model=target.api_name)

    return behavior
