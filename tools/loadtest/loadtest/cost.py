"""成本模型与报表（spec 设计要点 3）：调用记录 token × 价格表 × 会员档使用分布 → 科目 × 会员档单月 P50 / P90 + 敏感性分析。

- 输入只有元数据：调用记录（科目、任务、结果、token 数；不含内容与模型标识）、价格表（示例价，明显标注）、
  使用画像（config/cost_profile.yaml，逐项标来源）、额度（迁移 0015 草稿 v12-pilot@1，只读解析）；
- 单次调用成本 = 输入 token × 输入单价 + 输出 token × 输出单价（按百万 token 计价）；token 取调用记录中成功调用的均值，
  没有记录的任务用可配置分布（报告标“假设”）；
- 单月成本(科目, 档, u) = Σ_活动 额度(档, 资源) × u × 每单位调用次数 × 单次调用成本 + 语音分钟 × u × 每分钟单价；
- P50 / P90：使用率三档（默认 30% / 60% / 100%，等权）上按人数加权的最近秩分位；
- 敏感性：价格 ×0.5、×1.5，token ×1.5（语音按分钟计价，不随 token 变）。
报告标注“合成估算”；真实会话数据到位后，同一工具换输入重算（RUNBOOK 第 3 节）。
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "tools/loadtest/config"
MIGRATION_0015 = ROOT / "db/migrations/0015_platform__metering_v2.up.sql"
TIERS = ("basic", "plus", "pro", "max")
TIER_LABELS = {"basic": "基本", "plus": "Plus", "pro": "Pro", "max": "Max"}
SENSITIVITY = (("base", "基准", 1.0, 1.0), ("price_x0.5", "价格 ×0.5", 0.5, 1.0), ("price_x1.5", "价格 ×1.5", 1.5, 1.0),
               ("tokens_x1.5", "token ×1.5", 1.0, 1.5))
SOURCE_REPLAY = "合成回放（假供应商按提示字符数估算 token）"
SOURCE_ASSUMED = "可配置分布（假设）"


# ---------------- 输入 ----------------
def load_quotas(config_version: str, path: Path = MIGRATION_0015) -> dict[str, dict[str, float]]:
    """只读解析迁移里某个额度配置的 plan_allowance 种子：{档: {资源: 数量}}"""
    sql = path.read_text(encoding="utf-8")
    m = re.search(r"INSERT INTO platform\.plan_allowance \(config_version, tier, resource_type, quantity, fair_use\)\s*"
                  rf"SELECT '{re.escape(config_version)}'.*?FROM \(VALUES(.*?)\) AS v\(tier, res, qty, fair\);", sql, re.S)
    if not m:
        raise ValueError(f"迁移里找不到额度配置 {config_version}")
    block = m.group(1)
    tuples = re.findall(r"\([^()]*\)", block)
    parsed = re.findall(r"\('(\w+)',\s*'(\w+)',\s*(\d+),\s*(?:true|false)\)", block)
    # 行数核对（契约审查 L1）：每个元组都要解析成功，不能有静默漏掉或重复的行
    if not parsed or len(parsed) != len(tuples):
        raise ValueError(f"额度配置 {config_version} 行数不符：解析了 {len(parsed)} 行，迁移里有 {len(tuples)} 行")
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for tier, res, qty in parsed:
        if res in out[tier]:
            raise ValueError(f"额度配置 {config_version} 的 {tier}/{res} 重复")
        out[tier][res] = float(qty)
    if set(out) != set(TIERS) or len({frozenset(v) for v in out.values()}) != 1:
        raise ValueError(f"额度配置 {config_version} 的四档资源集合不一致")
    return dict(out)


CODE = re.compile(r"^(供应商|模型)?代号[\w\-]{1,24}$")


def validate(prices: dict, profile: dict, quotas: dict) -> None:
    """输入核对：价格表里的供应商与模型只能是合成代号（安全审查 L4，签字价也一样）；画像引用的资源、任务必须存在（契约审查 L1）"""
    entries = list(prices["per_million_tokens"].values()) + [prices["voice_per_minute"]]
    for e in entries:
        for k in ("vendor", "model"):
            if not isinstance(e.get(k), str) or not CODE.match(e[k]):
                raise ValueError(f"价格表的 {k} 必须是合成代号（形如“供应商代号甲”“模型代号甲-对话”），不写真实供应商或模型标识")
    resources = set(next(iter(quotas.values())))
    for subject, cfg in profile["subjects"].items():
        for a in cfg.get("activities", []):
            if a["resource"] not in resources:
                raise ValueError(f"画像 {subject} 引用了额度配置里不存在的资源 {a['resource']!r}")
            if a["task"] and a["task"] != "voice" and (a["task"] not in prices["per_million_tokens"]
                                                        or a["task"] not in profile["fallback_tokens"]):
                raise ValueError(f"画像 {subject} 的任务 {a['task']} 没有价格或回退 token 分布")


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------- 计算 ----------------
def weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    """按权重的最近秩分位：升序后第一个累计权重占比 ≥ q 的值"""
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc / total >= q - 1e-12:
            return v
    return pairs[-1][0]


@dataclass(frozen=True)
class TokenStat:
    input: float
    output: float
    calls: int
    source: str


def token_stats(records: list[dict], fallback: dict) -> dict[tuple[str, str], TokenStat]:
    """（科目, 任务）→ 每次成功调用的平均 token；记录里没有的任务在 lookup 时回退到 fallback"""
    acc: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for r in records:
        if r.get("outcome") == "ok" and r.get("subject") and r.get("task"):
            acc[(r["subject"], r["task"])].append((int(r["input_tokens"]), int(r["output_tokens"])))
    return {k: TokenStat(sum(i for i, _ in v) / len(v), sum(o for _, o in v) / len(v), len(v), SOURCE_REPLAY) for k, v in acc.items()}


def lookup_tokens(stats: dict, fallback: dict, subject: str, task: str) -> TokenStat:
    if (subject, task) in stats:
        return stats[(subject, task)]
    f = fallback[task]
    return TokenStat(float(f["input"]), float(f["output"]), 0, SOURCE_ASSUMED)


def per_call_cost(tokens: TokenStat, price: dict, price_mul: float = 1.0, token_mul: float = 1.0) -> float:
    return (tokens.input * token_mul * price["input"] + tokens.output * token_mul * price["output"]) / 1e6 * price_mul


def monthly_cost(subject_cfg: dict, subject: str, tier: str, u: float, quotas: dict, stats: dict, fallback: dict, prices: dict,
                 price_mul: float = 1.0, token_mul: float = 1.0) -> float:
    total = 0.0
    for act in subject_cfg["activities"]:
        units = quotas[tier].get(act["resource"], 0.0) * u
        if not act["task"] or act["calls_per_unit"] == 0:
            continue
        if act["task"] == "voice":
            total += units * act["calls_per_unit"] * prices["voice_per_minute"]["price"] * price_mul
            continue
        tok = lookup_tokens(stats, fallback, subject, act["task"])
        total += units * act["calls_per_unit"] * per_call_cost(tok, prices["per_million_tokens"][act["task"]], price_mul, token_mul)
    return total


def build(records: list[dict], prices: dict, profile: dict, quotas: dict) -> dict:
    validate(prices, profile, quotas)
    util = profile["utilization"]
    levels, weights = [float(x) for x in util["levels"]], [float(x) for x in util["weights"]]
    fallback = profile["fallback_tokens"]
    stats = token_stats(records, fallback)
    table, sens, sources = [], [], []
    for subject, cfg in profile["subjects"].items():
        if cfg.get("shelved"):
            table.append({"subject": subject, "label": cfg["label"], "status": "搁置，无数据"})
            continue
        for act in cfg["activities"]:
            if act["task"] and act["task"] != "voice" and act["calls_per_unit"]:
                tok = lookup_tokens(stats, fallback, subject, act["task"])
                sources.append({"subject": subject, "task": act["task"], "input_tokens": round(tok.input, 1),
                                "output_tokens": round(tok.output, 1), "calls_observed": tok.calls, "source": tok.source,
                                "per_call_cost": round(per_call_cost(tok, prices["per_million_tokens"][act["task"]]), 6),
                                "price_ref": {k: prices["per_million_tokens"][act["task"]][k] for k in ("vendor", "model")}})
        for tier in TIERS:
            row = {"subject": subject, "label": cfg["label"], "tier": tier, "status": "ok"}
            for key, _, pm, tm in SENSITIVITY:
                costs = [monthly_cost(cfg, subject, tier, u, quotas, stats, fallback, prices, pm, tm) for u in levels]
                entry = {"p50": weighted_quantile(costs, weights, 0.5), "p90": weighted_quantile(costs, weights, 0.9)}
                if key == "base":
                    row.update(entry)
                    row["by_level"] = {f"{u:g}": c for u, c in zip(levels, costs)}
                else:
                    sens.append({"subject": subject, "tier": tier, "scenario": key, **entry})
            table.append(row)
    unique_sources = {(s["subject"], s["task"]): s for s in sources}
    return {
        "banner": "合成估算：示例价、非真实会话数据",
        "example_prices": bool(prices.get("example", True)), "currency": prices.get("currency", "CNY"),
        "quota_config": profile["quota_config"], "utilization": {"levels": levels, "weights": weights},
        "records": {"total": len(records), "ok": sum(1 for r in records if r.get("outcome") == "ok")},
        "table": table, "sensitivity": sens, "token_sources": list(unique_sources.values()),
        "assumptions": [{"subject": s, "resource": a["resource"], "task": a["task"], "calls_per_unit": a["calls_per_unit"],
                         "note": a.get("note", "")} for s, c in profile["subjects"].items() for a in c["activities"]],
        "voice_price_ref": {k: prices["voice_per_minute"][k] for k in ("vendor", "model", "price")},
        "prices_ref": {t: {k: p[k] for k in ("vendor", "model", "input", "output")} for t, p in prices["per_million_tokens"].items()},
    }


# ---------------- 报表 ----------------
def _m(v: float) -> str:
    return f"{v:,.2f}"


def render_markdown(r: dict) -> str:
    cur = r["currency"]
    lines = [f"# 单位经济首版：科目 × 会员档单月成本（{r['banner']}）", ""]
    if r["example_prices"]:
        lines += ["> **示例价**：价格表是占位示例，不是任何真实供应商的价格；供应商与模型为合成代号。签字后由人填入价格并重算。", ""]
    lines += [f"> 数据来源：调用记录 {r['records']['total']} 条（成功 {r['records']['ok']} 条，来自本机合成回放，token 为假供应商按提示字符数估算）"
              "；其余为可配置分布与使用画像假设（逐项见下）。额度取草稿配置 "
              f"`{r['quota_config']}`（迁移 0015，未发布）。", "",
              "使用分布：额度上限的 " + " / ".join(f"{u:.0%}" for u in r["utilization"]["levels"])
              + " 三档，人数权重 " + " / ".join(f"{w:g}" for w in r["utilization"]["weights"])
              + "；P50 / P90 为按人数加权的最近秩分位。单位：" + cur + " / 学生 / 月。", "",
              "## 成本表（基准）", "",
              "| 科目 | 会员档 | P50 | P90 | " + " | ".join(f"{u:.0%} 档" for u in r["utilization"]["levels"]) + " |",
              "|---|---|---|---|" + "---|" * len(r["utilization"]["levels"])]
    for row in r["table"]:
        if row["status"] != "ok":
            lines.append(f"| {row['label']} | 全部 | {row['status']} | {row['status']} |" + " — |" * len(r["utilization"]["levels"]))
            continue
        lines.append(f"| {row['label']} | {TIER_LABELS[row['tier']]} | {_m(row['p50'])} | {_m(row['p90'])} | "
                     + " | ".join(_m(v) for v in row["by_level"].values()) + " |")
    lines += ["", "## 敏感性分析（P50 / P90）", "", "| 科目 | 会员档 | " + " | ".join(lbl for k, lbl, _, _ in SENSITIVITY) + " |",
              "|---|---|" + "---|" * len(SENSITIVITY)]
    idx = {(s["subject"], s["tier"], s["scenario"]): s for s in r["sensitivity"]}
    for row in r["table"]:
        if row["status"] != "ok":
            continue
        cells = [f"{_m(row['p50'])} / {_m(row['p90'])}"]
        for k, _, _, _ in SENSITIVITY[1:]:
            s = idx[(row["subject"], row["tier"], k)]
            cells.append(f"{_m(s['p50'])} / {_m(s['p90'])}")
        lines.append(f"| {row['label']} | {TIER_LABELS[row['tier']]} | " + " | ".join(cells) + " |")
    lines += ["", "## 单次调用成本与 token 来源", "", "| 科目 | 任务 | 平均输入 token | 平均输出 token | 观测调用数 | 单次成本 | 价格代号 | 来源 |",
              "|---|---|---|---|---|---|---|---|"]
    for s in r["token_sources"]:
        lines.append(f"| {s['subject']} | {s['task']} | {s['input_tokens']} | {s['output_tokens']} | {s['calls_observed']} | "
                     f"{s['per_call_cost']:.6f} | {s['price_ref']['vendor']} / {s['price_ref']['model']} | {s['source']} |")
    vp = r["voice_price_ref"]
    lines += ["", "## 价格表（" + ("示例价" if r["example_prices"] else "签字价") + "）", "",
              "| 任务 | 供应商代号 | 模型代号 | 输入 / 百万 token | 输出 / 百万 token |", "|---|---|---|---|---|"]
    for t, p in r["prices_ref"].items():
        lines.append(f"| {t} | {p['vendor']} | {p['model']} | {p['input']} | {p['output']} |")
    lines += [f"| 语音（每分钟） | {vp['vendor']} | {vp['model']} | {vp['price']} / 分钟 | — |", "",
              "## 使用画像假设", "", "| 科目 | 资源 | 任务 | 每单位调用 | 说明 |", "|---|---|---|---|---|"]
    for a in r["assumptions"]:
        lines.append(f"| {a['subject']} | {a['resource']} | {a['task'] or '—'} | {a['calls_per_unit']} | {a['note']} |")
    lines += ["", "## 说明", "",
              "- 本表是**合成估算**：没有真实会话、没有签字供应商与价格；只用于检验成本模型与报表口径；",
              "- 未计入：存储与计算摊销、模考 / 讲评课 / 深度报告、上传识别（T3）；T1 / T9 目前没有学科方法调用（讲解未实现），数量全是假设；",
              "- 未计入：失败尝试（超时、5xx、限流后重试或切换备选）可能被供应商计费的 token——调用记录只有最终成功那次的 token 数，"
              "失败尝试没有计数；真实重算时以供应商账单核对这部分；",
              "- 真实成本重算：按 RUNBOOK.md 第 3 节导出调用记录（只含 token 数与元数据）与签字价格，用同一工具重算。", ""]
    return "\n".join(lines)


def run(records_path: Path, prices_path: Path, profile_path: Path, out: Path | None) -> dict:
    profile = load_yaml(profile_path)
    r = build(load_records(records_path), load_yaml(prices_path), profile, load_quotas(profile["quota_config"]))
    from . import outdir

    out = outdir.prepare(out, "starbuddy-cost-report")
    outdir.write_text(out / "cost-report.json", json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    outdir.write_text(out / "cost-report.md", render_markdown(r))
    return {**r, "out": str(out)}


def is_close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
