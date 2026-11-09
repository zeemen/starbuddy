"""调度器（PLT-13 第一步，specs/scheduler-plt13）的变异测试：把时间表计算、配置校验、环境白名单、启动核对、超时、退出码映射、
告警级别、计数清洗、咨询锁与常驻循环的关键检查逐条改坏，断言调度器的测试能发现。

做法同 services/learning/scripts/practice_mutations.py：每个变异在临时目录里复制一份 services/scheduler（仓库其余部分软链接回去，
保持目录结构：子进程解释器、审核台的本机核对、洞察服务的一次性库都按相对位置找），改一处源码再跑测试；只有“测试失败”（退出码 1）
算发现，原文找不到或不唯一、收集错误（退出码 2 及以上）算变异本身写错。先跑一次不变异的对照组，必须通过。
SIE-05 起包括中圈指标静默重算：insight 身份放行 metrics、其余未放行子命令仍拒绝、每日一次、库上 --all 真实写出 agent 可见的指标判断。两组：
- 纯函数与运行器：tests/test_schedule.py、test_config.py、test_runner.py，不连库（子进程用假模块）；
- 库上：tests/test_db_e2e.py，一次性 Postgres（每个变异起一个库）；没有本机 Postgres 或子进程的 venv 时退出码 3（Makefile 决定是否算失败）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from glob import glob
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]          # services/scheduler
REPO = HERE.parents[1]
PY = HERE / ".venv" / "bin" / "python"
PURE_TESTS = ["tests/test_schedule.py", "tests/test_config.py", "tests/test_runner.py", "tests/test_staging_env.py",  # LT-01 staging
              "tests/test_health.py",  # LT-03 健康监听
              "tests/test_metrics.py"]  # LT-05 指标
DB_TESTS = ["tests/test_db_e2e.py"]
S, C, R, L, M = "scheduler/schedule.py", "scheduler/config.py", "scheduler/runner.py", "scheduler/loop.py", "scheduler/__main__.py"
H = "scheduler/health.py"
Y = "config/schedule.synthetic.yaml"

PURE: dict[str, list[tuple[str, str, str]]] = {
    # 时间表（纯函数）
    "每 N 分钟不对齐整点": [(S, "slot = (m // s.every_minutes + 1) * s.every_minutes", "slot = m + s.every_minutes")],
    "每天恰好在点上不往后推": [(S, "return cand if cand > local else _at(today + timedelta(days=1), s.hour, s.minute)",
                         "return cand if cand >= local else _at(today + timedelta(days=1), s.hour, s.minute)")],
    "每周不按周几": [(S, "d = today + timedelta(days=(s.weekday - today.weekday()) % 7)", "d = today")],
    "每周恰好在点上不推到下周": [(S, "return cand if cand > local else _at(d + timedelta(days=7), s.hour, s.minute)",
                            "return cand if cand >= local else _at(d + timedelta(days=7), s.hour, s.minute)")],
    "时区不是上海": [(S, "TZ = ZoneInfo(TZ_NAME)", "TZ = ZoneInfo(\"UTC\")")],
    "跨月末按日加一": [(S, "return cand if cand > local else _at(today + timedelta(days=1), s.hour, s.minute)",
                    "return cand if cand > local else _at(today.replace(day=min(today.day + 1, 28)), s.hour, s.minute)")],
    "每小时跨日算错": [(S, "            nxt = local.replace(minute=0, second=0, microsecond=0, tzinfo=None) + timedelta(hours=1)\n"
                         "            cand = _at(nxt.date(), nxt.hour, s.minute)",
                    "            cand = _at(today, (local.hour + 1) % 24, s.minute)")],
    "N 不整除 1440 也收": [(S, "if n > MINUTES_PER_DAY // 2 or MINUTES_PER_DAY % n:", "if n > MINUTES_PER_DAY // 2:")],
    "不要求输入带时区": [(S, "if after.tzinfo is None or after.utcoffset() is None:", "if False:")],
    # 配置
    "子命令不按身份限定": [(C, "_need(args[0] in ident.commands,", "_need(True,")],
    # 中圈指标静默重算（SIE-05）：insight 身份放行 metrics，其余未放行的子命令仍拒绝
    "insight 身份不放行 metrics": [(C, "frozenset({\"sweep\", \"weekly\", \"metrics\"})", "frozenset({\"sweep\", \"weekly\"})")],
    "insight 身份连带放行 sync": [(C, "frozenset({\"sweep\", \"weekly\", \"metrics\"})", "frozenset({\"sweep\", \"weekly\", \"metrics\", \"sync\"})")],
    "调度器身份也放行 metrics": [(C, "\"partition-ensure\", \"retention-purge\"})", "\"partition-ensure\", \"retention-purge\", \"metrics\"})")],
    # 模型调用记录留存删除（specs/plt-model-call-log，迁移 0060）：只放行给调度器身份、每日一次、批量 1000
    "调度器身份不放行留存删除": [(C, "\"spot-check\", \"model-call-purge\",", "\"spot-check\",")],
    # 分区续建与保留期清理（specs/plt23-data-maintenance，迁移 0062、0063）：只放行给调度器身份
    "调度器身份不放行分区续建": [(C, "\"partition-ensure\", \"retention-purge\"})", "\"retention-purge\"})")],
    "调度器身份不放行保留期清理": [(C, "\"partition-ensure\", \"retention-purge\"})", "\"partition-ensure\"})")],
    "insight 身份也放行留存删除": [(C, "frozenset({\"sweep\", \"weekly\", \"metrics\"})", "frozenset({\"sweep\", \"weekly\", \"metrics\", \"model-call-purge\"})")],
    "留存删除频率不是每日一次": [(Y, "    schedule: daily 04:10\n    identity: scheduler\n    args: [model-call-purge",
                             "    schedule: hourly :10\n    identity: scheduler\n    args: [model-call-purge")],
    "留存删除批量改了": [(Y, "    args: [model-call-purge, --batch, \"1000\"]", "    args: [model-call-purge, --batch, \"10000\"]")],
    "指标重算可以启动补跑": [(C, "CATCH_UP_COMMANDS = frozenset({\"weekly\"})", "CATCH_UP_COMMANDS = frozenset({\"weekly\", \"metrics\"})")],
    "指标重算频率不是每日一次": [(Y, "    schedule: daily 03:20\n    identity: insight\n    args: [metrics, --all, --skip, persistence]",
                             "    schedule: hourly :20\n    identity: insight\n    args: [metrics, --all, --skip, persistence]")],
    "定时作业不跳过学习持续性（纯配置）": [(Y, "    args: [metrics, --all, --skip, persistence]", "    args: [metrics, --all]")],
    # 附加环境变量：每个身份的显式白名单；连接串类名字只认本身份的
    "声明的附加变量不查白名单（同前缀都收）": [(C, "    return name in identity.extra_names and not is_url_name(name)",
                                  "    return name.startswith(identity.env_var.rsplit(\"_\", 1)[0] + \"_\") and not is_url_name(name)")],
    "白名单收连接串类名字": [(C, "    return name in identity.extra_names and not is_url_name(name)",
                        "    return name in identity.extra_names or is_url_name(name)")],
    "通配声明不查登记的前缀": [(C, "        return name[:-1] in identity.extra_prefixes", "        return True")],
    "通配匹配收连接串类名字": [(C, "and bool(_ENV_NAME.fullmatch(name)) and not is_url_name(name) and not any(",
                          "and bool(_ENV_NAME.fullmatch(name)) and not any(")],
    "连接串后缀只认 _DATABASE_URL": [(C, "URL_SUFFIXES = (\"_URL\", \"_DSN\", \"_DATABASE_URL\")", "URL_SUFFIXES = (\"_DATABASE_URL\",)")],
    "通配匹配不过滤凭据字样": [(C, " and not any(w in name for w in FORBIDDEN_ENV_WORDS))", ")")],
    "通配匹配不限登记的前缀": [(C, "    return (prefix in identity.extra_prefixes and name.startswith(prefix)",
                          "    return (name.startswith(prefix)")],
    "审核台退出码 1 不算拒绝": [(C, "frozenset({\"expire-affairs\"}), refused_exits=frozenset({1, 2})),",
                           "frozenset({\"expire-affairs\"}), refused_exits=frozenset({2})),")],
    "启动补跑不限周报": [(C, "_need(not catch_up or args[0] in CATCH_UP_COMMANDS,", "_need(True,")],
    "时区可以随便配": [(C, "_need(doc[\"timezone\"] == TZ_NAME,", "_need(True,")],
    "合成配置也算已签字": [(C, "signed = bool(doc[\"signed_by\"].strip() and doc[\"signed_at\"].strip()) and not doc[\"synthetic\"]",
                       "signed = bool(doc[\"signed_by\"].strip() and doc[\"signed_at\"].strip())")],
    "任务级超时默认不是 10 分钟": [(C, "DEFAULT_TIMEOUT = 600", "DEFAULT_TIMEOUT = 3600")],
    "参数不限简单字符串": [(C, "_ARG = re.compile(r\"[A-Za-z0-9_.:=+/-]{1,64}\")", "_ARG = re.compile(r\".{1,64}\")")],
    # 环境白名单与启动核对
    "子进程继承调度器的全部环境": [(R, "    out = dict(BASE_ENV)\n    for name in job.required_env:",
                              "    out = {**BASE_ENV, **environ}\n    for name in job.required_env:")],
    "子进程继承调度器的 PATH": [(R, "    out = dict(BASE_ENV)\n    for name in job.required_env:",
                             "    out = dict(BASE_ENV, PATH=environ.get(\"PATH\", \"\"))\n    for name in job.required_env:")],
    "运行时通配匹配不核对名字": [(R, "if v and matched_name_ok(job.identity, prefix, k)}", "if v and k.startswith(prefix)}")],
    "子进程连接串不加语句超时": [(R, "    env[url] = with_statement_timeout(env[url], statement_timeout_ms(job.timeout_seconds))", "    pass")],
    "语句超时不小于任务超时": [(R, "return max(1000, int(timeout_seconds * 900))", "return max(1000, int(timeout_seconds * 1000))")],
    "直接构造的任务不再核附加变量": [(R, "        if not env_name_ok(job.identity, pattern):", "        if False:")],
    "身份的运行环境不限 dev/test": [(R, "    elif run not in ALLOWED_ENVS:", "    elif False:")],
    "staging 下身份环境可以混用": [(R, "        if run != STAGING:\n", "        if False:\n")],
    "连接串不核对本机": [(R, "            check_local_url(k, v, run)", "            pass")],
    "连接串核对总按 staging 口径": [(R, "            check_local_url(k, v, run)", "            check_local_url(k, v, STAGING)")],
    "缺必填变量不报": [(R, "        if not environ.get(name):\n            raise PreflightError(f\"任务 {job.name} 缺少 {name}\")\n"
                         "        out[name] = environ[name]", "        out[name] = environ.get(name, \"\")")],
    "子进程解释器不存在也跑": [(R, "    if not interpreter.exists():", "    if False:")],
    "取锁连接不核对服务端": [(R, "        await check_server_local(conn, env.get(job.identity.env_var))\n        got =", "        got =")],
    # 运行：超时、退出码、告警、清洗、锁
    "任务级超时不生效": [(R, "deadline = loop.time() + job.timeout_seconds", "deadline = loop.time() + 3600")],
    "运行中不探活锁连接": [(R, "if loop.time() < deadline and not await handle.probe():", "if False:")],
    "退出码 1 当成功": [(R, "return \"partial\" if returncode == 1 else \"crashed\"", "return \"ok\" if returncode == 1 else \"crashed\"")],
    "被信号杀掉不算崩溃": [(R, "return \"partial\" if returncode == 1 else \"crashed\"", "return \"partial\" if returncode == 1 else \"ok\"")],
    "拒绝退出码不按身份区分": [(R, "if returncode in (identity.refused_exits if identity is not None else frozenset({2})):",
                          "if returncode == 2:")],
    "锁丢失不告警": [(R, "\"lock_error\", \"lock_lost\", \"skipped_running\"})", "\"lock_error\", \"skipped_running\"})")],
    "拒绝的退出码不是 2": [(R, "EXIT = {\"ok\": 0, \"partial\": 1, \"refused\": 2,", "EXIT = {\"ok\": 0, \"partial\": 1, \"refused\": 1,")],
    "部分失败不告警": [(R, "ALERT = frozenset({\"partial\", ", "ALERT = frozenset({")],
    "stale_estimate 不告警": [(R, "return outcome in ALERT or (isinstance(stale", "return outcome in ALERT or False and (isinstance(stale")],
    "计数不清洗 UUID": [(R, "return v if _TEXT.fullmatch(v) and not _UUID.search(v) else None", "return v")],
    "计数不限键名": [(R, "            if isinstance(k, str) and _KEY.fullmatch(k):", "            if isinstance(k, str):")],
    "错误摘要不抹 UUID": [(R, "    s = _UNSAFE.sub(\" \", _UUID.sub(\"uuid\", s))", "    s = _UNSAFE.sub(\" \", s)")],
    "错误摘要不按字符白名单": [(R, "    s = _UNSAFE.sub(\" \", _UUID.sub(\"uuid\", s))", "    s = _UUID.sub(\"uuid\", s)")],
    "错误摘要不剥连接串": [(R, "    s = _USER.sub(\" \", _URLISH.sub(\" \", lines[-1]))", "    s = _USER.sub(\" \", lines[-1])")],
    "错误摘要不剥 user=": [(R, "    s = _USER.sub(\" \", _URLISH.sub(\" \", lines[-1]))", "    s = _URLISH.sub(\" \", lines[-1])")],
    "计数清洗放过 @": [(R, "_TEXT = re.compile(r\"[A-Za-z0-9_.:+-]{1,40}\")", "_TEXT = re.compile(r\"[A-Za-z0-9_.:@+-]{1,40}\")")],
    "输出不是 JSON 也算成功": [(R, "return \"ok\" if counts is not None else \"bad_output\"", "return \"ok\"")],
    "取不到锁也执行": [(R, "                if not handle.acquired:\n                    return done(\"skipped_locked\")",
                    "                if False:\n                    return done(\"skipped_locked\")")],
    "锁键不按任务名派生": [(R, "f\"starbuddy.scheduler:{job_name}\".encode(\"utf-8\")", "b\"starbuddy.scheduler\"")],
    "dry-run 也取锁": [(R, "        if dry_run:\n            return done(\"dry_run\"", "        if False:\n            return done(\"dry_run\"")],
    # 常驻循环
    "上一轮没结束也重叠": [(L, "(busy if t is not None and not t.done() else start).append(job)", "start.append(job)")],
    "错过的轮次补跑": [(L, "            self.next_due[name] = next_run(job.schedule, now)",
                    "            self.next_due[name] = next_run(job.schedule, self.next_due[name])")],
    "启动补跑失效": [(L, "now if j.catch_up else next_run(j.schedule, now)", "next_run(j.schedule, now)")],
    "所有任务都启动补跑": [(L, "now if j.catch_up else next_run(j.schedule, now)", "now")],
    # 命令行
    "SCHEDULER_ENV 不限 dev/test/staging": [(M, "    if env.get(ENV_VAR, \"\") not in SERVICE_ENVS:", "    if False:")],
    "staging 也收合成时间表": [(M, "    if env.get(ENV_VAR) == STAGING and not cfg.signed:", "    if False:")],
    "镜像环境标记不查": [(M, "    if marked is not None and marked != env.get(ENV_VAR):", "    if False:")],
    "启动核对不过也启动": [(M, "    if problems:\n        for msg in problems:", "    if False:\n        for msg in problems:")],
    "--once 也核对全部任务": [(M, "    for job in selected:\n        try:\n            run.check(job)",
                          "    for job in cfg.jobs.values():\n        try:\n            run.check(job)")],
    "dry-run 列出变量的值": [(M, "\"env\": sorted(child_env(job, env))", "\"env\": sorted(f\"{k}={v}\" for k, v in child_env(job, env).items())")],
    # LT-03 健康监听（tests/test_health.py）
    "健康监听配置不合格不按拒绝处理（退出码不是 2）": [(M, "    except (health.HealthConfigError, PreflightError) as e:", "    except PreflightError as e:")],
    "健康监听端口不限范围": [(H, "not 1 <= int(raw_port) <= 65535", "False")],
    "健康监听地址不经内网口径": [(H, "    return _listen_host(environ.get(HOST_VAR), env), int(raw_port)",
                          "    return environ.get(HOST_VAR) or \"127.0.0.1\", int(raw_port)")],
    "就绪不看循环在不在跑": [(H, "        if not self.running:\n            return False\n        async with self._lock:", "        async with self._lock:"),
                       (H, "            return ok and self.running", "            return ok")],
    "就绪不查库": [(H, "            ok = await self.db_probe() is True\n", "            ok = True\n")],
    "就绪探测不限并发": [(H, "        async with self._lock:", "        async with contextlib.nullcontext():")],
    "就绪探测不复用结果": [(H, "            if last is not None and time.monotonic() - last[0] < READY_CACHE:", "            if False:")],
    "就绪回显异常": [(H, "    except Exception:  # noqa: BLE001 —— 不区分原因、不回显异常\n        ok = False\n    return response(200",
                "    except Exception as e:  # noqa: BLE001\n        return response(503, {\"status\": \"not_ready\", \"error\": str(e)})\n    return response(200")],
    "健康端点按前缀匹配": [(H, "    if path not in (HEALTH_PATH, READY_PATH):",
                     "    if not path.startswith((HEALTH_PATH, READY_PATH)):")],
    # LT-05 指标（tests/test_metrics.py）：任务成败计数、标签白名单、指标端点暴露范围（决定记录（续）第 5、6 条）
    "健康监听也服务 /metrics": [(H, "    if path not in (HEALTH_PATH, READY_PATH):", "    if path not in (HEALTH_PATH, READY_PATH, METRICS_PATH):"),
                          (H, "    if path == HEALTH_PATH:\n        return response(200, {\"status\": \"ok\"})",
                           "    if path == HEALTH_PATH:\n        return response(200, {\"status\": \"ok\"})\n    if path == METRICS_PATH:\n"
                           "        from . import metrics\n        return response(200, {}, payload=metrics.render(), content_type=METRICS_TYPE)")],
    "指标监听按前缀匹配（别的路径也应答）": [(H, "    if path != METRICS_PATH:", "    if not path.startswith(METRICS_PATH):")],
    "指标监听不限方法": [(H, "    if method == \"GET\":\n        from . import metrics", "    if True:\n        from . import metrics")],
    "指标监听在 staging 缺省不起": [(H, "        if env != \"staging\":\n            return None\n        port = DEFAULT_METRICS_PORT",
                             "        return None")],
    "指标端口可与健康端口相同": [(H, "    if health_listen is not None and port == health_listen[1]:", "    if False:")],
    "指标端口不限范围": [(H, "not 1 <= int(raw) <= 65535", "False")],
    "指标监听不起": [(L, "    if metrics is not None:\n        await metrics.start()\n", "")],
    "任务结果计数出错影响调度（只吞标签错误）": [("scheduler/metrics.py", "    except Exception:  # noqa: BLE001 —— 计数出任何错都只丢这一条",
                                       "    except ValueError:")],
    "运行器不记指标": [("scheduler/runner.py", "        metrics.record(r)\n", "")],
    "任务结果不分成败（一律记 ok）": [("scheduler/metrics.py", "RUNS.labels(task=result.job, outcome=result.outcome).inc()",
                               "RUNS.labels(task=result.job, outcome=\"ok\").inc()")],
    "失败也刷新最近成功时刻": [("scheduler/metrics.py", "        if result.outcome == \"ok\":\n            LAST_OK", "        if True:\n            LAST_OK")],
    "指标带上错误摘要标签": [("scheduler/metrics.py", "                   (\"task\", \"outcome\"))", "                   (\"task\", \"outcome\", \"reason\"))"),
                     ("scheduler/metrics.py", "RUNS.labels(task=result.job, outcome=result.outcome).inc()",
                      "RUNS.labels(task=result.job, outcome=result.outcome, reason=result.reason).inc()")],
    "健康端点不限方法": [(H, "    if method != \"GET\":", "    if False:")],
    "健康探针连接串不核对本机": [(H, "    check_local_url(\"SCHEDULER_DATABASE_URL\", url, env)\n", "")],
    "收到停止仍就绪": [(L, "        if health is not None:\n            health.running = False\n        stop.set()", "        stop.set()")],
}

DB: dict[str, list[tuple[str, str, str]]] = {
    "咨询锁形同虚设（总是取到）": [(R, "got = bool(await conn.fetchval(\"SELECT pg_try_advisory_lock($1::bigint)\", lock_key(job.name)))",
                            "got = True")],
    "所有任务共用一把锁": [(R, "\"SELECT pg_try_advisory_lock($1::bigint)\", lock_key(job.name)", "\"SELECT pg_try_advisory_lock($1::bigint)\", 1")],
    "锁不按该身份的连接取": [(R, "conn = await asyncpg.connect(env[job.identity.url_var],", "conn = await asyncpg.connect(env[\"SCHEDULER_DATABASE_URL\"],")],
    "子进程拿不到白名单环境": [(R, "cwd=str(cwd), env=dict(env),", "cwd=str(cwd), env=dict(BASE_ENV),")],
    "子进程在调度器目录里跑": [(R, "        cwd = self.interpreter(job)[1]\n", "        cwd = SERVICES / \"scheduler\"\n")],
    "取不到锁也执行（库上）": [(R, "                if not handle.acquired:\n                    return done(\"skipped_locked\")",
                         "                if False:\n                    return done(\"skipped_locked\")")],
    "探活总说锁还在": [(R, "return await asyncio.wait_for(conn.fetchval(\"SELECT 1\"), PROBE_TIMEOUT) == 1", "return True")],
    "指标重算只跑指定学生（不是 --all）": [(Y, "    args: [metrics, --all, --skip, persistence]",
                                     "    args: [metrics, --student, 00000000-0000-4000-8000-000000000000, --skip, persistence]")],
    "指标重算任务调起的是 sweep": [(Y, "    args: [metrics, --all, --skip, persistence]", "    args: [sweep]")],
    "定时作业去掉 --skip persistence（重算学习持续性）": [(Y, "    args: [metrics, --all, --skip, persistence]",
                                                "    args: [metrics, --all]")],
    "传给子进程的语句超时不生效": [(R, "    q.append((\"statement_timeout\", str(ms)))", "    q.append((\"statement_timeout_ms\", str(ms)))")],
}


def _copy(tmp: Path) -> Path:
    """tmp/repo：services/scheduler 复制一份（不带 .venv），仓库其余部分软链接回去"""
    root = tmp / "repo"
    (root / "services").mkdir(parents=True)
    for e in REPO.iterdir():
        if e.name not in ("services", ".git"):
            (root / e.name).symlink_to(e)
    for e in (REPO / "services").iterdir():
        if e.name != "scheduler":
            (root / "services" / e.name).symlink_to(e)
    dst = root / "services" / "scheduler"
    shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"))
    return dst


def _run(dst: Path, tests: list[str]) -> tuple[int, str]:
    env = {**os.environ, "SCHEDULER_TEST_STRICT": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run([str(PY), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=dst, env=env,
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return p.returncode, p.stdout[-4000:] + p.stderr[-2000:]


def one(name: str, edits: list[tuple[str, str, str]], tests: list[str]) -> str | None:
    with tempfile.TemporaryDirectory(prefix="scheduler-mut.") as t:
        dst = _copy(Path(t))
        for rel, orig, repl in edits:
            f = dst / rel
            # 变异目标必须在复制出来的 services/scheduler 里（不是软链接回主仓的 deploy/ 等）：否则会改到主仓文件
            if rel.startswith(("deploy/", "../")) or f.is_symlink() or not f.resolve().is_relative_to(dst.resolve()):
                return f"{name}：变异目标不在复制的目录里（{rel}）"
            src = f.read_text(encoding="utf-8")
            if src.count(orig) != 1:
                return f"{name}：变异本身写错（原文找不到或不唯一：{rel}）"
            f.write_text(src.replace(orig, repl), encoding="utf-8")
        rc, _ = _run(dst, tests)
    if rc == 1:
        return None
    return f"{name}：{'漏检' if rc == 0 else '超时（算漏检）' if rc == 124 else f'变异本身写错（pytest 退出码 {rc}）'}"


def group(label: str, mutations: dict[str, list[tuple[str, str, str]]], tests: list[str], workers: int) -> int:
    with tempfile.TemporaryDirectory(prefix="scheduler-mut.") as t:
        rc, out = _run(_copy(Path(t)), tests)
    if rc != 0:
        print(out)
        print(f"scheduler-mutation（{label}）：失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda kv: one(kv[0], kv[1], tests), mutations.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"scheduler-mutation（{label}）：失败，{len(missed)}/{len(mutations)} 个变异没有被发现")
        return 1
    print(f"scheduler-mutation（{label}）：通过（{len(mutations)} 个变异全部被发现）")
    return 0


def main() -> int:
    if not PY.exists():
        print("scheduler-mutation: 跳过（没有安装调度器依赖；见 services/scheduler/README.md）")
        return 3
    rc = group("纯函数与运行器", PURE, PURE_TESTS, 6)
    if rc:
        return rc
    if not (os.environ.get("PGBIN") or glob("/usr/lib/postgresql/*/bin/initdb")):
        print("scheduler-mutation（库上）：跳过（本机没有 Postgres 服务端）")
        return 3
    if not all((REPO / "services" / s / ".venv/bin/python").exists() for s in ("insight", "learning")):
        print("scheduler-mutation（库上）：跳过（没有洞察服务或学习服务的 venv：子进程解释器）")
        return 3
    return group("库上", DB, DB_TESTS, 3)


if __name__ == "__main__":
    sys.exit(main())
