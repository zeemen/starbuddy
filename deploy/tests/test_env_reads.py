"""反向核对（LT-01 契约审查 A3）：扫描各服务源码里读取环境变量的调用，被读取的名字都必须在 deploy/config/env-inventory.yaml
登记（登记在该服务名下，或在下面的白名单里并写明理由）。识别的写法：
  Python  os.environ / environ / e / env / source 的 .get("NAME") 与 ["NAME"]、os.getenv("NAME")、f"PREFIX{…}SUFFIX" 模板、
          形如 XXX_ENV = "NAME" / ENV_VAR = "NAME" 的常量（之后按常量名读取）
  TS      process.env.NAME、process.env['NAME']、env / vars / e 的 ['NAME'] 与 .NAME、形如 XXX_ENV = "NAME" 的常量
清单里的模板名（<SUBJECT> 等）匹配对应的模板读取。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import ROOT

SCAN = {
    "learning": ["services/learning/learning"],
    "review_console": ["services/learning/review_console"],
    "insight": ["services/insight/insight"],
    "knowledge": ["services/knowledge/knowledge"],
    "scheduler": ["services/scheduler/scheduler"],
    "bff": ["services/bff/src"],
    "web": ["apps/web/src", "apps/web/next.config.ts", "apps/web/scripts/serve.ts"],
    "adapter-aya": ["adapters/aya/src"],
    "adapter-zhixing": ["adapters/zhixing/src"],
    "adapter-wenli": ["adapters/wenli/src"],
}
# 白名单：不归部署配置管的名字（运行时与工具链自带、进程内转交、或只在命令行工具里用且不进服务镜像的启动路径），各写理由
ALLOW: dict[str, str] = {
    "NODE_ENV": "Next.js / Node 运行时自带，前端只用来区分开发构建（CSP 的 unsafe-eval），镜像里固定为 production",
    "NODE_USE_ENV_PROXY": "适配器启动时检测并拒绝（模型路由不得走代理），只读不配",
    "NODE_OPTIONS": "适配器启动时检测 --use-env-proxy 并拒绝，只读不配",
    "PATH": "调度器给子进程的固定基础变量",
    "HOME": "工具进程自带",
    "PYTHON": "阿鸦内容包构建工具（adapters/aya/src/pack/hint-scan.ts）选扫描器解释器；不在适配器服务启动路径上，镜像里不运行",
}
PY_PATTERNS = [
    re.compile(r"(?:os\.environ|\benviron|\be|\benv|\bsource|\bvars)\.get\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
    re.compile(r"(?:os\.environ|\benviron|\be|\benv|\bsource)\[\s*[\"']([A-Z][A-Z0-9_]+)[\"']\s*\]"),
    re.compile(r"os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
    re.compile(r"^[A-Z0-9_]*(?:_ENV|ENV_VAR|_VAR)\s*=\s*[\"']([A-Z][A-Z0-9_]+)[\"']", re.M),
]
PY_TEMPLATE = re.compile(r"\.get\(\s*f[\"']([A-Z][A-Z0-9_]*_)\{[^}]*\}([A-Z0-9_]*)[\"']")
TS_PATTERNS = [
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]+)"),
    re.compile(r"process\.env\[\s*['\"]([A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"\b(?:env|vars|e)\[\s*['\"]([A-Z][A-Z0-9_]+)['\"]\s*\]"),
    re.compile(r"\benv\.([A-Z][A-Z0-9_]{2,})\b"),
    re.compile(r"\b[A-Z0-9_]*_ENV\s*=\s*['\"]([A-Z][A-Z0-9_]+)['\"]"),
]


def files(paths):
    for p in paths:
        q = ROOT / p
        if q.is_file():
            yield q
        else:
            yield from (f for f in q.rglob("*") if f.suffix in (".py", ".ts", ".tsx") and "__pycache__" not in f.parts)


def reads(svc):
    names, templates = set(), set()
    for f in files(SCAN[svc]):
        text = f.read_text(encoding="utf-8")
        if f.suffix == ".py":
            for pat in PY_PATTERNS:
                names |= set(pat.findall(text))
            templates |= set(PY_TEMPLATE.findall(text))
        else:
            for pat in TS_PATTERNS:
                names |= set(pat.findall(text))
    return names, templates


def registered(inventory, svc):
    out = {v["name"] for v in inventory["services"][svc]["vars"]}
    if svc == "scheduler":  # 调度器把各身份的变量转给子进程（scheduler/config.py 的身份表）
        for other in ("insight", "learning", "review_console"):
            out |= {v["name"] for v in inventory["services"][other]["vars"]}
    return out


def covered(name, reg):
    if name in reg or name in ALLOW:
        return True
    for k in reg:
        if "<" in k:
            prefix, rest = k.split("<", 1)
            suffix = rest.split(">", 1)[1]
            if name.startswith(prefix) and name.endswith(suffix) and re.fullmatch(r"[A-Z0-9]+", name[len(prefix): len(name) - len(suffix) or None]):
                return True
    return False


@pytest.mark.parametrize("svc", sorted(SCAN))
def test_every_env_read_is_registered(svc, inventory):
    names, templates = reads(svc)
    reg = registered(inventory, svc)
    missing = sorted(n for n in names if not covered(n, reg))
    assert not missing, f"{svc}：源码读取了没有登记的环境变量 {missing}"
    tmpl_reg = {(k.split("<", 1)[0], k.split(">", 1)[1]) for k in reg if "<" in k}
    missing_t = sorted(f"{p}<…>{s}" for p, s in templates if (p, s) not in tmpl_reg)
    assert not missing_t, f"{svc}：源码按模板读取了没有登记的环境变量 {missing_t}"
    assert names, f"{svc}：没有扫描到任何读取（扫描写法失效）"
