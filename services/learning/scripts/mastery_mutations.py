"""掌握 v0 纯函数部分（KGE-04 第一步，specs/mastery-estimate-sprint4/spec.md 第 17 节）的变异测试。

把配置校验、证据分类、门槛状态机、衰减、写入判定、一致性检查的关键判定逐条改坏，断言 tests/test_mastery_*.py 能发现。
做法同 scripts/diagnosis_mutations.py：每个变异在临时目录里改一处（或几处）源码再跑测试；只有“测试失败”（退出码 1）算发现，
收集错误、导入错误不算。临时目录保持仓库结构（services/learning 的 learning、config、掌握测试复制，docs 软链接回仓库，
一致性检查要用 docs/design/sie 的禁用推断词表）。先跑一次不变异的对照组，必须通过。不连库、不联网，约 30 秒。
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
CFG, EV, G, BKT, W, CON = ("learning/mastery/config.py", "learning/mastery/evidence.py", "learning/mastery/gates.py",
                           "learning/mastery/bkt.py", "learning/mastery/write.py", "learning/mastery/consistency.py")

# 名字 → [(文件, 原文, 替换为), …]
MUTATIONS: dict[str, list[tuple[str, str, str]]] = {
    # 刷同题（开发计划验收）
    "刷同题计多次（同族重做也计入）": [(G, "if ev.family in consumed:", "if False:")],
    "题目族判定失效（按单题计）": [(EV, 'return self.family_id or f"item:{self.item_id}"', 'return f"item:{self.item_id}"')],
    "重复提交不去重": [(G, "if ev.evidence_id in seen_ids or (ev.attempt_id is not None and ev.attempt_id in seen_attempts):",
                  "if False:")],
    "看过高级提示或猜过的族可以再计": [(EV, "return _excluded(GUESSED, consumes=True)", "return _excluded(GUESSED)"),
                         (EV, "return _excluded(HINT_HEAVY, consumes=True, hint_followup=True)",
                          "return _excluded(HINT_HEAVY, hint_followup=True)")],
    # 证据等级、准入、待复核
    "B 级推到迁移（严格判定只看计入）": [(EV, 'return self.counted and not self.caps and self.level == "A"', "return self.counted")],
    "未准入不按 B 级": [(EV, 'if ev.admission != "admitted":', "if False:")],
    "待复核推动门槛": [(EV, "if ev.human_review_required:", "if False:")],
    "C 级计入": [(EV, 'if ev.evidence_level == "C":', "if False:")],
    "旧证据不封顶": [(EV, "if legacy:\n        caps.append(LEGACY_NO_KIND)", "if False:\n        caps.append(LEGACY_NO_KIND)")],
    "低置信判分计入": [(EV, 'ev.grade_confidence < cfg["grade_confidence_min"]', "False")],
    "次要节点计入": [(EV, "if ev.main_node_id != unit.node_id:",
                 "if ev.main_node_id != unit.node_id and unit.node_id not in ev.secondary_node_ids:")],
    "只计有判分与作答引用的证据（#23）失效": [(EV, "if not ev.has_grade or not ev.has_response_ref:", "if not ev.has_grade:")],
    # 提示与自评
    "去掉提示等级判定（高级提示当低级）": [(EV, 'low_hint = hint is not None and 1 <= hint <= cfg["low_hint_max_level"]', "low_hint = True")],
    "没有提示等级按无提示（#10 失效）": [(EV, 'if hint is None and ev.source == "diagnose":', "if hint is None:")],
    "猜的做对计入": [(EV, 'if correct and ev.self_report == "guessed":', "if False:")],
    "提示后做错降低置信度之外还加质量": [(G, 'if weight_key != "hinted_wrong":', "if True:")],
    # 门槛与签字
    "未签字配置可以点亮": [(G, 'if gate == "stable" and self.cfg.signed and self.transfer_ok(strict=True):',
                    'if gate == "stable" and self.transfer_ok(strict=True):')],
    "迁移不重查严格证据": [(G, 'if gate == "stable" and self.cfg.signed and self.transfer_ok(strict=True):',
                    'if gate == "stable" and self.cfg.signed and self.transfer_ok(strict=False):')],
    "稳定不要求当日复测通过": [(G, 'if (gate == "execute" and self.passed["rung0"] is not None and self.stable_counts_ok(self.all)',
                     'if (gate == "execute" and self.stable_counts_ok(self.all)')],
    "稳定不看题型组": [(G, 'and len(c.groups) >= self.g["stable"]["item_type_groups_min"])', ")")],
    "BKT 下限不挡晋级": [(G, 'and self.p >= g["execute"]["p_floor"]:', ":")],
    # 复测
    "复测到期前也算": [(G, "if t < pend.due_at:", "if False:")],
    "间隔按客户端时间算": [(G, "        t = ev.recorded_at\n        if t < pend.due_at:",
                    "        t = ev.occurred_at or ev.recorded_at\n        if t < pend.due_at:")],
    "复测失败后不重排（换题再试就过）": [(G, "            self._schedule(pend.rung, t)", "            self.pending = pend")],
    "退级失效（第三轮失败仍是迁移）": [(G, '            self._move("stable", ev, "retest_failed")', "            pass")],
    "已亮的星做错不提前复测（#13）": [(G, 'self._schedule("rung2", t, "early")', "pass")],
    "到达保持后仍排复测（#14）": [(G, "self.pending = None  # 到达保持后不再排复测（#14）", 'self._schedule("rung2", t)')],
    "高级提示不排无提示变式": [(G, 'tasks.append(Task("hint_followup", None, self.hint_followup_at))', "pass")],
    "B 级不排平台内验证": [(G, 'tasks.append(Task("verify_in_platform", None, self.last_counted_at))', "pass")],
    # 衰减
    "衰减改掌握": [(G, "result = UnitResult(self.unit.node_id, self.unit.cognitive_level, self.gate, self.p, conf,",
               "result = UnitResult(self.unit.node_id, self.unit.cognitive_level, self.gate, "
               "self.p * (conf / conf_raw if conf_raw else 1.0), conf,")],
    "衰减不降置信度": [(BKT, "return confidence * 0.5 ** (idle / d[\"half_life_seconds\"])", "return confidence")],
    "做错也加学习转移（方向可能反）": [(BKT, "        target = post\n", "        target = post + (1 - post) * learn\n")],
    # 写入
    "每次重算都写": [(W, "return bool(why), tuple(why)", "return True, tuple(why)")],
    "写库前自检放过 B 级": [(W, 'f.evidence_level != "A" or ', "")],
    "已亮记录引用非严格证据": [(G, "ids = tuple(eid for eid, c in self.counted if c.strict)", "ids = tuple(eid for eid, _ in self.counted)")],
    # 配置校验
    "未签字配置可在非 test 环境加载": [(CFG, '_need(cfg.signed, "未签字的掌握配置只允许', 'pass  # _need(cfg.signed, "未签字的掌握配置只允许')],
    "合成配置可在非 test 环境加载": [(CFG, '_need(not d["synthetic"], ', 'pass  # _need(not d["synthetic"], ')],
    "方向校验放过（独立 > 低级提示 > 高级提示）": [(CFG, '_need(w["independent"] > w["hinted_low"] > w["hinted_high"]',
                                     '_need(True or w["independent"] > w["hinted_low"] > w["hinted_high"]')],
    "B 级权重可不低于 A 级": [(CFG, '_need(0 < d["level_weight_B"] < 1,', '_need(0 < d["level_weight_B"] <= 1,')],
    "门槛单调校验放过": [(CFG, "_need(floors == sorted(floors),", "_need(True,")],
    "复测间隔递增校验放过": [(CFG, 'r["rung0_min_gap_seconds"] < r["rung1_interval_seconds"] < r["rung2_interval_seconds"]', "True")],
    "一族题可到执行（families_min 下限放过）": [(CFG, '_need(g["execute"]["families_min"] >= 2,', "_need(True,")],
    "星亮看最低层次": [(G, 'level = rule["node_levels"].get(node_id) or levels[-1]', 'level = rule["node_levels"].get(node_id) or levels[0]')],
    # 一致性检查（开发计划验收：拦截程度升级）
    "一致性检查放过程度升级": [(CON, "elif cfg.degree_words[w] > group_degree:", "elif False:")],
    "一致性检查放过新增结论": [(CON, "            if w not in allowed:", "            if False:")],
    "一致性检查放过数字": [(CON, "if _DIGITS.search(conn):", "if False:")],
    "一致性检查放过禁用推断": [(CON, "    if hits:\n", "    if False:\n")],
    "一致性检查放过人设语言": [(CON, "if term in conn:", "if False:")],
    "一致性检查放过无引用陈述": [(CON, 'out.append(Violation("no_claim", sec.key, (), ""))', "pass")],
    "待验证判断可作陈述": [(CON, 'VISIBLE_STATUSES = ("established", "hypothesis")', 'VISIBLE_STATUSES = ("established", "hypothesis", "to_verify")')],
    "程度词不遮掉（尚不稳定误判为结论）": [(CON, 'text = text.replace(w, "\\u0000" * len(w))', "pass")],
    "阻断后仍发布衔接语": [(CON, 'Section(s.key, "", tuple(', "Section(s.key, s.connective, tuple(")],
}


def _copy(tmp: Path) -> Path:
    root = tmp / "repo"
    dst = root / "services" / "learning"
    dst.mkdir(parents=True)
    shutil.copytree(HERE / "learning", dst / "learning", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(HERE / "config", dst / "config")
    (dst / "tests").mkdir()
    for t in sorted((HERE / "tests").glob("test_mastery_*.py")):
        shutil.copy(t, dst / "tests" / t.name)
    (dst / "pytest.ini").write_text("[pytest]\ntestpaths = tests\npythonpath = . tests\naddopts = -p no:cacheprovider\n",
                                    encoding="utf-8")
    (root / "docs").symlink_to(REPO / "docs")
    return dst


def _run(dst: Path) -> tuple[int, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "HOME": os.environ.get("HOME", "/tmp")}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests"],
                       cwd=dst, capture_output=True, text=True, env=env, timeout=600)
    return r.returncode, r.stdout[-2000:]


def one(name: str, edits: list[tuple[str, str, str]]) -> str | None:
    with tempfile.TemporaryDirectory(prefix="mastery-mut.") as t:
        dst = _copy(Path(t))
        for fname, pattern, replacement in edits:
            f = dst / fname
            text = f.read_text(encoding="utf-8")
            if pattern not in text:
                return f"变异本身写错（找不到原文）：{name}"
            f.write_text(text.replace(pattern, replacement, 1), encoding="utf-8")
        rc, _ = _run(dst)
        if rc != 1:
            return f"{'漏检' if rc == 0 else f'不是被断言发现（退出码 {rc}）'}：{name}"
    return None


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mastery-mut.") as t:
        rc, out = _run(_copy(Path(t)))
    if rc != 0:
        print(out)
        print(f"mastery-mutation: 失败（对照组没有通过，rc={rc}）")
        return 1
    with ThreadPoolExecutor(max_workers=max(2, min(8, os.cpu_count() or 2))) as pool:
        results = list(pool.map(lambda kv: one(*kv), MUTATIONS.items()))
    missed = [r for r in results if r]
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"mastery-mutation: 失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"mastery-mutation: 通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
