"""压测报告（Markdown + JSON，只含汇总）：标注“本机基线，不代表生产容量”；不含请求或响应内容、学生 ID、模型标识。"""
from __future__ import annotations

import json

from .scenarios import ENDPOINTS

BANNER = "本机基线，不代表生产容量"


def _fmt(v) -> str:
    return "—" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))


def render_markdown(r: dict) -> str:
    lines = [f"# 压测基线报告（{BANNER}）", ""]
    if r.get("scale_note"):
        lines += [f"> {r['scale_note']}", ""]
    p = r["profile"]
    stages = " → ".join(f"{s['concurrency']} 并发 × {s['duration_s']:.0f} s" for s in p["stages"])
    lines += [
        "## 运行参数", "",
        f"- 种子：`{p['seed']}`；阶段：{stages}；平均会话时长（推到达率）{p['mean_session_s']} s；步骤间平均思考 {p['think_mean_s']} s",
        "- 旅程配比：" + "、".join(f"{m['journey']} {m['weight']}" for m in p["mix"]),
        f"- 到达数：{r['arrivals']}（" + "、".join(f"{k} {v}" for k, v in r["arrivals_by_journey"].items()) + "）",
        f"- 假供应商延迟：中位 {r['provider']['median_ms']} ms、P90 {r['provider']['p90_ms']} ms；故障注入："
        + (json.dumps(r["faults"], ensure_ascii=False) if r["faults"] else "无"),
        f"- 请求序列：计划 `{r['plan_digest'][:16]}…`，实际 `{r['executed_digest'][:16]}…`，"
        + ("一致" if r["plan_digest"] == r["executed_digest"] else "**不一致**（有步骤因前置失败被跳过，见下表）"),
        f"- 墙钟 {r['wall_seconds']} s；会话排队等待 P95 {_fmt(r['queue_wait_p95_ms'])} ms", "",
        "## 各阶段（按到达阶段归属）", "",
        "| 阶段 | 请求数 | 错误率 | p50 ms | p95 ms | p99 ms | 吞吐 req/s |", "|---|---|---|---|---|---|---|",
    ]
    for s in r["stages"]:
        lines.append(f"| {p['stages'][s['stage']]['concurrency']} 并发 | {s['count']} | {_fmt(s['error_rate'])} | {_fmt(s['p50_ms'])} | "
                     f"{_fmt(s['p95_ms'])} | {_fmt(s['p99_ms'])} | {_fmt(s['throughput_rps'])} |")
    lines += ["", "## 各接口", "",
              "| 接口 | 请求数 | 错误率 | 业务拒绝 | p50 ms | p95 ms | p99 ms | 吞吐 req/s | 状态 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for step, d in r["endpoints"].items():
        codes = "、".join(f"{c}×{n}" for c, n in d["codes"].items())
        lines.append(f"| {ENDPOINTS.get(step, step)} | {d['count']} | {_fmt(d['error_rate'])} | {d['rejected']} | {_fmt(d['p50_ms'])} | "
                     f"{_fmt(d['p95_ms'])} | {_fmt(d['p99_ms'])} | {_fmt(d['throughput_rps'])} | {codes} |")
    if r["skipped"]:
        lines += ["", "跳过的步骤（前置失败）：" + "、".join(f"{ENDPOINTS.get(k, k)} ×{v}" for k, v in r["skipped"].items())]
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
              f"- 共 {mc['total']} 次；按科目与任务：" + "、".join(f"{k} {v}" for k, v in mc["by_subject_task"].items()),
              "- 结果：" + "、".join(f"{k} {v}" for k, v in mc["by_outcome"].items()), ""]
    lines += ["## 说明", "",
              "- 分位数为最近秩法；错误 = 非 2xx 且非 402 业务拒绝，或连接失败；",
              "- 批改没有 BFF HTTP 入口（壳里尚无批改路由）：经计量驱动桩直接运行 BFF 的 meteredCall（耗时含预留、同键退避重试与结算）；",
              "- 负载生成器、学习服务、BFF、适配器、假供应商与数据库同在一台机器，互相争用 CPU；数字只作回归比较的基线；",
              f"- {BANNER}。", ""]
    return "\n".join(lines)


def to_json(r: dict) -> str:
    return json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
