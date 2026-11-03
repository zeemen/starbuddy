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

TESTS = ["tests/test_staging_env.py", "tests/test_health.py",  # 后者：LT-03 健康端点
         "tests/test_obs.py", "tests/test_route_listener.py"]  # LT-05 指标与日志（标签白名单、脱敏、指标端点暴露范围、指标监听）
OB, MT = "learning/obs.py", "learning/metrics.py"
NP, CF, MC, RC, RM = ("learning/netpolicy.py", "learning/config.py", "learning/mastery/config.py", "review_console/config.py",
                      "review_console/__main__.py")
AP, HL, RA = "learning/app.py", "learning/health.py", "review_console/app.py"

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
    "命令行 staging 连接串不分环境": [(RM, "    if env == STAGING:\n        return _staging_host_ok(host)", "    if env == STAGING or private_ipv4(host):\n        return _staging_host_ok(host)")],
    "服务端核对 staging 放过公网": [(RM, "(env == STAGING and private_ipv4(addr))", "(env == STAGING)")],
    # 审查后补（契约 A2、安全 S10）
    "镜像环境标记不查": [(NP, "    if marked is not None and marked != env:", "    if False:")],
    "staging 诊断来源缺省用合成夹具": [(CF, '("knowledge" if env == STAGING else "fixture")', '"fixture"')],
    "对账命令 staging 不核对库主机": [("learning/jobs.py", '    if mode == "staging" and pool is None:\n        check_staging_db_url', '    if False:\n        check_staging_db_url')],
    "对账命令 staging 主机核对放过公网": [("learning/jobs.py", '        local_only(url, "staging")', "        pass")],
    # LT-03 托管库只给主机名时的连接核对（补 LT-01 待人确认 6；tests/test_staging_env.py）
    "staging 命令行不要求 TLS": [(RM, "    if env == STAGING and not tls_verified(query):", "    if False:")],
    "dev 也收托管库主机名": [(RM, "    if host in LOCAL_NAMES or host.startswith(\"/\"):\n        return True",
                         "    if host in LOCAL_NAMES or host.startswith(\"/\") or _DNS_NAME.fullmatch(host):\n        return True")],
    "verify-ca / require 也算校验主机名": [(RM, 'return mode == ["verify-full"] and', 'return mode in (["verify-full"], ["verify-ca"], ["require"]) and')],
    "CA 不限固定路径": [(RM, "roots == [netpolicy.RDS_CA_PATH] and os.path.isfile(netpolicy.RDS_CA_PATH)", "len(roots) == 1 and os.path.isfile(roots[0])")],
    "CA 文件不存在也收": [(RM, "roots == [netpolicy.RDS_CA_PATH] and os.path.isfile(netpolicy.RDS_CA_PATH)", "roots == [netpolicy.RDS_CA_PATH]")],
    "主机名不限格式（收裸名字、大写）": [(RM, "    if _DNS_NAME.fullmatch(host) is None:\n        return False\n", "")],
    "主机名不在连接前解析核对": [(RM, "    return bool(addrs) and all(private_ipv4(a) for a in addrs)", "    return True")],
    "解析结果混有公网也收": [(RM, "    return bool(addrs) and all(private_ipv4(a) for a in addrs)", "    return bool(addrs) and any(private_ipv4(a) for a in addrs)")],
    "staging 命令行收 Unix socket": [(RM, "    if env == STAGING:\n        return _staging_host_ok(host)", "    if env == STAGING and not host.startswith(\"/\"):\n        return _staging_host_ok(host)")],
    # 决定记录（续）第 6 条：服务启动的连库 TLS（netpolicy.check_db_tls，学习服务与审核台后端）
    "服务连库不要求 verify-full": [(NP, '    return (u.scheme in ("postgres", "postgresql") and q.get("sslmode") == ["verify-full"]',
                              '    return (u.scheme in ("postgres", "postgresql") and q.get("sslmode") in (["verify-full"], ["require"])')],
    "服务连库 CA 不限路径": [(NP, "            and q.get(\"sslrootcert\") == [RDS_CA_PATH] and os.path.isfile(RDS_CA_PATH))",
                         "            and os.path.isfile(RDS_CA_PATH))")],
    "服务连库 CA 文件可以不在": [(NP, "            and q.get(\"sslrootcert\") == [RDS_CA_PATH] and os.path.isfile(RDS_CA_PATH))",
                           "            and q.get(\"sslrootcert\") == [RDS_CA_PATH])")],
    "学习服务启动不查连库 TLS": [(AP, '        check_db_tls(settings.database_url, settings.env, "LEARNING_DATABASE_URL")', "        pass")],
    "审核台启动不查连库 TLS": [(RA, '            check_db_tls(s.kge_database_url, s.env, "REVIEW_CONSOLE_KGE_DATABASE_URL")\n', "")],
    # LT-03 健康端点（tests/test_health.py）
    "就绪不等启动前置": [(AP, '        p = state.get("ready_pool")\n        if not await health.pool_alive(p):',
                   '        p = state.get("ready_pool") or pool\n        if not await health.pool_alive(p):')],
    "关闭后仍就绪": [(AP, '            state.pop("ready_pool", None)\n', "")],
    "就绪不查 0058 执行权": [(AP, "        return await p.fetchval(READY_METER_SQL, store.METER_CHECK_FUNCTION) is True", "        return True")],
    "就绪查 0058 时函数不存在就报错": [(AP, "THEN false\"\n                   \" ELSE has_function_privilege(current_user, to_regprocedure($1), 'EXECUTE')",
                                "THEN true\"\n                   \" ELSE has_function_privilege(current_user, to_regprocedure($1), 'EXECUTE')")],
    "就绪不查库": [(HL, '    return pool is not None and await pool.fetchval("SELECT 1") == 1', "    return pool is not None")],
    "就绪不限时": [(HL, "        return await asyncio.wait_for(probe(), timeout) is True", "        return await probe() is True")],
    "就绪回显异常": [(HL, "    except Exception:  # noqa: BLE001 —— 连接断开、超时、池已关闭：都只是“没就绪”\n        return False",
                "    except Exception as e:  # noqa: BLE001\n        raise RuntimeError(str(e))")],
    "就绪不做 single-flight": [(HL, "        if self._task is None:\n            self._task = asyncio.ensure_future(self._run())\n"
                                "        return await asyncio.shield(self._task)", "        return await self._run()")],
    "就绪不缓存": [(HL, "        if last is not None and self.clock() - last[0] < READY_CACHE:", "        if False:")],
    "存活也查库": [(HL, "    async def healthz() -> JSONResponse:\n        return alive()",
               "    async def healthz() -> JSONResponse:\n        return await ready(probe)")],
    "审核台健康端点不过 Host 校验": [(RA, 'if (request.method, request.url.path) in HEALTH_ROUTES and host_name(request.headers.get("host", "")) in hosts:',
                          "if (request.method, request.url.path) in HEALTH_ROUTES:")],
    "审核台 dev 也收监听地址作 Host": [(RA, "    if settings is not None and settings.env == STAGING:", "    if settings is not None:")],
    "审核台不收 Service 地址作 Host": [(RA, "        if settings.extra_host:\n            extra.add(settings.extra_host)", "        pass")],
    "审核台额外 Host 收任意写法": [(RC, "    if not private_ipv4(raw):\n        raise RuntimeError(\"REVIEW_CONSOLE_EXTRA_HOST", "    if False:\n        raise RuntimeError(\"REVIEW_CONSOLE_EXTRA_HOST")],
    "审核台额外 Host dev 也能设": [(RC, "    if env != STAGING:\n        raise RuntimeError(\"REVIEW_CONSOLE_EXTRA_HOST", "    if False:\n        raise RuntimeError(\"REVIEW_CONSOLE_EXTRA_HOST")],
    # LT-06 内部测试运营开关：staging 能开、只认 internal_test
    "审核台内测开关 staging 打不开": [(RC, '    if env not in SERVICE_ENVS:\n        raise RuntimeError("REVIEW_CONSOLE_INTERNAL_TEST',
                               '    if env not in ALLOWED_ENVS:\n        raise RuntimeError("REVIEW_CONSOLE_INTERNAL_TEST')],
    "审核台内测开关收任意取值": [(RC, "    if v != INTERNAL_TEST_FEATURE:", "    if False:")],
    "审核台 Host 收任意内网地址": [(RA, '            if host_name(request.headers.get("host", "")) not in hosts:',
                            '            if host_name(request.headers.get("host", "")) not in hosts and not private_ipv4(host_name(request.headers.get("host", "")).rsplit(":", 1)[0]):'),
                           (RA, "from learning.netpolicy import STAGING", "from learning.netpolicy import STAGING, private_ipv4")],
    "审核台就绪只看一个池": [(RA, "all([await health.pool_alive(p) for p in pair])", "all([await health.pool_alive(p) for p in pair[:1]])")],
    # LT-05 可观测性（learning/obs.py、learning/metrics.py；tests/test_obs.py）：标签白名单、脱敏、指标端点暴露范围
    "指标：标签名不查白名单": [(OB, '    if name not in LABELS:\n        raise ValueError(f"标签 {name} 不在白名单里")',
                       '    if False:\n        raise ValueError(f"标签 {name} 不在白名单里")')],
    "指标：登记时不查标签名": [(OB, '    if bad:\n        raise ValueError(f"标签 {bad} 不在白名单里")', '    if False:\n        raise ValueError(f"标签 {bad} 不在白名单里")')],
    "指标：名字不查 starbuddy_ 前缀": [(OB, "    if not _NAME.fullmatch(name):", "    if False:")],
    "指标：枚举标签收枚举外的取值": [(OB, '        return v if v in ENUMS[name] else "other"', "        return v or \"other\"")],
    "指标：枚举外的取值抛错（不记 other）": [(OB, '        return v if v in ENUMS[name] else "other"',
                                    '        if v not in ENUMS[name]:\n            raise ValueError(name)\n        return v')],
    "指标：中间件写指标出错影响请求": [(OB, "    except Exception:  # noqa: BLE001\n        pass\n\n\nclass MetricsMiddleware:",
                             "    except ValueError:\n        pass\n\n\nclass MetricsMiddleware:")],
    "指标：路由标签收原始路径": [(OB, '"route": lambda: bool(_ROUTE.fullmatch(v)) and not _IDISH.search(v)}', '"route": lambda: True}')],
    "指标：业务端口仍服务 /metrics": [(OB, '        return JSONResponse({"status": "not_found"}, status_code=404, headers=HEADERS)',
                               "        from starlette.responses import Response\n"
                               "        return Response(await scrape(), media_type=CONTENT_TYPE_LATEST, headers=HEADERS)  # type: ignore[return-value]")],
    "指标：指标端口按前缀匹配": [(OB, '    if scope.get("path") != METRICS_PATH or scope.get("query_string"):',
                          '    if not str(scope.get("path")).startswith(METRICS_PATH):')],
    "指标：指标端口不限方法": [(OB, '    elif scope.get("method") != "GET":', "    elif False:")],
    "指标：指标端口可与业务端口相同": [(OB, "    if port is not None and port in taken:", "    if False:")],
    "指标：staging 缺省不起指标监听": [(OB, '        port = default if env == "staging" else None', "        port = None")],
    "指标：学习服务不起指标监听": [("learning/route_listener.py", '    if getattr(settings, "metrics_port", None) is not None:', "    if False:")],
    "指标：调用记录计数出错影响调用（只吞标签错误）": [(MT, "        except Exception:  # noqa: BLE001 —— 计数出任何错都只丢这一条，不影响调用（决定记录（续）第 6 条）",
                                        "        except ValueError:")],
    "指标：计量核对计数出错改变核对结果": [(MT, '    try:\n        METER_CHECK.labels(outcome="allowed" if allowed else "sealed").inc()\n    except Exception:  # noqa: BLE001\n        pass',
                                 '    METER_CHECK.labels(outcome="allowed" if allowed else "sealed").inc()')],
    "指标：复核积压失败不缓存（每次采集都打库）": [(MT, "                self._at = self.clock()\n                return", "                return")],
    "指标：复核积压最早等待不分档": [(MT, "max(age0, coarse_age(float(r[\"oldest\"] or 0.0)))", "max(age0, float(r[\"oldest\"] or 0.0))")],
    "指标：路由标签用原始路径": [(OB, '    path = getattr(scope.get("route"), "path", None)', '    path = scope.get("path")')],
    "指标：/metrics 与健康端点也计入请求": [(OB, '    if scope.get("type") != "http" or scope.get("path") in SKIP_PATHS:', '    if scope.get("type") != "http":')],
    "指标：/metrics 可以缓存": [(OB, 'HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}',
                          'HEADERS = {"x-content-type-options": "nosniff"}')],
    "指标：审核台业务端口的 /metrics 要会话": [(RA, ', ("GET", obs.METRICS_PATH)}', "}")],
    "审核台不起指标监听": [("review_console/__main__.py", '    return metrics_port(e.get("REVIEW_CONSOLE_METRICS_PORT"), env, DEFAULT_METRICS_PORT,',
                       '    return None and metrics_port(e.get("REVIEW_CONSOLE_METRICS_PORT"), env, DEFAULT_METRICS_PORT,')],
    "指标：复核积压不缓存（每次采集都查库）": [(MT, "            if self._at is not None and self.clock() - self._at < BACKLOG_CACHE:", "            if False:")],
    "指标：复核积压的学科不过白名单": [(MT, 's = obs.label_value("subject", r["subject"])', 's = r["subject"]')],
    "指标：计量核对不计数": [("learning/meter.py", "    meter_checked(state in basis.allowed)  # LT-05：放行 / 409 计数（只记结果，不记键与学生）\n", "")],
    "日志：脱敏不丢禁止字段": [(OB, "    return bool(_FORBIDDEN_KEY.fullmatch(normalize_key(key)))", "    return False")],
    "日志：禁止字段不认 session": [(OB, "|id_?card|id_?number|otp|dsn|session|name|", "|id_?card|id_?number|otp|dsn|name|")],
    "日志：脱敏不替换手机号": [(OB, '    re.compile(r"(?<!\\d)(?:\\+?86[- ]?)?1[3-9]\\d[ -]?\\d{4}[ -]?\\d{4}(?!\\d)"),\n', "")],
    "日志：手机号不认空格与连字符分隔": [(OB, r'1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)', r'1[3-9]\d{9}(?!\d)')],
    "日志：正文 UUID 不替换": [(OB, "    s = _UUID.sub(UUID_MARK, s)\n", "")],
    "日志：request_id 也按正文替换": [(OB, '        if nk == "request_id" and isinstance(v, str) and _UUID.fullmatch(v):', "        if False:")],
    "日志：键名不先驼峰归一": [(OB, '    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\\1", str(key)).replace("-", "_").lower()', "    return str(key)")],
    "日志：顶层字段不过白名单": [(OB, "        if nk not in LOG_FIELDS:\n            continue\n", "")],
    "日志：脱敏不处理密钥样式长串": [(OB, "        s = _LONG_SECRET.sub(REDACTED, s)", "        pass")],
    "日志：不截断超长文本": [(OB, '    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + "…[truncated]"', "    return s")],
    "日志：记异常消息": [(OB, '            out["exc_type"] = record.exc_info[0].__name__[:48]', '            out["exc_type"] = str(record.exc_info[1])')],
    "日志：字段不经白名单与脱敏": [(OB, "        for k, v in log_fields(fields).items():", "        for k, v in fields.items():")],
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
