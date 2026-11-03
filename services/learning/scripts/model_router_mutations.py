"""模型路由的变异测试（specs/model-router-sprint1/spec.md 范围第 4 条；LT-02 范围第 7 条补充）。

每个变异把 learning/model_router（以及 LT-02 的内部接口映射、冒烟脚本）的一段关键检查改坏，在临时目录里的仓库副本上跑
TESTS，断言测试能发现（失败）。有一个没被发现就退出码 1。

LT-02 起：副本按仓库目录结构摆放（services/learning 复制，docs、packages 等用符号链接，同 practice_mutations），
测试才能读到 docs/compliance/vendors.md 与契约文件；先跑不变异的对照组，对照组不通过就直接失败
（此前复制到扁平临时目录，读契约文件的用例在对照组里就会失败，变异“被发现”不可信）。并行 6 路，约 1–2 分钟。
"""
from __future__ import annotations

import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import practice_mutations as PM  # noqa: E402

TESTS = ["tests/test_model_router.py", "tests/test_model_router_staging.py", "tests/test_model_smoke.py", "tests/test_route_listener.py",
         "tests/test_obs.py",  # LT-05 模型路由指标（调用 / 失败 / 熔断 / 护栏剩余 / 估算成本）
         "tests/test_model_call_log.py"]  # 调用记录落库（specs/plt-model-call-log；真实库用例在 test_model_call_log_db.py，不进变异）
P = "learning/model_router/"
CF, RT, PR, BR, BG, LG, RG = (P + "config.py", P + "router.py", P + "providers.py", P + "breaker.py", P + "budget.py", P + "ledger.py",
                              P + "registry.py")
API, SMOKE, LS, SCF = "learning/route_api.py", "scripts/model_smoke.py", "learning/route_listener.py", "learning/config.py"

MUTATIONS: dict[str, tuple[str, str, str]] = {
    # LT-05 指标（learning/metrics.py 与熔断、护栏的只读快照；tests/test_obs.py）
    "指标：熔断快照把半开当关闭": (BR, "return {k: self.state(*k) for k in list(self._states)}",
                        'return {k: ("closed" if self.state(*k) == "half_open" else self.state(*k)) for k in list(self._states)}'),
    "指标：护栏剩余不扣已用 token": (BG, "max(0, b.daily_tokens - self._tokens.get(t, 0))", "b.daily_tokens"),
    "指标：供应商尝试不按结果计（失败看不见）": ("learning/metrics.py", "PROVIDER_CALLS.labels(provider=a.vendor, task=record.task, outcome=a.status).inc()",
                                 "PROVIDER_CALLS.labels(provider=a.vendor, task=record.task, outcome=\"ok\").inc()"),
    "指标：失败的尝试也计成本": ("learning/metrics.py", 'ok = [a for a in record.attempts if a.status == "ok"]', "ok = list(record.attempts)[:1]"),
    "运行时不过滤境外（出境第二道防线）": (RT, "return env in SYNTHETIC_ENVS and data_class is DataClass.L0", "return True"),
    "境外只接 L0 改成接到 L1": (RT, "and data_class is DataClass.L0", "and data_class.rank <= 1"),
    "staging 也允许境外": (CF, 'SYNTHETIC_ENVS = frozenset({"dev", "test"})', 'SYNTHETIC_ENVS = frozenset({"dev", "test", "staging"})'),
    "启动校验放过 pilot、prod 的境外供应商": (CF, '_need(residency is Residency.DOMESTIC, f"{where}: {env} 环境不能出现境外供应商（数据不出境）")', "pass"),
    "启动校验放过未批准的供应商": (CF, '_need(whitelist == "approved", f"{where}: {env} 环境只能用白名单里已批准的供应商")', "pass"),
    "可达 L2 的任务允许境外": (CF, '_need(vendors[t.vendor].residency is Residency.DOMESTIC, f"{where}: 可达 L2、L3 的任务只能配境内供应商")', "pass"),
    "允许 latest 别名": (CF, '_need(not _FLOATING.search(model) and not _FLOATING.search(api_name), f"{where}: 不能用 latest 之类会漂移的别名")', "pass"),
    "pilot 不要求带版本的 api_name": (CF, '_need(bool(_PINNED_API_NAME.search(api_name)), f"{where}: api_name 必须带日期或版本号（锁定快照）")', "pass"),
    "版本号可以是 YAML 数字": (CF, '_need(isinstance(raw.get(k), str), f"{where}: {k} 必须是带引号的字符串")', "pass"),
    "供应商 ID 不按契约校验": (CF, "_need(isinstance(vid, str) and bool(_VENDOR_ID.match(vid))", "_need(True or bool(_VENDOR_ID.match(vid))"),
    "不核对地址主机名": (PR, "if vendor.allowed_hosts and parts.hostname.lower() not in vendor.allowed_hosts:", "if False:"),
    "pilot 允许 http": (PR, 'if env not in SYNTHETIC_ENVS and parts.scheme != "https":', "if False:"),
    "手工配置绕过不变量": (RT, "        check_invariants(config)\n", ""),
    "主备可以同一厂商同一平台": (CF, "_need(not (a.org == b.org and a.platform == b.platform)", "_need(True or not (a.org == b.org and a.platform == b.platform)"),
    "熔断后不过滤数据级别（候选不经过滤）": (RT, "return tuple(t for t in route.targets if egress_allowed(self._cfg.vendors[t.vendor], req.data_class, self._cfg.env))",
                                  "return route.targets"),
    "熔断永不打开": (BR, "if s.failures >= self._threshold:", "if False:"),
    "半开放行多个试探": (BR, 'if st == "half_open" and s.probe is None:', 'if st == "half_open":'),
    "任何令牌都能释放试探名额": (BR, "return t.is_probe and self._get(t.vendor, t.task).probe is t", "return True"),
    "意外异常不兜底": (RT, "                except Exception:  # noqa: BLE001", "                except ZeroDivisionError:  # noqa: BLE001"),
    "不查 usage_id 与幂等键": (RT, "if not req.usage_id or not _KEY.match(req.usage_id) or not req.idempotency_key or not _KEY.match(req.idempotency_key):", "if False:"),
    "prompt 名不按契约校验": (RT, '_PROMPT = re.compile(r"^[a-z0-9][a-z0-9\\-]{0,47}$")', '_PROMPT = re.compile(r"^[a-z0-9][a-z0-9._/\\-]{0,63}$")'),
    "不查学科": (RT, "if req.subject is not None and req.subject not in SUBJECTS:", "if False:"),
    "重试换了幂等键": (RT, "idempotency_key=req.idempotency_key, timeout=timeout", "idempotency_key=f\"{req.idempotency_key}-{t0}\", timeout=timeout"),
    "client 错误也计入熔断": (RT, "if kind in BREAKER_FAILURES:", "if True:"),
    "不传总时限时没有上限": (RT, "deadline = started + (req.deadline_ms or DEFAULT_DEADLINE_MS) / 1000", "deadline = started + (req.deadline_ms or 10**9) / 1000"),
    "不看 Retry-After": (RT, "wait = retry_after if retry_after is not None else BACKOFF_BASE * (2 ** n)", "wait = BACKOFF_BASE * (2 ** n)"),
    "调用记录带上请求内容": (RT, "subject=req.subject if req.subject in SUBJECTS else None,", "subject=str(req.messages),"),
    "不一致不告诉调用方": (RT, "usage=reply.usage, model_mismatch=mismatch, reported_model=reported)", "usage=reply.usage)"),
    "model_version 写死": (RT, "return RouteResult(output=reply.output, model_version=target.model_version,", "return RouteResult(output=reply.output, model_version=\"routed\","),
    "响应体不限大小": (PR, "if len(buf) > MAX_RESPONSE_BYTES:", "if False:"),
    # ---- LT-02：staging 按 pilot 口径、占位拒绝 ----
    "staging 不按 pilot 口径校验": (CF, 'STRICT_ENVS = frozenset({"staging"}) | NO_EGRESS_ENVS', "STRICT_ENVS = NO_EGRESS_ENVS"),
    "占位值不拒绝": (CF, "return isinstance(value, str) and bool(_PLACEHOLDER.match(value))", "return False"),
    "staging 不要求 allowed_hosts": (CF, '_need(bool(hosts), f"{where}: {env} 环境必须登记 allowed_hosts（启动时核对实际地址）")', "pass"),
    "staging 不要求显式 api_name": (CF, '_need("api_name" in raw, f"{where}: {env} 环境必须显式写发给供应商的快照名 api_name")', "pass"),
    "手工配置的占位不核对": (CF, "_need(not is_placeholder(t.api_name) and not any(is_placeholder(h) for h in v.allowed_hosts),", "_need(True,"),
    "环境节收未知键": (CF, '_need(not extra, f"{env}: 未知键 {sorted(extra)}")', "pass"),
    # ---- LT-02：成本护栏 ----
    "超限也放行": (RT, "                if reservation is None:\n                    self.breaker.release(ticket)",
                "                if False:\n                    self.breaker.release(ticket)"),
    "失败调用报告的用量不计": (RT, "if e.usage is not None:", "if False:"),
    "内容不合格式时不带用量": (PR, 'raise ProviderError("bad_response", usage=usage if usage.input_tokens or usage.output_tokens else None)',
                    'raise ProviderError("bad_response")'),
    "staging 等不要求每个路由都有成本护栏": (CF, "_need(not missing,", "_need(True,"),
    "次数不计": (BG, "            self._calls[task] = self._calls.get(task, 0) + 1", "            pass"),
    "token 不计": (RT, "self.budget.settle(reservation, reply.usage.input_tokens + reply.usage.output_tokens)", "pass"),
    "按 UTC 自然日重置": (BG, "UTC8_OFFSET_SECONDS = 8 * 3600", "UTC8_OFFSET_SECONDS = 0"),
    "跨 0 点的结算算进新一天": (BG, "if reservation.day == self._day:", "if True:"),
    "次数上限差一（多放一次）": (BG, "                if self._calls.get(task, 0) >= limit.daily_calls or", "                if self._calls.get(task, 0) > limit.daily_calls or"),
    "token 上限不看": (BG, " or self._tokens.get(task, 0) >= limit.daily_tokens:", ":"),
    "预算可配给没有路由的任务": (CF, '_need(task in routes, f"{where}: 本环境没有这个任务的路由")', "pass"),
    "预算次数收布尔": (CF, "return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= upper", "return isinstance(value, int) and 1 <= value <= upper"),
    "超限映射成普通 503": (API, "    except BudgetExceeded as e:", "    except ZeroDivisionError as e:"),
    "超限标为可重试": (API, '"daily model call budget exhausted", retryable=False,', '"daily model call budget exhausted", retryable=True,'),
    "超限不带 retry-after": (API, 'headers={"retry-after": str(e.retry_after_seconds)}', "headers={}"),
    # ---- LT-02：成本估算 ----
    "没有单价也估算": (LG, "    if price is None:\n        return None", "    if price is None:\n        return 0.0"),
    "单价键不校验": (CF, '_need(isinstance(key, str) and key in known,', "_need(isinstance(key, str),"),
    # ---- LT-02：供应商登记联动 ----
    "登记状态不核对": (RG, "elif entry.status not in STATUS_FOR_WHITELIST[wl]:", "elif False:"),
    "未登记供应商不报": (RG, 'problems.append(f"{where}: 不在 docs/compliance/vendors.md 的登记表里")\n', "pass\n"),
    "停用照样放过": (RG, 'if entry.status == "停用":', "if False:"),
    "主机名不核对": (RG, "if entry.hosts_placeholder or set(entry.hosts) != {str(h).lower() for h in hosts}:", "if False:"),
    "只核对 staging": (RG, "for env in sorted(STRICT_ENVS):", 'for env in ["staging"]:'),
    "登记表重复 ID 不报": (RG, "if vid in out:", "if False:"),
    "测试专用供应商进 staging 不报": (RG, "elif wl not in STATUS_FOR_WHITELIST:", "elif False:"),
    "登记表不靠标记锚定": (RG, "    if len(marks) != 1:", "    if False:"),
    "不像主机名的片段不报": (RG, "    if bad:", "    if False:"),
    # ---- LT-02 决定记录（续）第 5 条：staging 回环路由监听 ----
    "回环监听放行所有路径": (LS, 'if kind == "http" and scope.get("path") == ROUTE_PATH and not scope.get("root_path"):', 'if kind == "http":'),
    "回环监听重复跑应用的启动与关闭": (LS, '        if kind == "lifespan":', "        if False:"),
    "回环监听不在回环地址": (LS, 'LOOPBACK = "127.0.0.1"', 'LOOPBACK = "0.0.0.0"'),
    "dev、test 也起回环监听": (SCF, "    if env != STAGING:\n        if raw:", "    if False:\n        if raw:"),
    "回环端口可与主监听相同": (SCF, 'if text == (main_raw or "8788"):', "if False:"),
    # ---- LT-02：真实模型冒烟 ----
    "冒烟收 pilot、prod": (SMOKE, 'SMOKE_ENVS = ("staging", "dev")', 'SMOKE_ENVS = ("staging", "dev", "pilot", "prod", "test")'),
    "冒烟不限次": (SMOKE, "if len(targets) > max_calls:", "if False:"),
    "冒烟打印回复原文": (SMOKE, "回复 {len(res.output)} 字", "回复 {res.output}"),
    "冒烟带重试": (SMOKE, "timeout_seconds=cfg.routes[task].timeout_seconds, max_retries=0)", "timeout_seconds=cfg.routes[task].timeout_seconds, max_retries=2)"),
    # ---- 调用记录落库（specs/plt-model-call-log 决定记录第 2 条；迁移 0060；tests/test_model_call_log.py）----
    "落库：TeeSink 不隔离异常": (LG, "            try:\n                s.write(record)\n            except Exception:  # noqa: BLE001\n",
                          "            s.write(record)\n            if False:\n"),
    "落库：队列不设上限": (LG, "if self._closing or len(self._queue) >= self.max_queue:", "if self._closing:"),
    "落库：写库失败向上抛": (LG, "        except Exception as e:  # noqa: BLE001 —— 连接断开", "        except ZeroDivisionError as e:  # noqa: BLE001 —— 连接断开"),
    "落库：写库失败计为已写入": (LG, "_bump(self._on_failed, len(batch))\n", "_bump(self._on_written, len(batch))\n"),
    "落库：失败日志带异常消息": (LG, '"error_class": type(e).__name__[:48]', '"error_class": str(e)'),
    "落库：摘要不核对格式（原值能入队）": (LG, 'if not _REF.match(record.usage_ref or "") or not _REF.match(record.idempotency_ref or ""):', "if False:"),
    "落库：尝试状态不核对": (LG, " or any(a.status not in _ATTEMPT_STATUSES for a in attempts)", ""),
    "落库：切备按尝试次数算": (LG, "len({a.model_version for a in attempts}) > 1", "len(attempts) > 1"),
    "落库：失败时取第一次尝试的目标": (LG, "(attempts[-1].model_version if attempts else None)", "(attempts[0].model_version if attempts else None)"),
    "落库：写入列多一列": (LG, '"idempotency_ref")\nINSERT_SQL', '"idempotency_ref", "reported_model")\nINSERT_SQL'),
    "落库：不分批": (LG, "range(min(self.batch_size, len(self._queue)))", "range(len(self._queue))"),
    "落库：写库不限时": (LG, "await asyncio.wait_for(self._write(batch), self.write_timeout)", "await self._write(batch)"),
    # 改回只限 executemany：池满或库连不上时取连接一直等（审查建议）
    "落库：取连接不限时（只限写入）": (LG, "await asyncio.wait_for(self._write(batch), self.write_timeout)",
                           "async with self._pool.acquire() as conn:\n                async with conn.transaction():\n"
                           "                    await asyncio.wait_for(conn.executemany(INSERT_SQL, batch), self.write_timeout)"),
    # 路由层模板白名单（审查建议）：不在提示词登记表里的模板也照记进调用记录
    "落库：模板不核对白名单": (RT, "if SOURCE_VERSION.match(source_version) and req.prompt_template in self._recorded_templates",
                      "if SOURCE_VERSION.match(source_version)"),
    "落库：模板白名单多了未登记的名字": (RT, '"wenli-essay-grade", "zhixing-m3-grade"})', '"wenli-essay-grade", "zhixing-m3-grade", "tutor-aya-hint"})'),
    "落库：自建调用记录池不核对 TLS": ("learning/app.py", "    if pool is None or (settings.model_call_log and call_log_pool is None):",
                            "    if pool is None:"),
    "落库：退出时不叫醒刷写": (LG, "            self._wake.set()\n            try:", "            self._wake.clear()\n            try:"),
    "落库：退出超时正在写的那批不计丢弃": (LG, "_bump(self._on_dropped, len(self._queue) + self._inflight)", "_bump(self._on_dropped, len(self._queue))"),
    "落库：计数钩子出错外泄": (LG, "    try:\n        hook(n)\n    except Exception:  # noqa: BLE001 —— 计数出错只丢这一次计数\n        pass", "    hook(n)"),
    "落库：写库失败计数接到丢弃": ("learning/metrics.py", "on_failed=_inc(CALL_LOG_FAILED)", "on_failed=_inc(CALL_LOG_DROPPED)"),
    "落库：路由落地不带数据库": ("learning/metrics.py", "*([db] if db is not None else [])", ""),
    "落库：开关收任意值": (SCF, 'if call_log_raw not in ("", "0", "1"):', "if False:"),
    "落库：开关打开也不核对 0060": ("learning/app.py", "        if settings.model_call_log:\n            try:\n                await check_call_log(p)",
                           "        if False:\n            try:\n                await check_call_log(p)"),
    "落库：启动不核对 INSERT 权限": ("learning/app.py", "if not await pool.fetchval(CALL_LOG_GRANT_SQL, list(MODEL_CALL_COLUMNS)):", "if False:"),
}


def one(name: str, fname: str, pattern: str, replacement: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="model-router-mut.") as t:
        dst = PM._copy(Path(t))  # noqa: SLF001
        f = dst / fname
        text = f.read_text(encoding="utf-8")
        if pattern not in text:
            return f"变异本身写错（找不到原文）：{name}"
        f.write_text(text.replace(pattern, replacement, 1), encoding="utf-8")
        rc, _ = PM._run(dst, TESTS)  # noqa: SLF001
        # 只有“测试失败”（退出码 1）算发现；收集错误、导入错误（2、4 等）说明变异把代码改坏了，不算（安全审查建议 13）
        if rc != 1:
            return f"{'漏检' if rc == 0 else f'不是被断言发现（退出码 {rc}）'}：{name}"
    return None


def main() -> int:
    if not (PM.HERE / ".venv/bin/python").exists():
        print("model-router-mutation: 跳过（没有安装学习服务依赖）")
        return 3
    with tempfile.TemporaryDirectory(prefix="model-router-mut.") as t:
        rc, out = PM._run(PM._copy(Path(t)), TESTS)  # noqa: SLF001
    if rc != 0:
        print(out)
        print(f"model-router-mutation: 失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda kv: one(kv[0], *kv[1]), MUTATIONS.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"model-router-mutation: 失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"model-router-mutation: 通过（对照组通过；{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
