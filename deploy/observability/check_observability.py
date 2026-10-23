#!/usr/bin/env python3
"""告警规则与仪表盘的离线校验（LT-05 范围第 3、4、7 条，specs/launch-lt05-observability）。随 make deploy-check 运行（需要 PyYAML：
用学习服务的 venv）；不联网、不连任何监控产品。

规则：
  O01 登记清单 metrics-inventory.yaml：version 1；labels 恰好是标签白名单；每个服务的指标名都以 starbuddy_ 开头、不重复；
      histograms 里的名字都登记在某个服务下；external 只放采集侧提供的名字（不以 starbuddy_ 开头）
  O02 alerts.yaml 是 Prometheus 规则格式：groups[].name 唯一，rules[] 每条有 alert（唯一、Starbuddy 开头）、expr、for（时长）、
      labels.severity（critical / warning）、annotations.summary / description / threshold（threshold 必须写明“待人签字”）
  O03 规则与仪表盘的表达式只引用登记的指标：starbuddy_* 必须在某个服务的清单里（直方图只能用 _bucket / _sum / _count），
      其余名字必须在 external 里；不收 {__name__=…} 这类绕开名字的写法
  O04 标签：starbuddy_* 指标的匹配器只用标签白名单（直方图另收 le）；external 指标与 by / without / on 只另收采集侧标签
      （namespace、pod、container、persistentvolumeclaim、job、instance）
  O05 必备告警：服务不可用、就绪失败、5xx 率、p95 时延、模型熔断、护栏将满、计量回滚升高、计量 409 升高、复核积压、调度失败、证书、磁盘
  O06 仪表盘（dashboards/*.json）：合法 JSON，有 title、uid（唯一）、panels；每个 target 有 expr；必备四个：总览、模型与成本、计量、学习链路
用法：python3 check_observability.py [目录] [--promtool | --require-promtool]
  --promtool：promtool 在就另跑 promtool check rules（缺失打印“跳过”）；--require-promtool：缺失时退出码 3（CI 有工具时用）
退出码：0 通过；1 有违规；2 读不了或解析失败；3 要求 promtool 但没有。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

LABELS = {"service", "route", "method", "status_class", "subject", "task", "provider", "outcome"}
EXTERNAL_LABELS = {"namespace", "pod", "container", "persistentvolumeclaim", "job", "instance"}
SEVERITIES = {"critical", "warning"}
DURATION = re.compile(r"^\d+[smhd]$")
FUNCTIONS = {"sum", "min", "max", "avg", "count", "topk", "bottomk", "quantile", "stddev", "group", "rate", "irate", "increase", "delta",
             "deriv", "histogram_quantile", "clamp_min", "clamp_max", "time", "abs", "absent", "absent_over_time", "vector", "scalar",
             "sort", "sort_desc", "avg_over_time", "max_over_time", "min_over_time", "sum_over_time", "predict_linear", "label_replace",
             "changes", "resets", "ceil", "floor", "round"}
KEYWORDS = {"by", "without", "on", "ignoring", "group_left", "group_right", "and", "or", "unless", "bool", "offset", "inf", "nan"}
HIST_SUFFIX = ("_bucket", "_sum", "_count")
REQUIRED_ALERTS = {
    "StarbuddyServiceDown": "服务不可用", "StarbuddyNotReady": "就绪失败", "StarbuddyHigh5xxRate": "5xx 率", "StarbuddyHighLatencyP95": "p95 时延",
    "StarbuddyModelBreakerOpen": "模型熔断", "StarbuddyModelBudgetNearlyExhausted": "护栏将满", "StarbuddyMeterRollbackSpike": "计量回滚",
    "StarbuddyMeterConflictSpike": "计量 409", "StarbuddyReviewQueueBacklog": "复核积压", "StarbuddySchedulerJobFailed": "调度失败",
    "StarbuddyCertExpiringSoon": "证书", "StarbuddyDiskLow": "磁盘"}
REQUIRED_DASHBOARDS = {"starbuddy-overview": "总览", "starbuddy-model-cost": "模型与成本", "starbuddy-metering": "计量",
                       "starbuddy-learning-pipeline": "学习链路"}


class Inventory:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc
        self.services: dict[str, list[str]] = doc.get("services") or {}
        self.names = {n for v in self.services.values() for n in (v or [])}
        self.histograms = set(doc.get("histograms") or [])
        self.external = set((doc.get("external") or {}).keys())


def strip(expr: str) -> str:
    s = re.sub(r'"(?:[^"\\]|\\.)*"', '""', expr)
    s = re.sub(r"'(?:[^'\\]|\\.)*'", "''", s)
    return re.sub(r"\[[^\]]*\]", " ", s)


def references(expr: str) -> tuple[list[tuple[str, list[str]]], set[str], list[str]]:
    """（[(指标名, 匹配器里的标签名)], 分组子句里的标签名, 问题）"""
    s = strip(expr)
    grouping: set[str] = set()
    problems: list[str] = []
    for m in re.finditer(r"\b(by|without|on|ignoring|group_left|group_right)\s*\(([^)]*)\)", s):
        grouping |= {x.strip() for x in m.group(2).split(",") if x.strip()}
    s = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", s)
    out: list[tuple[str, list[str]]] = []
    for m in re.finditer(r"(?<![\w.:])([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(\{([^}]*)\})?", s):
        name, rest = m.group(1), s[m.end():].lstrip()
        if name in KEYWORDS or (rest.startswith("(") and m.group(2) is None):
            continue
        labels = re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=~|!~|!=|=)", m.group(3) or "")
        out.append((name, labels))
    if re.search(r"(?<![\w}])\{", s) or "__name__" in s:
        problems.append("不收没有指标名的选择器或 __name__ 匹配")
    return out, grouping, problems


def check_expr(where: str, expr: Any, inv: Inventory) -> list[str]:
    if not isinstance(expr, str) or not expr.strip():
        return [f"O03 {where}：缺表达式"]
    refs, grouping, problems = references(expr)
    found = [f"O03 {where}：{p}" for p in problems]
    if not refs:
        found.append(f"O03 {where}：表达式里没有指标")
    for name, labels in refs:
        if name.startswith("starbuddy_"):
            base = next((name[: -len(s)] for s in HIST_SUFFIX if name.endswith(s) and name[: -len(s)] in inv.histograms), None)
            if base is None and (name not in inv.names or name in inv.histograms):
                found.append(f"O03 {where}：{name} 不是登记的指标" + ("（直方图只能用 _bucket / _sum / _count）" if name in inv.histograms else ""))
            allowed = LABELS | ({"le"} if base is not None else set())
        else:
            if name not in inv.external:
                found.append(f"O03 {where}：{name} 不在 external 登记里")
            allowed = EXTERNAL_LABELS
        bad = sorted(set(labels) - allowed)
        if bad:
            found.append(f"O04 {where}：{name} 用了白名单外的标签 {bad}")
    bad = sorted(grouping - (LABELS | EXTERNAL_LABELS | {"le"}))
    if bad:
        found.append(f"O04 {where}：分组用了白名单外的标签 {bad}")
    return found


def check_inventory(inv: Inventory) -> list[str]:
    found = []
    d = inv.doc
    if d.get("version") != 1:
        found.append("O01 清单：version 必须是 1")
    if set(d.get("labels") or []) != LABELS or len(d.get("labels") or []) != len(LABELS):
        found.append("O01 清单：labels 必须恰好是标签白名单")
    if not inv.services:
        found.append("O01 清单：没有 services")
    for svc, names in inv.services.items():
        names = names or []
        if len(set(names)) != len(names):
            found.append(f"O01 清单：{svc} 有重复的指标名")
        for n in names:
            if not re.fullmatch(r"starbuddy_[a-z][a-z0-9_]{2,80}", str(n)):
                found.append(f"O01 清单：{svc} 的 {n} 必须以 starbuddy_ 开头")
    for h in inv.histograms - inv.names:
        found.append(f"O01 清单：直方图 {h} 没有登记在任何服务下")
    for e in inv.external:
        if e.startswith("starbuddy_"):
            found.append(f"O01 清单：external 不能放本仓库的指标（{e}）")
    return found


def check_alerts(doc: Any, inv: Inventory) -> list[str]:
    found = []
    if not isinstance(doc, dict) or not isinstance(doc.get("groups"), list) or set(doc) != {"groups"}:
        return ["O02 alerts.yaml：顶层只能是 groups 列表"]
    groups, alerts = set(), set()
    for g in doc["groups"]:
        name = g.get("name") if isinstance(g, dict) else None
        if not name or name in groups:
            found.append(f"O02 规则组名缺失或重复：{name}")
        groups.add(name)
        for r in (g.get("rules") or []) if isinstance(g, dict) else []:
            a = r.get("alert")
            where = f"告警 {a}"
            if not isinstance(a, str) or not re.fullmatch(r"Starbuddy[A-Z][A-Za-z0-9]+", a) or a in alerts:
                found.append(f"O02 {where}：alert 名缺失、不是 Starbuddy 开头或重复")
            alerts.add(a)
            if "record" in r:
                found.append(f"O02 {where}：本文件只放告警规则")
            if not DURATION.fullmatch(str(r.get("for", ""))):
                found.append(f"O02 {where}：for 必须是时长（如 5m）")
            if (r.get("labels") or {}).get("severity") not in SEVERITIES:
                found.append(f"O02 {where}：labels.severity 只能是 {sorted(SEVERITIES)}")
            ann = r.get("annotations") or {}
            for k in ("summary", "description", "threshold"):
                if not str(ann.get(k, "")).strip():
                    found.append(f"O02 {where}：缺 annotations.{k}")
            if "待人签字" not in str(ann.get("threshold", "")):
                found.append(f"O02 {where}：annotations.threshold 必须写明阈值是占位、待人签字")
            found += check_expr(where, r.get("expr"), inv)
    for a, what in REQUIRED_ALERTS.items():
        if a not in alerts:
            found.append(f"O05 缺必备告警 {a}（{what}）")
    return found


def check_dashboards(root: Path, inv: Inventory) -> list[str]:
    found, uids = [], set()
    files = sorted((root / "dashboards").glob("*.json"))
    if not files:
        return ["O06 没有仪表盘"]
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            found.append(f"O06 {p.name}：不是合法 JSON（{e}）")
            continue
        uid = d.get("uid")
        if not d.get("title") or not uid or uid in uids:
            found.append(f"O06 {p.name}：缺 title / uid 或 uid 重复")
        uids.add(uid)
        panels = d.get("panels") or []
        if not panels:
            found.append(f"O06 {p.name}：没有面板")
        for panel in panels:
            targets = panel.get("targets") or []
            if not targets:
                found.append(f"O06 {p.name} 面板 {panel.get('title')}：没有查询")
            for t in targets:
                found += check_expr(f"{p.name} 面板 {panel.get('title')}", t.get("expr"), inv)
    for uid, what in REQUIRED_DASHBOARDS.items():
        if uid not in uids:
            found.append(f"O06 缺必备仪表盘 {uid}（{what}）")
    return found


def check(root: Path) -> list[str]:
    inv = Inventory(yaml.safe_load((root / "metrics-inventory.yaml").read_text(encoding="utf-8")) or {})
    alerts = yaml.safe_load((root / "alerts.yaml").read_text(encoding="utf-8"))
    return check_inventory(inv) + check_alerts(alerts, inv) + check_dashboards(root, inv)


def promtool(root: Path, required: bool) -> int:
    tool = shutil.which("promtool")
    if tool is None:
        print("observability-check：跳过 promtool（本机没有安装；结构与指标引用已由本脚本校验）")
        return 3 if required else 0
    r = subprocess.run([tool, "check", "rules", str(root / "alerts.yaml")], capture_output=True, text=True, check=False)
    print(r.stdout.strip() or r.stderr.strip())
    return 0 if r.returncode == 0 else 1


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    root = Path(args[0]) if args else Path(__file__).resolve().parent
    try:
        found = check(root)
    except (OSError, yaml.YAMLError) as e:
        print(f"observability-check：读不了或解析失败：{e}")
        return 2
    for f in found:
        print(f"  {f}")
    if found:
        print(f"observability-check：失败（{len(found)} 条）")
        return 1
    print(f"observability-check：通过（规则 O01–O06；{len(list((root / 'dashboards').glob('*.json')))} 个仪表盘）")
    if "--promtool" in argv or "--require-promtool" in argv:
        return promtool(root, "--require-promtool" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
