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
    "运行时不过滤境外（出境第二道防线）": ("router.py", "return env in SYNTHETIC_ENVS and data_class is DataClass.L0", "return True"),
    "境外只接 L0 改成接到 L1": ("router.py", "and data_class is DataClass.L0", "and data_class.rank <= 1"),
    "staging 也允许境外": ("config.py", 'SYNTHETIC_ENVS = frozenset({"dev", "test"})', 'SYNTHETIC_ENVS = frozenset({"dev", "test", "staging"})'),
    "启动校验放过 pilot、prod 的境外供应商": ("config.py", '_need(residency is Residency.DOMESTIC, f"{where}: {env} 环境不能出现境外供应商（数据不出境）")', "pass"),
    "启动校验放过未批准的供应商": ("config.py", '_need(whitelist == "approved", f"{where}: {env} 环境只能用白名单里已批准的供应商")', "pass"),
    "可达 L2 的任务允许境外": ("config.py", '_need(vendors[t.vendor].residency is Residency.DOMESTIC, f"{where}: 可达 L2、L3 的任务只能配境内供应商")', "pass"),
    "staging 可达 L2 用未批准的供应商": ("config.py", "if env not in SYNTHETIC_ENVS:\n                    _need(vendors[t.vendor].whitelist", "if False:\n                    _need(vendors[t.vendor].whitelist"),
    "允许 latest 别名": ("config.py", '_need(not _FLOATING.search(model) and not _FLOATING.search(api_name), f"{where}: 不能用 latest 之类会漂移的别名")', "pass"),
    "pilot 不要求带版本的 api_name": ("config.py", '_need(bool(_PINNED_API_NAME.search(api_name)), f"{where}: api_name 必须带日期或版本号（锁定快照）")', "pass"),
    "版本号可以是 YAML 数字": ("config.py", '_need(isinstance(raw.get(k), str), f"{where}: {k} 必须是带引号的字符串")', "pass"),
    "供应商 ID 不按契约校验": ("config.py", "_need(isinstance(vid, str) and bool(_VENDOR_ID.match(vid))", "_need(True or bool(_VENDOR_ID.match(vid))"),
    "不核对地址主机名": ("providers.py", "if vendor.allowed_hosts and parts.hostname.lower() not in vendor.allowed_hosts:", "if False:"),
    "pilot 允许 http": ("providers.py", 'if env not in SYNTHETIC_ENVS and parts.scheme != "https":', "if False:"),
    "手工配置绕过不变量": ("router.py", "        check_invariants(config)\n", ""),
    "主备可以同一厂商同一平台": ("config.py", "_need(not (a.org == b.org and a.platform == b.platform)", "_need(True or not (a.org == b.org and a.platform == b.platform)"),
    "熔断后不过滤数据级别（候选不经过滤）": ("router.py", "return tuple(t for t in route.targets if egress_allowed(self._cfg.vendors[t.vendor], req.data_class, self._cfg.env))",
                                  "return route.targets"),
    "熔断永不打开": ("breaker.py", "if s.failures >= self._threshold:", "if False:"),
    "半开放行多个试探": ("breaker.py", 'if st == "half_open" and s.probe is None:', 'if st == "half_open":'),
    "任何令牌都能释放试探名额": ("breaker.py", "return t.is_probe and self._get(t.vendor, t.task).probe is t", "return True"),
    "意外异常不兜底": ("router.py", "                except Exception:  # noqa: BLE001", "                except ZeroDivisionError:  # noqa: BLE001"),
    "不查 usage_id 与幂等键": ("router.py", "if not req.usage_id or not _KEY.match(req.usage_id) or not req.idempotency_key or not _KEY.match(req.idempotency_key):", "if False:"),
    "prompt 名不按契约校验": ("router.py", '_PROMPT = re.compile(r"^[a-z0-9][a-z0-9\\-]{0,47}$")', '_PROMPT = re.compile(r"^[a-z0-9][a-z0-9._/\\-]{0,63}$")'),
    "不查学科": ("router.py", "if req.subject is not None and req.subject not in SUBJECTS:", "if False:"),
    "重试换了幂等键": ("router.py", "idempotency_key=req.idempotency_key, timeout=timeout", "idempotency_key=f\"{req.idempotency_key}-{t0}\", timeout=timeout"),
    "client 错误也计入熔断": ("router.py", "if kind in BREAKER_FAILURES:", "if True:"),
    "不传总时限时没有上限": ("router.py", "deadline = started + (req.deadline_ms or DEFAULT_DEADLINE_MS) / 1000", "deadline = started + (req.deadline_ms or 10**9) / 1000"),
    "不看 Retry-After": ("router.py", "wait = retry_after if retry_after is not None else BACKOFF_BASE * (2 ** n)", "wait = BACKOFF_BASE * (2 ** n)"),
    "调用记录带上请求内容": ("router.py", "subject=req.subject if req.subject in SUBJECTS else None,", "subject=str(req.messages),"),
    "不一致不告诉调用方": ("router.py", "usage=reply.usage, model_mismatch=mismatch, reported_model=reported)", "usage=reply.usage)"),
    "model_version 写死": ("router.py", "return RouteResult(output=reply.output, model_version=target.model_version,", "return RouteResult(output=reply.output, model_version=\"routed\","),
    "响应体不限大小": ("providers.py", "if len(buf) > MAX_RESPONSE_BYTES:", "if False:"),
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
            # 只有“测试失败”（退出码 1）算发现；收集错误、导入错误（2、4 等）说明变异把代码改坏了，不算（安全审查建议 13）
            if r.returncode != 1:
                print(f"  {'漏检' if r.returncode == 0 else f'不是被断言发现（退出码 {r.returncode}）'}：{name}")
                missed.append(name)
    if missed:
        print(f"model-router-mutation: 失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"model-router-mutation: 通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
