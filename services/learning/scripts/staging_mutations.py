"""LT-01 staging 运行环境（specs/launch-lt01-deploy-packaging 决定记录第 1、5 条）的变异测试：把学习服务与审核台后端的 staging 启动校验
逐条改坏，断言 tests/test_staging_env.py 能发现。做法同 scripts/review_console_mutations.py（复用 practice_mutations 的临时目录复制与
运行）：每个变异在临时目录里改一处源码再跑测试，只有“测试失败”（退出码 1）算发现；先跑不变异的对照组。只用纯函数，不连库（约 30 秒）。"""
from __future__ import annotations

import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import practice_mutations as PM  # noqa: E402

TESTS = ["tests/test_staging_env.py"]
NP, CF, MC, RC, RM = ("learning/netpolicy.py", "learning/config.py", "learning/mastery/config.py", "review_console/config.py",
                      "review_console/__main__.py")

MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    "pilot 也当 staging": [(NP, 'SERVICE_ENVS = LOCAL_ENVS + (STAGING,)', 'SERVICE_ENVS = LOCAL_ENVS + (STAGING, "pilot")')],
    "内网判断放过公网": [(NP, "return str(ip) == host and any(ip in n for n in _PRIVATE_V4)", "return True")],
    "staging 服务间地址收任意 http": [(NP, "    return bool(m) and private_ipv4(m.group(1))", "    return bool(m)")],
    "dev 也收内网服务间地址": [(NP, "    if env != STAGING:\n        return False", "    if False:\n        return False")],
    "dev 也能改监听地址": [(NP, '    if env != STAGING:\n        raise RuntimeError(f"{name} 只有 staging', '    if False:\n        raise RuntimeError(f"{name} 只有 staging')],
    "staging 监听地址不限内网": [(NP, "    if not private_ipv4(raw):\n", "    if False:\n")],
    "staging 收合成配置": [(CF, '                if e.get(name) == "synthetic":', '                if False:')],
    "staging 收候选契约开关": [(CF, '            if e.get("STARBUDDY_ALLOW_CANDIDATE_CONTRACT"):', '            if False:')],
    "staging 收合成夹具": [(CF, '            if e.get("LEARNING_DIAGNOSIS_SOURCE") == "fixture":', '            if False:')],
    "监听地址不经校验": [(CF, '        host = listen_host(e.get("LEARNING_HOST"), env, "LEARNING_HOST")', '        host = e.get("LEARNING_HOST") or "127.0.0.1"')],
    "适配器地址不经校验": [(CF, "                if not service_url_ok(url, env):", "                if False:")],
    "掌握配置在 pilot 也加载": [(MC, '_need(env in ("dev", TEST_ENV, "staging"),', '_need(env in ("dev", TEST_ENV, "staging", "pilot"),')],
    "审核台监听地址不经校验": [(RC, '        host = listen_host(e.get("REVIEW_CONSOLE_HOST"), env, "REVIEW_CONSOLE_HOST")', '        host = e.get("REVIEW_CONSOLE_HOST") or "127.0.0.1"')],
    "命令行 staging 连接串不分环境": [(RM, "    if env == STAGING and private_ipv4(host):\n        return True", "    if private_ipv4(host):\n        return True")],
    "服务端核对 staging 放过公网": [(RM, "(env == STAGING and private_ipv4(addr))", "(env == STAGING)")],
    # 审查后补（契约 A2、安全 S10）
    "镜像环境标记不查": [(NP, "    if marked is not None and marked != env:", "    if False:")],
    "staging 诊断来源缺省用合成夹具": [(CF, '("knowledge" if env == STAGING else "fixture")', '"fixture"')],
    "对账命令 staging 不核对库主机": [("learning/jobs.py", '    if mode == "staging" and pool is None:\n        check_staging_db_url', '    if False:\n        check_staging_db_url')],
    "对账命令 staging 主机核对放过公网": [("learning/jobs.py", '        local_only(url, "staging")', "        pass")],
}

# 前端启动包装（安全审查 A1、S7、S10）：apps/web/src/lib/serve-env.ts 与 consent-version.ts，断言 apps/web/tests/serve-env.test.ts 能发现
WEB_TESTS = ["tests/serve-env.test.ts"]
SE, CV = "src/lib/serve-env.ts", "src/lib/consent-version.ts"
WEB_MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    "前端 dev 也能改监听地址": [(SE, "    if (run !== 'staging') throw new Error('WEB_HOST 只有 staging", "    if (false) throw new Error('WEB_HOST 只有 staging")],
    "前端 staging 监听地址不限内网": [(SE, "    if (!privateIpv4(host)) throw new Error", "    if (false) throw new Error")],
    "前端内网判断放过公网": [(SE, "  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);", "  return true;")],
    "前端收 pilot": [(SE, "['dev', 'test', 'staging'].includes(run)", "['dev', 'test', 'staging', 'pilot'].includes(run)")],
    "前端镜像环境标记不查": [(SE, "  if (marked !== undefined && marked !== run) throw new Error", "  if (false) throw new Error")],
    "前端 staging 不查同意版本": [(SE, "    if (consentVersions(env) === null) {", "    if (false) {")],
    "前端 staging 收开发页面开关": [(SE, "    if (env['STARBUDDY_DEV_PAGES']) throw new Error", "    if (false) throw new Error")],
    "同意版本合成判断大小写敏感": [(CV, "v.toLowerCase().startsWith('synthetic-')", "v.startsWith('synthetic-')")],
    "staging 同意版本退回共用变量": [(CV, "    if (!own || !VERSION.test(own) || isSyntheticVersion(own)) return null;",
                               "    if (!own) return `${CONSENT_DOCS[type]}@${env['STARBUDDY_CONSENT_VERSION'] ?? 'x'}`;")],
}
WEB = PM.REPO / "apps" / "web"


def _web_copy(tmp: Path) -> Path:
    import shutil
    dst = tmp / "web"
    (dst / "src" / "lib").mkdir(parents=True)
    (dst / "tests").mkdir()
    for f in (SE, CV):
        shutil.copy(WEB / f, dst / f)
    for t in WEB_TESTS:
        shutil.copy(WEB / t, dst / t)
    return dst


def _web_run(dst: Path) -> int:
    import shutil
    import subprocess
    node = shutil.which("node")
    r = subprocess.run([node, "--no-warnings", "--test", *WEB_TESTS], cwd=dst, capture_output=True, text=True, timeout=120)
    return r.returncode


def web_one(name: str, edits: list[tuple[str, str, str]]) -> str | None:
    with tempfile.TemporaryDirectory(prefix="staging-web-mut.") as t:
        dst = _web_copy(Path(t))
        for fname, pattern, replacement in edits:
            f = dst / fname
            text = f.read_text(encoding="utf-8")
            if pattern not in text:
                return f"变异本身写错（找不到原文）：{name}"
            f.write_text(text.replace(pattern, replacement, 1), encoding="utf-8")
        rc = _web_run(dst)
        if rc != 1:
            return f"{'漏检' if rc == 0 else f'不是被断言发现（退出码 {rc}）'}：{name}"
    return None


def web_group() -> int:
    import shutil
    if shutil.which("node") is None:
        print("staging-mutation（前端）：跳过（没有 node）")
        return 3
    with tempfile.TemporaryDirectory(prefix="staging-web-mut.") as t:
        if _web_run(_web_copy(Path(t))) != 0:
            print("staging-mutation（前端）：失败（对照组没有通过）")
            return 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        missed = [r for r in pool.map(lambda kv: web_one(kv[0], kv[1]), WEB_MUTATIONS.items()) if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"staging-mutation（前端）：失败，{len(missed)}/{len(WEB_MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"staging-mutation（前端）：通过（{len(WEB_MUTATIONS)} 个变异全部被发现）")
    return 0


def main() -> int:
    if not (PM.HERE / ".venv/bin/python").exists():
        print("staging-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    with tempfile.TemporaryDirectory(prefix="staging-mut.") as t:
        rc, out = PM._run(PM._copy(Path(t)), TESTS)
    if rc != 0:
        print(out)
        print(f"staging-mutation：失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda kv: PM.one(kv[0], kv[1], TESTS), MUTATIONS.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"staging-mutation：失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"staging-mutation：通过（{len(MUTATIONS)} 个变异全部被发现）")
    return web_group()


if __name__ == "__main__":
    sys.exit(main())
