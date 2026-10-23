"""LT-05 可观测性的部署侧（specs/launch-lt05-observability 范围第 3、4、5、7 条）：
- 告警规则与仪表盘的离线校验（deploy/observability/check_observability.py，O01–O06）：仓库通过；每条规则逐条改坏（临时副本里改一处），
  断言能发现、且是对应的规则号（先跑对照组）；promtool 缺失时 --require-promtool 退出码 3；接进 deploy-check；
- k8s：六个后端 Pod 带 starbuddy/metrics 标签、业务端口 http 与单独的指标端口 metrics；PodMonitor 只采这些 Pod 的 metrics 端口
  /metrics 并只保留 starbuddy_*；NetworkPolicy metrics-from-collector 只放行采集组件（一个命名空间 + Pod 选择器，均为由人填写的占位）、
  只到各服务的指标端口（不放行业务端口）；SLS 采集配置只采本命名空间六个后端主容器（Pod 标签 + 容器名白名单）、进 IaC 预建的 app
  日志库（不写 lifeCycle）、JSON 展开、解析失败的行丢弃；清单里没有真实域名。全部静态，不连任何云。"""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import re
import shutil
import sys

import pytest
import yaml

from conftest import DEPLOY, ROOT

OBS = DEPLOY / "observability"
_spec = importlib.util.spec_from_file_location("check_observability", OBS / "check_observability.py")
co = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["check_observability"] = co
_spec.loader.exec_module(co)
# 业务端口（端口名 http）与单独的指标端口（端口名 metrics，决定记录（续）第 5 条）；各服务的指标端口变量与缺省值见 env-inventory
BUSINESS_PORTS = {"learning": 8788, "knowledge": 8790, "insight": 8791, "review-console": 8792, "bff": 8787, "scheduler": 8793}
METRICS_PORTS = {"learning": 9788, "knowledge": 9790, "insight": 9791, "review-console": 9792, "bff": 9787, "scheduler": 9793}
METRICS_PORT_VARS = {"learning": "LEARNING_METRICS_PORT", "knowledge": "KNOWLEDGE_METRICS_PORT", "insight": "INSIGHT_METRICS_PORT",
                     "review-console": "REVIEW_CONSOLE_METRICS_PORT", "bff": "BFF_METRICS_PORT", "scheduler": "SCHEDULER_METRICS_PORT"}


def test_repo_rules_and_dashboards_pass():
    assert co.check(OBS) == []
    assert co.main(["x", str(OBS)]) == 0


def test_control_copy_passes(tmp_path):
    dst = tmp_path / "observability"
    shutil.copytree(OBS, dst)
    assert co.check(dst) == []


# (名字, 文件, 原文, 替换, 期望规则号)；原文必须恰好出现一次
MUTATIONS = [
    ("告警引用没登记的指标", "alerts.yaml", "expr: starbuddy_ready == 0", "expr: starbuddy_readiness == 0", "O03"),
    ("告警直接用直方图基名", "alerts.yaml", "rate(starbuddy_http_request_duration_seconds_bucket[5m])))", "rate(starbuddy_http_request_duration_seconds[5m])))", "O03"),
    ("告警引用没登记的采集侧指标", "alerts.yaml", "expr: probe_ssl_earliest_cert_expiry - time()", "expr: node_ssl_cert_expiry - time()", "O03"),
    ("告警用 __name__ 绕开名字", "alerts.yaml", "expr: starbuddy_db_pool_waiting > 0", "expr: '{__name__=~\"starbuddy_.*\"} > 0'", "O03"),
    ("告警匹配学生标识标签", "alerts.yaml", "expr: starbuddy_ready == 0", "expr: starbuddy_ready{student_id=\"x\"} == 0", "O04"),
    ("告警按账号分组", "alerts.yaml", "max by (provider, task) (starbuddy_model_breaker_open)", "max by (provider, account_id) (starbuddy_model_breaker_open)", "O04"),
    ("采集侧指标用白名单外标签", "alerts.yaml", "kube_pod_container_status_ready{namespace=\"starbuddy-staging\"}",
     "kube_pod_container_status_ready{namespace=\"starbuddy-staging\", student=\"x\"}", "O04"),
    ("阈值没写待人签字", "alerts.yaml", "threshold: 占位缺省（for 2m），待人签字", "threshold: 2 分钟", "O02"),
    ("severity 不在枚举里", "alerts.yaml", "        for: 2m\n        labels: {severity: critical}", "        for: 2m\n        labels: {severity: page}", "O02"),
    ("for 不是时长", "alerts.yaml", "        for: 2m\n", "        for: two\n", "O02"),
    ("告警名重复", "alerts.yaml", "- alert: StarbuddyPodNotReady", "- alert: StarbuddyNotReady", "O02"),
    ("缺必备告警（磁盘）", "alerts.yaml", "- alert: StarbuddyDiskLow", "- alert: StarbuddyVolumeLow", "O05"),
    ("缺必备告警（计量 409）", "alerts.yaml", "- alert: StarbuddyMeterConflictSpike", "- alert: StarbuddyMeterOther", "O05"),
    ("仪表盘引用没登记的指标", "dashboards/metering.json", "starbuddy_meter_settle_total", "starbuddy_meter_settled_total", "O03"),
    ("仪表盘按学生分组", "dashboards/overview.json", "sum by (service, status_class) (rate(starbuddy_http_requests_total[5m]))",
     "sum by (service, student_id) (rate(starbuddy_http_requests_total[5m]))", "O04"),
    ("仪表盘面板没有查询", "dashboards/model-cost.json", "\"targets\": [{\"refId\": \"A\", \"expr\": \"min by (task) (starbuddy_model_budget_remaining_ratio)\", \"legendFormat\": \"{{task}}\"}]",
     "\"targets\": []", "O06"),
    ("缺必备仪表盘", "dashboards/learning-pipeline.json", "\"uid\": \"starbuddy-learning-pipeline\"", "\"uid\": \"starbuddy-other\"", "O06"),
    ("清单标签多了学生标识", "metrics-inventory.yaml", "labels: [service, route, method, status_class, subject, task, provider, outcome]",
     "labels: [service, route, method, status_class, subject, task, provider, outcome, student_id]", "O01"),
    ("清单登记了非 starbuddy_ 指标", "metrics-inventory.yaml", "    - starbuddy_meter_conflict_total\n", "    - meter_conflict_total\n", "O01"),
    ("清单把本仓库指标放进 external", "metrics-inventory.yaml", "external:\n", "external:\n  starbuddy_fake_total: x\n", "O01"),
]


def _mutate(tmp, file: str, old: str, new: str):
    dst = tmp / "observability"
    shutil.copytree(OBS, dst)
    p = dst / file
    text = p.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"变异原文不唯一或找不到：{file} {old[:50]!r}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    return dst


@pytest.mark.parametrize("name,file,old,new,rule", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_rule_catches_its_mutation(tmp_path, name, file, old, new, rule):
    found = co.check(_mutate(tmp_path, file, old, new))
    assert any(f.startswith(rule + " ") for f in found), f"{name}：没有被 {rule} 发现（{found}）"


def test_references_parser():
    refs, grouping, problems = co.references(
        'sum by (service, le) (rate(starbuddy_http_request_duration_seconds_bucket{route!="unmatched"}[5m])) / 1e-9 and on (service) up')
    assert refs == [("starbuddy_http_request_duration_seconds_bucket", ["route"]), ("up", [])]
    assert grouping == {"service", "le"} and problems == []


def test_promtool_required_but_missing(monkeypatch, capsys):
    monkeypatch.setattr(co.shutil, "which", lambda _n: None)
    assert co.main(["x", str(OBS), "--require-promtool"]) == 3
    assert co.main(["x", str(OBS), "--promtool"]) == 0
    assert "跳过 promtool" in capsys.readouterr().out


def test_wired_into_deploy_check_and_make():
    sh = (DEPLOY / "scripts" / "deploy-check.sh").read_text(encoding="utf-8")
    assert "deploy/observability/check_observability.py deploy/observability --promtool" in sh
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "obs-promtool:" in make and "--require-promtool" in make
    check_line = next(ln for ln in make.splitlines() if ln.startswith("check: "))
    assert "obs-promtool" not in check_line and "deploy-check" in check_line


def test_alert_thresholds_are_placeholders_and_no_contacts():
    text = (OBS / "alerts.yaml").read_text(encoding="utf-8")
    assert "待人签字" in text and "由人在 ARMS" in text
    assert "拨测须从 VPC 内网发起" in text, "证书拨测只从内网发起（安全审查 C）"
    assert "stale_estimate > 0 的轮次结果是 ok、不在这里" in text, "调度失败告警的口径写清：按结果编码，不含 stale_estimate"
    assert not re.search(r"@|1[3-9]\d{9}|https?://|webhook", text, re.I), "告警规则里不写联系方式与回调地址"


# ---------------------------------------------------------------- k8s：采集目标、NetworkPolicy、SLS 采集
def _obs_docs():
    return [d for d in yaml.safe_load_all((DEPLOY / "k8s" / "observability.yaml").read_text(encoding="utf-8")) if d]


def test_metrics_pods_labels_and_ports(k8s_docs):
    deps = {d["metadata"]["name"]: d for d in k8s_docs if d["kind"] == "Deployment"}
    labeled = {n for n, d in deps.items() if d["spec"]["template"]["metadata"]["labels"].get("starbuddy/metrics") == "true"}
    assert labeled == set(METRICS_PORTS), "web 与适配器不暴露 /metrics"
    for name, port in METRICS_PORTS.items():
        c = deps[name]["spec"]["template"]["spec"]["containers"][0]
        assert c["ports"] == [{"name": "http", "containerPort": BUSINESS_PORTS[name]}, {"name": "metrics", "containerPort": port}], name
        env = {e["name"]: e.get("value") for e in c["env"]}
        assert env[METRICS_PORT_VARS[name]] == str(port), f"{name}：指标端口变量与容器端口一致"
        for probe in ("readinessProbe", "livenessProbe"):
            assert c[probe]["httpGet"]["port"] == BUSINESS_PORTS[name], f"{name}：探针仍走业务端口（指标端口只服务 /metrics）"


def test_metrics_port_defaults_match_code():
    """各服务代码里的指标端口缺省值与 k8s、env-inventory 一致，且六个互不相同、不与业务端口相同"""
    assert len(set(METRICS_PORTS.values())) == 6 and not set(METRICS_PORTS.values()) & set(BUSINESS_PORTS.values())
    src = {
        "learning": ("services/learning/learning/config.py", r"DEFAULT_METRICS_PORT = (\d+)"),
        "knowledge": ("services/knowledge/knowledge/service/config.py", r"DEFAULT_METRICS_PORT = (\d+)"),
        "insight": ("services/insight/insight/service/config.py", r"DEFAULT_METRICS_PORT = (\d+)"),
        "review-console": ("services/learning/review_console/__main__.py", r"DEFAULT_METRICS_PORT = (\d+)"),
        "bff": ("services/bff/src/observability/metrics.ts", r"DEFAULT_METRICS_PORT = (\d+)"),
        "scheduler": ("services/scheduler/scheduler/health.py", r"DEFAULT_METRICS_PORT = (\d+)"),
    }
    inv = (ROOT / "deploy" / "config" / "env-inventory.yaml").read_text(encoding="utf-8")
    for name, (path, pat) in src.items():
        m = re.search(pat, (ROOT / path).read_text(encoding="utf-8"))
        assert m and int(m.group(1)) == METRICS_PORTS[name], name
        assert METRICS_PORT_VARS[name] in inv, f"env-inventory 没登记 {METRICS_PORT_VARS[name]}"


def test_networkpolicy_only_collector_reaches_metrics(k8s_docs):
    pols = {d["metadata"]["name"]: d for d in k8s_docs if d["kind"] == "NetworkPolicy"}
    p = pols["metrics-from-collector"]["spec"]
    assert p["podSelector"] == {"matchLabels": {"starbuddy/metrics": "true"}} and p["policyTypes"] == ["Ingress"]
    assert len(p["ingress"]) == 1
    rule = p["ingress"][0]
    assert len(rule["from"]) == 1 and set(rule["from"][0]) == {"namespaceSelector", "podSelector"}, "命名空间与 Pod 选择器同时限定，不放 ipBlock"
    ns = rule["from"][0]["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
    pod = rule["from"][0]["podSelector"]["matchLabels"]
    assert ns.startswith("<由人填写") and all(str(v).startswith("<由人填写") for v in pod.values()) and pod
    assert sorted(x["port"] for x in rule["ports"]) == sorted(METRICS_PORTS.values()) and {x["protocol"] for x in rule["ports"]} == {"TCP"}
    assert not {x["port"] for x in rule["ports"]} & set(BUSINESS_PORTS.values()), "采集组件不放行业务端口"
    assert "ipBlock" not in repr(pols["metrics-from-collector"])


def test_podmonitor_scrapes_only_metrics_path():
    docs = _obs_docs()
    pm = [d for d in docs if d["kind"] == "PodMonitor"]
    assert len(pm) == 1
    spec = pm[0]["spec"]
    assert spec["selector"] == {"matchLabels": {"starbuddy/metrics": "true"}} and spec["namespaceSelector"] == {"matchNames": ["starbuddy-staging"]}
    ep = spec["podMetricsEndpoints"]
    assert len(ep) == 1 and ep[0]["port"] == "metrics" and ep[0]["path"] == "/metrics" and ep[0]["scheme"] == "http"
    assert ep[0]["honorLabels"] is False
    assert ep[0]["metricRelabelings"] == [{"sourceLabels": ["__name__"], "regex": "starbuddy_.*", "action": "keep"}]


def test_sls_log_config():
    lc = [d for d in _obs_docs() if d["kind"] == "AliyunLogConfig"]
    assert len(lc) == 1
    spec = lc[0]["spec"]
    assert spec["logstore"] == "app" and spec["project"].startswith("<由人填写")
    assert "lifeCycle" not in spec, "留存只由 IaC 设（日志库由 IaC 预建，180–365 天），采集配置不写死"
    inputs = spec["logtailConfig"]["inputDetail"]["plugin"]["inputs"]
    assert [i["type"] for i in inputs] == ["service_docker_stdout"]
    d = inputs[0]["detail"]
    assert d["K8sNamespaceRegex"] == "^starbuddy-staging$"
    # 安全审查 A：只采六个后端主容器（Pod 标签 + 容器名白名单）；适配器、前端不采
    assert d["IncludeK8sLabel"] == {"starbuddy/metrics": "true"}
    names = re.fullmatch(r"\^\(([a-z|-]+)\)\$", d["K8sContainerRegex"])
    assert names and set(names.group(1).split("|")) == set(METRICS_PORTS)
    for other in ("adapter-aya", "adapter-zhixing", "adapter-wenli", "web", "learning-sidecar", "xbff"):
        assert not re.fullmatch(d["K8sContainerRegex"], other), other
    procs = spec["logtailConfig"]["inputDetail"]["plugin"]["processors"]
    assert [p["type"] for p in procs] == ["processor_json"]
    assert procs[0]["detail"]["KeepSourceIfParseError"] is False, "解析失败的行（未经字段白名单）不进日志库"
    assert procs[0]["detail"]["KeepSource"] is False
    sls = (DEPLOY / "iac" / "alicloud" / "sls.tf").read_text(encoding="utf-8")
    assert 'logstore_name         = "app"' in sls
    assert "retention_period      = var.log_retention_days" in sls
    head = (DEPLOY / "k8s" / "observability.yaml").read_text(encoding="utf-8").split("apiVersion:")[0]
    assert "由 IaC 预建" in head


def test_observability_manifest_has_no_real_domains_or_addresses():
    api_groups = {"monitoring.coreos.com/v1", "log.alibabacloud.com/v1alpha1"}
    def strings(o):
        if isinstance(o, dict):
            for v in o.values():  # 键是 Kubernetes 标签名（app.kubernetes.io/…），只查取值
                yield from strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from strings(v)
        else:
            yield str(o)
    for s in (x for d in _obs_docs() for x in strings(d)):
        if s in api_groups:
            continue
        assert not re.search(r"\b(?:[a-z0-9-]+\.)+(?:com|cn|net|org|io|cloud)\b", s, re.I), s
        for tok in re.findall(r"\b\d+\.\d+\.\d+\.\d+\b", s):
            assert ipaddress.ip_address(tok).is_private, s


def test_dashboards_reference_datasource_placeholder():
    for p in sorted((OBS / "dashboards").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["templating"]["list"][0]["type"] == "datasource", p.name
        for panel in d["panels"]:
            assert panel["datasource"]["uid"] == "${datasource}", (p.name, panel["title"])
