"""模型路由的变异测试（specs/model-router-sprint1/spec.md 范围第 4 条）。

每个变异把 learning/model_router 的一段关键检查改坏，复制到临时目录里跑 tests/test_model_router.py，
断言测试能发现（失败）。有一个没被发现就退出码 1。约 10 秒。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = "learning/model_router"

MUTATIONS: dict[str, tuple[str, str, str]] = {
    "运行时不过滤境外（出境第二道防线）": ("router.py", "return env not in NO_EGRESS_ENVS and data_class is DataClass.L0", "return True"),
    "境外只接 L0 改成接到 L1": ("router.py", "and data_class is DataClass.L0", "and data_class.rank <= 1"),
    "启动校验放过 pilot、prod 的境外供应商": ("config.py", '_need(residency is Residency.DOMESTIC, f"{where}: {env} 环境不能出现境外供应商（数据不出境）")', "pass"),
    "启动校验放过未批准的供应商": ("config.py", '_need(whitelist == "approved", f"{where}: {env} 环境只能用白名单里已批准的供应商")', "pass"),
    "可达 L2 的任务允许境外": ("config.py", '_need(vendors[t.vendor].residency is Residency.DOMESTIC, f"{where}: 可达 L2、L3 的任务只能配境内供应商")', "pass"),
    "允许 latest 别名": ("config.py", '_need(not _FLOATING.search(model) and not _FLOATING.search(api_name), f"{where}: 不能用 latest 之类会漂移的别名")', "pass"),
    "主备可以同一厂商同一平台": ("config.py", "_need(not (a.org == b.org and a.platform == b.platform)", "_need(True or not (a.org == b.org and a.platform == b.platform)"),
    "熔断后不过滤数据级别（候选不经过滤）": ("router.py", "return tuple(t for t in route.targets if egress_allowed(self._cfg.vendors[t.vendor], req.data_class, self._cfg.env))",
                                  "return route.targets"),
    "熔断永不打开": ("breaker.py", "if s.failures >= self._threshold:", "if False:"),
    "半开放行多个试探": ("breaker.py", 'if st == "half_open" and not s.probing:', 'if st == "half_open":'),
    "不查 usage_id 与幂等键": ("router.py", "if not req.usage_id or not _KEY.match(req.usage_id) or not req.idempotency_key or not _KEY.match(req.idempotency_key):", "if False:"),
    "不查 prompt 版本": ("router.py", "if not _PROMPT.match(req.prompt_template or \"\") or not _PROMPT_VERSION.match(req.prompt_version or \"\"):", "if False:"),
    "重试换了幂等键": ("router.py", "idempotency_key=req.idempotency_key, timeout=timeout", "idempotency_key=f\"{req.idempotency_key}-{t0}\", timeout=timeout"),
    "client 错误也计入熔断": ("router.py", "if kind in BREAKER_FAILURES:", "if True:"),
    "调用记录带上请求内容": ("router.py", "subject=req.subject,", "subject=str(req.messages),"),
    "model_version 写死": ("router.py", "return RouteResult(output=reply.output, model_version=target.model_version,", "return RouteResult(output=reply.output, model_version=\"routed\","),
}


def main() -> int:
    missed: list[str] = []
    for name, (fname, pattern, replacement) in MUTATIONS.items():
        with tempfile.TemporaryDirectory(prefix="model-router-mut.") as tmp:
            dst = Path(tmp)
            shutil.copytree(ROOT / "learning", dst / "learning", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "config", dst / "config")
            shutil.copy(ROOT / "pytest.ini", dst / "pytest.ini")
            (dst / "tests").mkdir()
            shutil.copy(ROOT / "tests" / "test_model_router.py", dst / "tests" / "test_model_router.py")
            target = dst / PKG / fname
            src = target.read_text(encoding="utf-8")
            if pattern not in src:
                print(f"  变异本身写错（找不到原文）：{name}")
                missed.append(name)
                continue
            target.write_text(src.replace(pattern, replacement, 1), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests/test_model_router.py"],
                               cwd=dst, capture_output=True, text=True,
                               env={"PYTHONPATH": str(dst), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
            if r.returncode == 0:
                print(f"  漏检：{name}")
                missed.append(name)
    if missed:
        print(f"model-router-mutation: 失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"model-router-mutation: 通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
