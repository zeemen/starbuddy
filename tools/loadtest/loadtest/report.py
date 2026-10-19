"""压测报告（Markdown + JSON，只含汇总）：标注“本机基线，不代表生产容量”；不含请求或响应内容、学生 ID、模型标识。"""
from __future__ import annotations

import json

from .metrics import GATE_BUCKETS
from .scenarios import ENDPOINTS, JOURNEYS

BANNER = "本机基线，不代表生产容量"
PEAK_BANNER = "本机合成，不是容量结论"   # PLT-20 --profile peak2x 的报告首段


def _ordered(d: dict, order) -> list[tuple]:
    """按固定顺序（order 里的位置，不在里面的排后、按键）列出：渲染结果不依赖字典的插入顺序，
    由 JSON（sort_keys）读回的报告与内存里的报告渲染出逐字节相同的 Markdown（PLT-21 样例可复现）"""
    pos = {k: i for i, k in enumerate(order)}
    return sorted(d.items(), key=lambda kv: (pos.get(kv[0], len(pos)), kv[0]))


def _fmt(v) -> str:
    return "—" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))


def render_gate(g: dict | None) -> list[str]:
    """分学科并发闸门（PLT-16）：按学科的放行 / 排队后放行 / 被拒与排队毫秒分档（来自 BFF 访问日志的枚举字段）"""
    if not g or not g.get("enabled"):
        return ["", "分学科并发闸门：未打开（--subject-gate 打开；开关关时 BFF 行为与以前逐字节相同）"]
    out = ["", "## 分学科并发闸门（BFF 访问日志汇总）", "",
           "| 学科码 | 立即放行 | 排队后放行 | 被拒 | 绕过（已计量的重试） | 被拒原因 | 排队毫秒分档 |", "|---|---|---|---|---|---|---|"]
    if not g.get("by_subject"):
        out.append("| — | 0 | 0 | 0 | 0 | — | — |")
    for subject, d in sorted(g.get("by_subject", {}).items()):
        reasons = "、".join(f"{k}×{v}" for k, v in sorted(d["reasons"].items())) or "—"
        buckets = "、".join(f"{k}×{v}" for k, v in _ordered(d["queue_ms"], GATE_BUCKETS)) or "—"
        out.append(f"| {subject} | {d['admitted']} | {d['queued']} | {d['rejected']} | {d.get('bypassed', 0)} | {reasons} | {buckets} |")
    return out


SLO_ITEMS = (("p50_ms", "p50 ms"), ("p95_ms", "p95 ms"), ("p99_ms", "p99 ms"), ("error_rate", "错误率"),
             ("throttled_429_rate", "429 占比"), ("subject_busy_503_rate", "503 BUSY 占比"))
VERDICT = {"pass": "通过", "fail": "**未达**", "no_threshold": "无通过线", "no_samples": "无样本"}


def render_slo(s: dict | None, stage_names: list[str]) -> list[str]:
    """SLO 判定（PLT-20）：每组总体判定与逐项值 / 上限，另按阶段给一行判定；未签字时注明“只报告，不影响退出码”"""
    if not s:
        return []
    signed = "已签字" if s["signed"] else f"**{s['status'] or '未签字'}**"
    mode = "严格（未达使退出码失败）" if s.get("strict") else "只报告（未达不影响退出码）"
    out = ["", "## SLO 判定（config/slo.yaml）", "", f"- 状态：{signed}；判定方式：{mode}；边界值算通过；样本按到达阶段归属", "",
           "| 路由组 | 请求数 | 判定 | " + " | ".join(name for _, name in SLO_ITEMS) + " |", "|---|---|---|" + "---|" * len(SLO_ITEMS)]
    for g in s["groups"]:
        cells = []
        for k, _ in SLO_ITEMS:
            v, limit = g["values"].get(k), g["limits"].get(k)
            cells.append(f"{_fmt(v)} / ≤{_fmt(limit)}" if limit is not None else f"{_fmt(v)} / —")
        out.append(f"| {g['group']} | {g['values']['count']} | {VERDICT[g['verdict']]} | " + " | ".join(cells) + " |")
    if any(g["by_stage"] for g in s["groups"]):
        stages = sorted({b["stage"] for g in s["groups"] for b in g["by_stage"]})
        out += ["", "按阶段：", "", "| 路由组 | " + " | ".join(stage_names[i] if i < len(stage_names) else str(i) for i in stages) + " |",
                "|---|" + "---|" * len(stages)]
        for g in s["groups"]:
            by = {b["stage"]: b for b in g["by_stage"]}
            out.append(f"| {g['group']} | " + " | ".join(
                f"{VERDICT[by[i]['verdict']]}（{by[i]['count']}）" if i in by else "—" for i in stages) + " |")
    if s["ungrouped_steps"]:
        out += ["", "无通过线的步骤（不在任何组）：" + "、".join(ENDPOINTS.get(k, k) for k in s["ungrouped_steps"])]
    return out


def render_extras_check(x: dict | None) -> list[str]:
    """可选场景核对（PLT-20 审查 S1、S3、契约 5）：按步骤的真 ID / 真票据与派生路径次数、预期占比与告警、续做核对、月度计划改记"""
    if not x:
        return []
    out = ["", "## 可选场景核对", "",
           "真路径（真 ID / 真票据）必须 2xx；派生 / 预期路径必须全部是预期结果——任一不满足即告警。", "",
           "| 接口 | 真路径 | 派生路径 | 派生中预期占比 | 真路径非 2xx | 派生非预期 | 告警 |", "|---|---|---|---|---|---|---|"]
    alerts = []
    for step, d in sorted(x["paths"].items()):
        share = "—" if d["expected_share"] is None else f"{d['expected_share'] * 100:.1f}%"
        out.append(f"| {ENDPOINTS.get(step, step)} | {d['real']} | {d['derived']} | {share} | {d['real_not_2xx']} | "
                   f"{d['derived_not_expected']} | {'**是**' if d['alert'] else '否'} |")
        if d["alert"]:
            alerts.append(ENDPOINTS.get(step, step))
    if x.get("notes"):
        out += ["", "核对标记：" + "、".join(f"{k} ×{v}" for k, v in sorted(x["notes"].items()))]
        if any(k.endswith(":resumed_new") for k in x["notes"]):
            alerts.append("诊断续做回了新会话（resumed_new）")
    if x.get("monthly_plan_demoted"):
        out += ["", f"家长月度计划：{x['monthly_plan_demoted']} 次 503 在 BFF 访问日志里没有核对到 generate=not_configured，已改记为错误"]
        alerts.append("月度计划的 503 未全部核对到 not_configured")
    out += ["", ("**告警**：" + "；".join(alerts)) if alerts else "无告警"]
    return out


def render_write_path(w: dict | None) -> list[str]:
    """写路径（PLT-21）：是否接上、合成内容与到期条目的种子、作答 / 计次 / 证据一致性核对、各环节耗时（各服务访问日志）"""
    if not w:
        return []
    if not w.get("enabled"):
        return ["", f"写路径（PLT-21）：未接上（{w.get('note') or '原因未知'}）；再练 / 复测作答只到 BFF 票据校验，派生票据按“预期”计"]
    c, s, k = w["content"], w["seeded"], w["check"]
    verdict = {True: "**通过**", False: "**不通过**", None: "不判定（没开作答场景）"}[k["ok"]]
    share = "—" if k["real_share"] is None else f"{k['real_share'] * 100:.1f}%"
    out = ["", "## 写路径（PLT-21）", "",
           f"- 合成地图 aya {c['map_version']}（经两名合成 staff 签字发布）：{len(c['units'])} 个单元（层次 {c['level']}）、"
           f"{c['items']} 道合成题（经准入闸门升为 admitted，打标审核通过）；真知识服务只读这个一次性库，响应由学习服务按契约校验；"
           "判分走阿鸦合成题路径（验证器，确定性；合成阿鸦进程打开了候选版开关，只限该进程）；"
           "合成内容包的准入决定记录是**合成占位摘要，不是真实准入依据**",
           f"- 到期条目种子（压测前准备数据：经学习服务 grade 写入路径，**不经计量**——不预留、不结算、不计次，不计入压测指标）："
           f"{s['students']} 名学生、{s['grades']} 道作答"
           f"（" + "、".join(f"{x}×{n}" for x, n in sorted(s["verdicts"].items())) + f"）、{s['retest_tasks']} 条已到期的平台内验证任务"
           "（**手工种入，不是 KGE 调度**）",
           "- 派生票据与派生条目的“预期”已停用：取题与作答一律按真路径要求 2xx（派生的照发、按错误计）", "",
           "| 核对项 | 值 |", "|---|---|",
           f"| 作答（真票据 2xx） | {k['answers_ok']} |",
           f"| 真票据 / 派生票据 | {k['answers_real']} / {k['answers_derived']}（真路径占比 {share}） |",
           f"| 合成题计次增量（practice_log，经幂等键关联到合成题作答） / 全部计次增量 | {k['counted_delta']} / {k.get('counted_all_delta', '—')} |",
           f"| 作答证据行增量（每次作答一行） | {k['response_evidence_delta']} |",
           f"| 证据行总增量（含判分轨迹） / 每次作答 | {k['evidence_delta']} / {_fmt(k['evidence_per_answer'])} |",
           f"| 计次无对应作答证据 / 作答证据无对应计次（增量） | {k['counted_without_evidence_delta']} / {k['evidence_without_count_delta']} |",
           f"| 结论 | {verdict} |",
           "", "口径：作答 = 合成题计次增量 = 作答证据行增量，且两类“无对应”都为 0、全部计次 = 合成题计次 + 无对应作答证据的计次。"
           "题库自练是“开始即计数、没有释放”（BFF 先计次再调学习服务），计次之后学习服务失败会留下“计次无对应作答证据”——"
           "这是设计口径，不是计量故障；本机无故障注入时应为 0，出现即判不通过。"]
    if k["problems"]:
        out += ["", "问题：" + "；".join(k["problems"])]
    if w.get("segments"):
        out += ["", "各环节耗时（各服务自己的访问日志，只取方法名、状态码、耗时；压测期间，不含种子）：", "",
                "| 环节 | 次数 | 非 2xx | p50 ms | p95 ms | 最大 ms |", "|---|---|---|---|---|---|"]
        for g in w["segments"]:
            out.append(f"| {g['label']} | {g['count']} | {g['not_2xx']} | {_fmt(g['p50_ms'])} | {_fmt(g['p95_ms'])} | {_fmt(g['max_ms'])} |")
        out += ["", "BFF 侧（含计次与转发）见“各接口”表的再练 / 复测取题、作答行；掌握更新：本栈不设掌握配置，作答后不重算（未测）。"]
    return out


def render_stage_detail(r: dict) -> list[str]:
    """各阶段明细（PLT-20 peak2x）：吞吐、分位数、错误率、429 / 503、连接峰值与等待、本阶段慢查询、闸门排队分档"""
    detail = r.get("stage_detail") or []
    if not detail or not r.get("peak2x"):
        return []
    out = ["", "## 2 倍峰值各阶段", "",
           "延迟、吞吐、错误率按到达阶段归属；连接峰值、慢查询、闸门分档按墙钟所在阶段（交界处可能差一个会话时长）。", "",
           "| 阶段 | 并发 | 请求数 | 吞吐 req/s | p50 ms | p95 ms | p99 ms | 错误率 | 429 | 503 BUSY | 预期 | 连接峰值（角色:总/活跃/锁等待/等待） | 闸门排队分档 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in detail:
        peaks = "；".join(f"{role}:{p['max_connections']}/{p['max_active']}/{p['max_lock_wait']}/{p.get('max_waiting', 0)}"
                         for role, p in sorted(d.get("db_peaks", {}).items())) or "—"
        buckets: dict[str, int] = {}
        for g in d.get("gate", {}).values():
            for k, v in g["queue_ms"].items():
                buckets[k] = buckets.get(k, 0) + v
        gate = "、".join(f"{k}×{v}" for k, v in _ordered(buckets, GATE_BUCKETS)) or "—"
        out.append(f"| {d['label']} | {d['concurrency']} | {d['count']} | {_fmt(d['throughput_rps'])} | {_fmt(d['p50_ms'])} | "
                   f"{_fmt(d['p95_ms'])} | {_fmt(d['p99_ms'])} | {_fmt(d['error_rate'])} | {d.get('throttled_429', 0)} | "
                   f"{d.get('subject_busy_503', 0)} | {d.get('expected', 0)} | {peaks} | {gate} |")
    if any(d.get("top_queries") for d in detail):
        out += ["", "各阶段慢查询（pg_stat_statements 阶段边界快照相减，按总耗时前三；语句已规范化）：", "",
                "| 阶段 | 角色 | 调用 | 总 ms | 平均 ms | 语句（截断） |", "|---|---|---|---|---|---|"]
        for d in detail:
            for q in d.get("top_queries", []):
                text = q["query"].replace("|", "\\|")
                out.append(f"| {d['label']} | {q['role']} | {q['calls']} | {q['total_ms']} | {q['mean_ms']} | `{text}` |")
    return out


def render_markdown(r: dict) -> str:
    banner = r.get("banner") or BANNER
    peak = r.get("peak2x")
    title = "2 倍峰值压测报告" if peak else "压测基线报告"
    lines = [f"# {title}（{banner}）", ""]
    if peak:
        lines += [f"> {PEAK_BANNER}：负载生成器与全部服务同在一台机器，阶段并发是合成占位；{peak['note']}。", ""]
    if r.get("scale_note"):
        lines += [f"> {r['scale_note']}", ""]
    p = r["profile"]
    stages = " → ".join((f"{s['label']} " if s.get("label") else "") + f"{s['concurrency']} 并发 × {s['duration_s']:.0f} s"
                        for s in p["stages"])
    lines += [
        "## 运行参数", "",
        f"- 种子：`{p['seed']}`；阶段：{stages}；平均会话时长（推到达率）{p['mean_session_s']} s；步骤间平均思考 {p['think_mean_s']} s",
        "- 旅程配比：" + "、".join(f"{m['journey']} {m['weight']}" for m in p["mix"]),
    ]
    if p.get("journeys"):
        lines.append("- 可选场景（PLT-20）：" + "、".join(p["journeys"]))
    lines += [
        f"- 到达数：{r['arrivals']}（" + "、".join(f"{k} {v}" for k, v in _ordered(r["arrivals_by_journey"], JOURNEYS)) + "）",
        f"- 假供应商延迟：中位 {r['provider']['median_ms']} ms、P90 {r['provider']['p90_ms']} ms；故障注入："
        + (json.dumps(r["faults"], ensure_ascii=False, sort_keys=True) if r["faults"] else "无"),
        f"- 请求序列：计划 `{r['plan_digest'][:16]}…`，实际 `{r['executed_digest'][:16]}…`，"
        + ("一致" if r["plan_digest"] == r["executed_digest"] else "**不一致**（有步骤因前置失败被跳过，见下表）"),
        f"- 墙钟 {r['wall_seconds']} s；会话排队等待 P95 {_fmt(r['queue_wait_p95_ms'])} ms", "",
        "## 各阶段（按到达阶段归属）", "",
        "| 阶段 | 请求数 | 错误率 | p50 ms | p95 ms | p99 ms | 吞吐 req/s |", "|---|---|---|---|---|---|---|",
    ]
    for s in r["stages"]:
        st = p["stages"][s["stage"]]
        name = (f"{st['label']}（{st['concurrency']} 并发）" if st.get("label") else f"{st['concurrency']} 并发")
        lines.append(f"| {name} | {s['count']} | {_fmt(s['error_rate'])} | {_fmt(s['p50_ms'])} | "
                     f"{_fmt(s['p95_ms'])} | {_fmt(s['p99_ms'])} | {_fmt(s['throughput_rps'])} |")
    lines += render_stage_detail(r)
    lines += ["", "## 各接口", "",
              "| 接口 | 请求数 | 错误率 | 业务拒绝 | 429 限流 | 503 SUBJECT_BUSY | 预期 | p50 ms | p95 ms | p99 ms | 吞吐 req/s | 状态 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for step, d in _ordered(r["endpoints"], ENDPOINTS):
        codes = "、".join(f"{c}×{n}" for c, n in sorted(d["codes"].items()))
        lines.append(f"| {ENDPOINTS.get(step, step)} | {d['count']} | {_fmt(d['error_rate'])} | {d['rejected']} | {d.get('throttled_429', 0)} | "
                     f"{d.get('subject_busy_503', 0)} | {d.get('expected', 0)} | {_fmt(d['p50_ms'])} | "
                     f"{_fmt(d['p95_ms'])} | {_fmt(d['p99_ms'])} | {_fmt(d['throughput_rps'])} | {codes} |")
    stage_names = [(s.get("label") or f"{s['concurrency']} 并发") for s in p["stages"]]
    lines += render_extras_check(r.get("extras_check"))
    lines += render_write_path(r.get("write_path"))
    mb = r.get("model_budget")
    if mb and mb.get("limit") is not None:
        lines += ["", f"模型调用上限（--max-model-calls）：{mb['used']} / {mb['limit']}"
                  + ("，**已到上限并停止**（之后的走模型请求与新到达的旅程都没有发出）" if mb["stopped"] else "")]
    if "plan_events" in (p.get("journeys") or []):
        lines += ["", "注：开了 plan_events，学生的今日计划另走跨科编排与快照，行名“today.orch”，不与基线的 today 比较。"]
    lines += render_slo(r.get("slo"), stage_names)
    lines += render_gate(r.get("subject_gate"))
    if r["skipped"]:
        lines += ["", "跳过的步骤（前置失败）：" + "、".join(f"{ENDPOINTS.get(k, k)} ×{v}" for k, v in sorted(r["skipped"].items()))]
    db = r["db"]
    lines += ["", "## 数据库", "", "连接（pg_stat_activity 每 0.5 s 采样的峰值）：", "",
              "| 登录角色 | 最大连接 | 最大活跃 | 最大锁等待 |", "|---|---|---|---|"]
    for role, pk in sorted(db["activity"]["by_role"].items()):
        lines.append(f"| {role} | {pk['max_connections']} | {pk['max_active']} | {pk['max_lock_wait']} |")
    lines += ["", f"应用侧池上限：{db['pool_note']}", "", "慢查询前十（pg_stat_statements，按总耗时；语句已规范化）：", ""]
    if db["top_queries"]:
        lines += ["| # | 角色 | 调用 | 总 ms | 平均 ms | 最大 ms | 语句（截断） |", "|---|---|---|---|---|---|---|"]
        for i, q in enumerate(db["top_queries"], 1):
            text = q["query"].replace("|", "\\|")
            lines.append(f"| {i} | {q['role']} | {q['calls']} | {q['total_ms']} | {q['mean_ms']} | {q['max_ms']} | `{text}` |")
    else:
        lines.append("未采集（pg_stat_statements 不可用）")
    m = r["metering"]
    lines += ["", "## 计量一致性（压测后）", "",
              f"- 快进计量时钟 {m['skew']} 并跑过期任务，回收 {m['expired_by_job']} 笔；",
              f"- 预留 {m['reservations']} 笔：结算 {m['settled']}、释放 {m['released']}、过期 {m['expired']}、仍在预留 {m['still_reserved']}；"
              f"流水不平 {m['unbalanced']}；对账视图 {m['reconciliation_rows']} 行；额度桶未结预留量 {m['bucket_reserved']}",
              f"- 结论：**{'零泄漏' if m['zero_leak'] else '有泄漏'}**", ""]
    mc = r["model_calls"]
    lines += ["## 模型调用（调用记录元数据）", "",
              f"- 共 {mc['total']} 次；按科目与任务：" + "、".join(f"{k} {v}" for k, v in sorted(mc["by_subject_task"].items())),
              "- 结果：" + "、".join(f"{k} {v}" for k, v in sorted(mc["by_outcome"].items())), ""]
    lines += ["## 说明", "",
              "- 分位数为最近秩法；错误 = 非 2xx 且非 402 业务拒绝、非“预期”，或连接失败（429 限流与 503 SUBJECT_BUSY 仍算错误，另单列两列）；",
              "- “预期”= 本栈缺依赖时的预期结果（PLT-20 可选场景：没有洞察服务时月度计划与预测的 503 UNAVAILABLE、"
              "没有可作答题时派生票据的 422 TICKET_INVALID、快照里没有任务时派生任务 ID 的 422 INVALID 等），不算错误，单列"
              + ("（本次写路径已接上：再练 / 复测的派生条目与派生票据不算预期）" if (r.get("write_path") or {}).get("enabled") else "") + "；",
              "- 批改没有 BFF HTTP 入口（壳里尚无批改路由）：经计量驱动桩直接运行 BFF 的 meteredCall（耗时含预留、同键退避重试与结算）；",
              "- 负载生成器、学习服务、BFF、适配器、假供应商与数据库同在一台机器，互相争用 CPU；数字只作回归比较的基线；",
              f"- {banner}。", ""]
    return "\n".join(lines)


def to_json(r: dict) -> str:
    return json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
