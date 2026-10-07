"""学习服务调适配器的客户端（learning/adapter_client.py）的变异测试：把重试、错误归类、编码转发、严格 JSON、对象形状、
202、超时、总预算与响应体上限的关键检查逐条改坏，断言客户端测试能发现。

做法同 scripts/practice_mutations.py：每个变异在临时目录里改一处源码再跑测试；只有“测试失败”（退出码 1）算发现，
收集错误、导入错误不算；找不到原文算变异本身写错。临时目录保持仓库结构（services/learning 复制到 repo/services/learning）；
这组测试不读仓库其他目录（契约共享库从 venv 加载，pgharness 只在起库时用 db/），所以不软链任何目录，见 LINKS。
先跑一次不变异的对照组，必须通过。

测试集不连库、不起 Postgres（MockTransport 假适配器 + 只监听 127.0.0.1 的本机 HTTP 假适配器），所以没有“缺 Postgres”的退出码 3；
只在没有安装学习服务依赖时返回 3。并行 8 路，约 25 秒。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]          # services/learning
REPO = HERE.parents[1]
# test_adapter_response_limit.py 里带 ingest 的用例要连库，这里只取不连库的客户端用例（真实连接上的三种超限形态）
TESTS = ["tests/test_adapter_client.py", "tests/test_adapter_response_limit.py", "-k", "not ingest"]
A = "learning/adapter_client.py"
# 只软链测试实际要用的仓库目录：已核对对照组在不链任何目录时通过，故为空（不得链 evals）；以后测试要读仓库目录再按需加
LINKS: tuple[str, ...] = ()

# 名字 → [(文件, 原文, 替换为), …]
MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    # 重试
    "去掉重试": [(A, "for _ in range(2):", "for _ in range(1):")],
    "重试不限一次": [(A, "for _ in range(2):", "for _ in range(3):")],
    "连接失败不重试": [(A, "            continue\n", "            raise last\n")],
    "TransportError 不接住": [(A, "except httpx.TransportError:", "except httpx.ConnectTimeout:")],
    "非 retryable 也重试": [(A, "        if not retryable:\n            raise err\n", "")],
    "retryable 不要求严格为 true": [(A, 'retryable = problem.get("retryable") is True', 'retryable = bool(problem.get("retryable"))')],
    "retryable 恒为 false": [(A, 'retryable = problem.get("retryable") is True', "retryable = False")],
    "重试时换请求体（换键）": [(A, "status, raw = await _post(client, url, body, headers, timeout)",
                          'status, raw = await _post(client, url, {**body, "context": {**body.get("context", {}), '
                          '"idempotency_key": f"5b8e1c52-0000-4000-8000-00000000c0b{_}"}}, headers, timeout)')],
    "重试时换请求头": [(A, "status, raw = await _post(client, url, body, headers, timeout)",
                    'status, raw = await _post(client, url, body, {**headers, "x-attempt": str(_)}, timeout)')],
    # 请求形状
    "不带 timeout": [(A, 'client.stream("POST", url, json=body, headers=headers, timeout=timeout)',
                     'client.stream("POST", url, json=body, headers=headers)')],
    "路径不按契约": [(A, 'url = f"{target.base_url}/plugin/v0/{method}"', 'url = f"{target.base_url}/v0/{method}"')],
    # 错误归类
    "401 不归 502": [(A, "status not in (401, 403, 407)", "status not in (403, 407)")],
    "403 不归 502": [(A, "status not in (401, 403, 407)", "status not in (401, 407)")],
    "407 不归 502": [(A, "status not in (401, 403, 407)", "status not in (401, 403)")],
    "client_error 含 500": [(A, "client_error = 400 <= status < 500", "client_error = 400 <= status <= 500")],
    "client_error 不含 400": [(A, "client_error = 400 <= status < 500", "client_error = 400 < status < 500")],
    "client_error 从 300 起": [(A, "client_error = 400 <= status < 500", "client_error = 300 <= status < 500")],
    "客户端错误一律 502": [(A, "status if client_error else 502,", "502,")],
    "客户端错误编码写错": [(A, '"UPSTREAM_REJECTED" if client_error else "UPSTREAM_UNAVAILABLE"', '"UPSTREAM_UNAVAILABLE"')],
    # 只转编码
    "CODE_RE 不 fullmatch": [(A, "CODE_RE.fullmatch(code)", "CODE_RE.match(code)")],
    "CODE_RE 放宽长度": [(A, 'CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,47}")', 'CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")')],
    "CODE_RE 放宽字符": [(A, 'CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,47}")', 'CODE_RE = re.compile(r"\\w{1,48}")')],
    "CODE_RE 允许小写": [(A, 'CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,47}")', 'CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,47}")')],
    "upstream_code 用 isupper（B1）": [(A, "isinstance(code, str) and CODE_RE.fullmatch(code)", "isinstance(code, str) and code.isupper()")],
    "upstream_code 不转发": [(A, "            upstream_code=upstream_code,\n", "")],
    "转发 detail": [(A, "            upstream_code=upstream_code,\n",
                    "            upstream_code=upstream_code,\n"
                    '            detail=problem.get("detail") if isinstance(problem, dict) else None,\n')],
    # 严格 JSON 与对象形状
    "去掉 parse_constant": [(A, "return json.loads(raw, parse_constant=_reject_constant)", "return json.loads(raw)")],
    "非 dict 放过": [(A, "if not isinstance(data, dict):", "if False:")],
    "非法 JSON 当空对象": [(A, '                raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter returned invalid JSON") from None',
                          "                data = {}")],
    # 202
    "202 当成功": [(A, "if status == 200:", "if status in (200, 202):")],
    "202 不单独处理": [(A, "if status == 202:", "if status == 299:")],
    # 超时
    "httpx 超时码改错": [(A, 'except httpx.TimeoutException:\n            raise IngestError(504, "UPSTREAM_TIMEOUT"',
                        'except httpx.TimeoutException:\n            raise IngestError(503, "UPSTREAM_TIMEOUT"')],
    "httpx 超时也重试": [(A, 'except httpx.TimeoutException:\n            raise IngestError(504, "UPSTREAM_TIMEOUT", "adapter timed out", '
                           "retryable=True) from None\n",
                        'except httpx.TimeoutException:\n            last = IngestError(504, "UPSTREAM_TIMEOUT", "adapter timed out", '
                        "retryable=True)\n            continue\n")],
    "httpx 超时不可重试": [(A, 'except httpx.TimeoutException:\n            raise IngestError(504, "UPSTREAM_TIMEOUT", "adapter timed out", '
                             "retryable=True)",
                          'except httpx.TimeoutException:\n            raise IngestError(504, "UPSTREAM_TIMEOUT", "adapter timed out", '
                          "retryable=False)")],
    "连接超时当连接失败重试": [(A, "except httpx.TimeoutException:", "except httpx.ReadTimeout:")],
    "去掉 asyncio.timeout": [(A, "async with asyncio.timeout(2 * timeout + 1):", "async with asyncio.timeout(None):")],
    "总预算改大": [(A, "async with asyncio.timeout(2 * timeout + 1):", "async with asyncio.timeout(3 * timeout + 1):")],
    "总预算改小": [(A, "async with asyncio.timeout(2 * timeout + 1):", "async with asyncio.timeout(2 * timeout):")],
    "总预算超时码改错": [(A, 'except TimeoutError:\n        raise IngestError(504, "UPSTREAM_TIMEOUT"',
                        'except TimeoutError:\n        raise IngestError(502, "UPSTREAM_TIMEOUT"')],
    # 响应体上限
    "上限 > 改 >=": [(A, "if size > MAX_RESPONSE_BYTES:", "if size >= MAX_RESPONSE_BYTES:")],
    "上限改大": [(A, "MAX_RESPONSE_BYTES = 5 * 1024 * 1024", "MAX_RESPONSE_BYTES = 6 * 1024 * 1024")],
    "去掉上限": [(A, "if size > MAX_RESPONSE_BYTES:", "if False:")],
    "超限当可重试": [(A, 'raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter response too large")',
                    'raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter response too large", retryable=True)')],
}


def _copy(tmp: Path) -> Path:
    root = tmp / "repo"
    dst = root / "services" / "learning"
    shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("__pycache__", ".venv", ".cache", "*.pyc",
                                                             ".env*", ".mypy_cache", ".ruff_cache"))
    for name in LINKS:
        (root / name).symlink_to(REPO / name)
    return dst


def _run(dst: Path, tests: list[str]) -> tuple[int, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "LEARNING_TEST_STRICT": "1",
           "HOME": os.environ.get("HOME", "/tmp")}
    r = subprocess.run([str(HERE / ".venv/bin/python"), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
                       cwd=dst, capture_output=True, text=True, env=env, timeout=300)
    return r.returncode, r.stdout[-2000:]


def one(name: str, edits: list[tuple[str, str, str]]) -> str | None:
    with tempfile.TemporaryDirectory(prefix="adapter-client-mut.") as t:
        dst = _copy(Path(t))
        for fname, pattern, replacement in edits:
            f = dst / fname
            text = f.read_text(encoding="utf-8")
            if pattern not in text:
                return f"变异本身写错（找不到原文）：{name}"
            f.write_text(text.replace(pattern, replacement, 1), encoding="utf-8")
        rc, _ = _run(dst, TESTS)
        if rc != 1:
            return f"{'漏检' if rc == 0 else f'不是被断言发现（退出码 {rc}）'}：{name}"
    return None


def main() -> int:
    if not (HERE / ".venv/bin/python").exists():
        print("adapter-client-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    with tempfile.TemporaryDirectory(prefix="adapter-client-mut.") as t:
        rc, out = _run(_copy(Path(t)), TESTS)
    if rc != 0:
        print(out)
        print(f"adapter-client-mutation：失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda kv: one(kv[0], kv[1]), MUTATIONS.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"adapter-client-mutation：失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"adapter-client-mutation：通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
