"""PLT-17 成本仪表盘（specs/cost-dashboard-plt17）的变异测试：把人数扰动与门槛余量、插值与粗化、P90 尾部人数、每人一个点、分摊、
未归因、按任务的门槛与取整、标注、写出、输入守卫、出口检查与合成队列逐条改坏，断言 tests/test_dashboard.py 能发现。

做法同 tools/kge-calibrate/mutations.py：每个变异在临时目录里复制一份 tools/loadtest、改一处源码再跑测试；只有“测试失败”（退出码 1）
算发现。先跑一次不变异的对照组，必须通过。跑变异时不选“与仓库样例逐字节一致”这一条，逼手算、守卫与差分测试自己发现问题。
副本里的 db/ 是指回本仓库的符号链接（cost.py 只读解析迁移 0015 的额度）；kge-calibrate 的守卫正则经 LOADTEST_REPO 指回本仓库只读核对。
用学习服务的 venv 运行（make cost-dashboard-mutation）；缺 pytest 或 PyYAML 时退出码 3（Makefile 决定非严格跳过、严格失败）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DB, SY, CO = "loadtest/dashboard.py", "loadtest/dashboard_synth.py", "loadtest/cost.py"

MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    # 防回推：人数扰动、门槛余量、取整（安全审查 B1）
    "去掉人数扰动": [(DB, "n = len(ms) + noise(set_key(ms))", "n = len(ms)")],
    "门槛不留扰动余量": [(DB, 'if n < dcfg["min_group"] + NOISE_MAX:', 'if n < dcfg["min_group"]:')],
    "门槛比较改为严格大于": [(DB, 'if n < dcfg["min_group"] + NOISE_MAX:', 'if n <= dcfg["min_group"] + NOISE_MAX:')],
    "扰动幅度超过 2": [(DB, "NOISE_TABLE = ((-2, 0.1), (-1, 0.3), (0, 0.7), (1, 0.9), (2, 1.0))",
                        "NOISE_TABLE = ((-3, 0.1), (-1, 0.3), (0, 0.7), (1, 0.9), (3, 1.0))")],
    "表键与成员无关": [(DB, "return sum(record_key(p) for p in set(pids)) % KEY_MOD", "return 0")],
    "表键不按成员集合去重": [(DB, "return sum(record_key(p) for p in set(pids)) % KEY_MOD", "return sum(record_key(p) for p in pids) % KEY_MOD")],
    "人数不取整": [(DB, "return (n // step) * step", "return n")],
    "样本不足也给数": [(DB, '    if n is None:\n        return {"status": INSUFFICIENT}\n    vals', "    if n is None:\n        n = 0\n    vals")],
    "最小组人数下限放宽": [(DB, "MIN_GROUP_FLOOR = 20", "MIN_GROUP_FLOOR = 1")],
    "取整粒度下限放宽": [(DB, "ROUND_TO_FLOOR = 10", "ROUND_TO_FLOOR = 1")],
    "配置多余键放行": [(DB, '_need(set(doc) == {"min_group", "round_to", "unattributed"},', "_need(True,")],
    "每人多个点（不取月均）": [(DB, "k = len(months[pid])", "k = 1")],
    # 插值、粗化、尾部人数
    "插值改最近秩": [(DB, "return v[lo] + (h - lo) * (v[hi] - v[lo])", "return v[math.ceil(h)]")],
    "P90 写成 P95": [(DB, "p50, p90 = interp_quantile(vals, 0.5), interp_quantile(vals, 0.9)",
                     "p50, p90 = interp_quantile(vals, 0.5), interp_quantile(vals, 0.95)")],
    "P50 不粗化": [(DB, '"p50": coarsen(p50, step),', '"p50": p50,')],
    "粗化用银行家舍入": [(DB, "return round(math.floor(x / step + 0.5) * step, 6)", "return round(round(x / step) * step, 6)")],
    "P90 尾部门槛放宽": [(DB, "if tail >= MIN_TAIL else INSUFFICIENT", "if tail >= 1 else INSUFFICIENT")],
    "消耗档步长写成 1": [(DB, "return max(1.0, quota * STEP_QUOTA_SHARE)", "return 1.0")],
    "成本粒度改 0.01": [(DB, "STEP_COST = 0.1\n", "STEP_COST = 0.01\n")],
    "共用单次成本输出用输入价": [(CO, 'tokens.output * token_mul * price["output"]', 'tokens.output * token_mul * price["input"]')],
    # 成本与未归因
    "未归因 token 不用全量记录": [(DB, 'pooled = token_stats([{**r, "subject": UNATTR} for r in records], fallback)',
                                   "pooled = token_stats([], fallback)")],
    "未归因不计成本": [(DB, "costs = {s: cost_of(s, use[s]) for s in use}", "costs = {s: cost_of(s, use[s]) for s in use if s != UNATTR}")],
    "未归因占比分母错": [(DB, 'g["unattributed_share"] = _ratio(un / tot, STEP_UTIL) if tot > 0 else None',
                          'g["unattributed_share"] = _ratio(un, STEP_UTIL) if tot > 0 else None')],
    "使用率不除额度": [(DB, "([(p, v / quota) for p, v in vals], STEP_UTIL)", "([(p, v) for p, v in vals], STEP_UTIL)")],
    "全部行漏掉未归因": [(DB, "sum(u[x].get(res, 0.0) for x in u) if s == ALL", "sum(u[x].get(res, 0.0) for x in u if x != UNATTR) if s == ALL")],
    # 学科分摊（D5）
    "按调用次数用了 token": [(DB, "            calls[s] += 1\n", '            calls[s] += int(r["input_tokens"]) + int(r["output_tokens"])\n')],
    "分摊含失败调用": [(DB, 'if r.get("outcome") == "ok" and s in calls:', "if s in calls:")],
    "分摊不在在读科目间归一": [(DB, "(un * w[s] / tot if tot > 0 else 0.0)", "(un * w[s] if tot > 0 else 0.0)")],
    "未能分摊的成本不记": [(DB, "                unalloc[m] += un\n", "                pass\n")],
    # 按任务：门槛、取整、失败、多次尝试、回退、平均成本
    "调用数门槛放宽": [(DB, "if len(rs) < MIN_CALLS:", "if len(rs) < 1:")],
    "调用数不取整": [(DB, '"calls": floor_to(len(rs), ROUND_TO_FLOOR)', '"calls": len(rs)')],
    "多次尝试按 attempts ≥ 1": [(DB, 'r["attempts"] > 1)', 'r["attempts"] >= 1)')],
    "回退记录缺失时写 0": [(DB, "if with_fb else None}", "if with_fb else 0.0}")],
    "每次调用平均成本只除成功数": [(DB, 'row["cost_per_call"] = round(spent / len(rs), 6)',
                                    'row["cost_per_call"] = round(spent / sum(1 for r in rs if r["outcome"] == "ok"), 6)')],
    # 标注
    "签字价不查版本号": [(DB, 'if prices.get("example") is False and isinstance(ver, str) and SIGNOFF.fullmatch(ver):',
                          'if prices.get("example") is False:')],
    "合成标注写死": [(DB, 'data = "合成，不是试点结论" if synthetic else "试点数据，须经人核对后才可对外"', 'data = "合成，不是试点结论"')],
    "首段不标注": [(DB, "f\"> **{r['banner']}**。{source}；{price_note}。\"", 'f"> {source}；{price_note}。"')],
    "首段不写档位口径": [(DB, '"档位为导出时档位，期内升降档未拆分；', '"')],
    # 写出与出口
    "出口检查失效": [(DB, "if PSEUDONYM_ANYWHERE.search(text):", "if False:")],
    "目录非空也写": [(DB, "if any(out_dir.iterdir()):", "if False:")],
    "文件权限放宽": [(DB, "FILE_MODE = 0o600", "FILE_MODE = 0o644")],
    "输出目录可以是符号链接": [(DB, "if out_dir.is_symlink():", "if False:")],
    "非合成不强制 --out": [(DB, "if not inputs[0].synthetic and out is None:", "if False:")],
    # 输入守卫（同 kge-calibrate）
    "接受非合成数据": [(DB, '_need(synthetic, "本步只接受', '_need(True, "本步只接受')],
    "合成样例收 pil- 伪名": [(DB, "_need(not synthetic or pid.startswith(SYNTHETIC_PREFIX),", "_need(True,")],
    "不拦个人信息列名": [(DB, "_need(not FORBIDDEN_COLUMN.search(col),", "_need(True,")],
    "不查单元格个人信息": [(DB, "return any(r.search(t) for r in PERSONAL)", "return False")],
    "单元格不拒汉字": [(DB, ',\n            re.compile(r"[㐀-鿿]"))', ")")],
    "伪名放宽": [(DB, 'PSEUDONYM = re.compile(r"^(syn|pil)-[a-z]{10,24}$")', 'PSEUDONYM = re.compile(r"^[a-z][a-z0-9-]{5,63}$")')],
    "不核对额度配置": [(DB, '_need(meta["quota_config"] == quota_config,', "_need(True,")],
    "档位不一致放行": [(DB, "_need(tiers.setdefault(pid, tier) == tier,", "_need(True,")],
    "重复行放行": [(DB, "_need((pid, month, subject, res) not in seen,", "_need(True,")],
    "数量收小数": [(DB, 'QUANTITY = re.compile(r"^\\d{1,15}$")', 'QUANTITY = re.compile(r"^\\d+(\\.\\d+)?$")')],
    "读文件不查普通文件": [(DB, "_need(stat.S_ISREG(st.st_mode),", "_need(True,")],
    "调用记录放行模型标识": [(DB, "_need(not MODEL_KEY.search(k),", "_need(True,")],
    "调用记录放行未知字段": [(DB, "            raise InputRefused(f\"调用记录第 {i} 行有不认识的字段（只收 {'、'.join(RECORD_KEYS)}）\")",
                                "            continue")],
    "fallback 不校验": [(DB, '_need("fallback" not in r or isinstance(r["fallback"], bool),', "_need(True,")],
    "token 收布尔": [(DB, "return isinstance(v, int) and not isinstance(v, bool) and", "return isinstance(v, int) and")],
    # 合成队列
    "合成 Max 档不少于 20 人": [(SY, '"max": 14}', '"max": 25}')],
    "合成人数恰在取整边界": [(SY, '{"basic": 87,', '{"basic": 90,')],
    "合成没有未归因份额": [(SY, '            rows += [(pid, MONTH, tier, "unattributed", r, v) for r, v in un.items() if v > 0]\n', "            pass\n")],
    "合成没有回退": [(SY, "P_UNAVAILABLE, P_REJECTED, P_RETRY, P_FALLBACK = 0.03, 0.01, 0.06, 0.04",
                      "P_UNAVAILABLE, P_REJECTED, P_RETRY, P_FALLBACK = 0.03, 0.01, 0.06, 0.0")],
    "合成不标 synthetic": [(SY, "synthetic=true quota_config={QUOTA_CONFIG}", "synthetic=false quota_config={QUOTA_CONFIG}")],
    "合成种子不固定": [(SY, "rng = random.Random(seed)\n", "rng = random.Random()\n")],
}


def _copy(dst: Path) -> Path:
    tool = dst / "tools" / "loadtest"
    shutil.copytree(HERE, tool, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    os.symlink(REPO / "db", dst / "db")
    return tool


def _run(tool: Path) -> int:
    env = {**os.environ, "LOADTEST_REPO": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--rootdir", str(tool),
           "-k", "not matches_committed_sample", str(tool / "tests" / "test_dashboard.py")]
    return subprocess.run(cmd, cwd=tool, env=env, capture_output=True, text=True).returncode


def _mutate(name: str, edits: list[tuple[str, str, str]], base: Path) -> tuple[str, str]:
    work = Path(tempfile.mkdtemp(prefix="m.", dir=base))
    tool = _copy(work)
    for rel, old, new in edits:
        p = tool / rel
        src = p.read_text(encoding="utf-8")
        if src.count(old) != 1:
            return name, "anchor"
        p.write_text(src.replace(old, new), encoding="utf-8")
    rc = _run(tool)
    return name, "caught" if rc == 1 else "survived" if rc == 0 else f"error({rc})"


def main() -> int:
    try:
        import pytest  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        print("cost-dashboard-mutation: 跳过（当前解释器没有 pytest 或 PyYAML；用学习服务的 venv）")
        return 3
    with tempfile.TemporaryDirectory(prefix="cost-dash-mut.") as t:
        base = Path(t)
        (base / "control").mkdir()
        control = _run(_copy(base / "control"))
        if control != 0:
            print(f"cost-dashboard-mutation: 对照组（不变异）没有通过（退出码 {control}）")
            return 1
        workers = min(8, os.cpu_count() or 2)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda kv: _mutate(kv[0], kv[1], base), MUTATIONS.items()))
    bad = [(n, s) for n, s in results if s != "caught"]
    for n, s in results:
        print(f"  {'发现' if s == 'caught' else s}：{n}")
    print(f"cost-dashboard-mutation: {len(results) - len(bad)}/{len(results)} 个变异被测试发现")
    if bad:
        print("cost-dashboard-mutation: 未被发现或锚点失效：" + "；".join(f"{n}（{s}）" for n, s in bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
