"""一致性测试入口：启动被测适配器（或参考假适配器），跑同一套测试，输出覆盖率报告。

  # 测一个真实适配器：由本脚本启动进程，随机端口、随机令牌，结束后停掉
  python packages/contracts/conformance/cli.py --adapter-cmd "node --no-warnings src/main.ts" \\
      --adapter-cwd adapters/aya --token-var AYA_ADAPTER_SERVICE_TOKEN \\
      --fixtures adapters/aya/conformance/fixtures.yaml --report /tmp/aya.json

  # 测参考假适配器（可注入违约，见 reference_adapter.FAULTS）
  python packages/contracts/conformance/cli.py --reference [--fault extra_field]

退出码：0 通过；1 测试失败；2 用法或环境错误；3 缺依赖（pytest）。
"""
from __future__ import annotations

import argparse
import os
import secrets
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import OPENER  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

try:
    import pytest
except ImportError:  # pragma: no cover
    print("conformance: 跳过（缺少 pytest；pip install -r packages/contracts/requirements-dev.txt）")
    sys.exit(3)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(url: str, credential: str, proc: subprocess.Popen, seconds: float = 30) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            req = urllib.request.Request(f"{url}/plugin/v0/capabilities", headers={"authorization": f"Bearer {credential}"})
            with OPENER.open(req, timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def run_suite(url: str, credential: str, fixtures: Path, report: Path | None, quiet: bool) -> int:
    os.environ["CONFORMANCE_ADAPTER_TOKEN"] = credential
    args = [str(HERE), "-p", "no:cacheprovider", "--rootdir", str(HERE), "-c", os.devnull,
            "--adapter-url", url, "--conformance-fixtures", str(fixtures)]
    if report:
        args += ["--conformance-report", str(report)]
    args += ["-q", "--no-header", "-p", "no:randomly"] if quiet else ["-v"]
    return int(pytest.main(args))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", action="store_true", help="测参考假适配器")
    ap.add_argument("--fault", default=None, help="给参考假适配器注入一种违约")
    ap.add_argument("--adapter-cmd", help="启动被测适配器的命令")
    ap.add_argument("--adapter-cwd", default=".", help="启动命令的工作目录（相对仓库根目录）")
    ap.add_argument("--token-var", default="ADAPTER_SERVICE_TOKEN", help="被测适配器读取服务间令牌的环境变量名")
    ap.add_argument("--port-var", default="PORT", help="被测适配器读取端口的环境变量名")
    ap.add_argument("--fixtures", default=None, help="学科夹具 YAML（相对仓库根目录）")
    ap.add_argument("--report", default=None, help="覆盖率报告 JSON 输出路径")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    credential = secrets.token_urlsafe(32)  # 每次随机，不落盘
    report = Path(a.report) if a.report else None
    if a.reference:
        from reference_adapter import serve  # noqa: PLC0415
        fixtures = ROOT / (a.fixtures or "packages/contracts/conformance/fixtures/reference.yaml")
        import yaml  # noqa: PLC0415
        subject = yaml.safe_load(fixtures.read_text(encoding="utf-8"))["subject"]
        server, url = serve(subject, credential, a.fault)
        try:
            return run_suite(url, credential, fixtures, report, a.quiet)
        finally:
            server.shutdown()

    if not a.adapter_cmd or not a.fixtures:
        ap.error("需要 --reference，或者同时给出 --adapter-cmd 与 --fixtures")
    port = _free_port()
    # 环境变量白名单（安全审查 S2）：被测适配器可能是任何一科、任何团队写的，不把开发者 shell 里的凭据交给它
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR") if k in os.environ}
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    env[a.token_var] = credential
    env[a.port_var] = str(port)
    proc = subprocess.Popen(shlex.split(a.adapter_cmd), cwd=ROOT / a.adapter_cwd, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        if not _wait_ready(url, credential, proc):
            print("conformance: 失败（被测适配器没有启动）")
            return 2
        return run_suite(url, credential, ROOT / a.fixtures, report, a.quiet)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    sys.exit(main())
