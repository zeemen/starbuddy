"""讲评课验收清单检查器的变异测试（make lesson-acceptance-check 的一部分）：把 check.py 的关键规则逐条改坏，断言 tests/ 能发现。
做法同其他变异脚本：把本目录复制到临时目录（checklist.yaml 的 refs 指向仓库文件，所以 ROOT 改指真仓库），改一处，跑单测；
只有“测试失败”（退出码 1）算发现。先跑一次不变异的对照组，必须通过。不联网，约 10 秒。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

MUTATIONS: list[tuple[str, str, str]] = [
    ("未签字也能得出通过", "    if not is_signed(cl):\n        return \"trial\"", "    if False:\n        return \"trial\""),
    ("必过项 blocked 也算通过", 'if it.get("must") is True and outcome != "pass":', 'if it.get("must") is True and outcome == "fail":'),
    ("证据情境不一致仍可记 pass", 'elif actual != expect and outcome == "pass":', "elif False:"),
    ("有期望的条目可以不记实际情境", "            if actual not in EVIDENCE_KINDS:\n                rep.add(where, f\"这一条要记",
     "            if False:\n                rep.add(where, f\"这一条要记"),
    ("fail / blocked 不要复现", 'if outcome in ("fail", "blocked") and not _text(e.get("repro")):', "if False:"),
    ("n_a 不要理由", 'if outcome == "n_a" and not _text(e.get("n_a_reason")):', "if False:"),
    ("漏条目不报", "    for iid in sorted(want - set(entries)):", "    for iid in []:"),
    ("多条目不报", "    for iid in sorted(set(entries) - want):", "    for iid in []:"),
    ("不查合成数据声明", 'if rec.get("synthetic_inputs") is not True:', "if False:"),
    ("可以填生产环境", 'ENVIRONMENTS = ("staging", "local")', 'ENVIRONMENTS = ("staging", "local", "production")'),
    ("不核对清单指纹", 'or rec.get("checklist_sha256") != digest', ""),
    ("不扫手机号", '"手机号": re.compile(r"(?<!\\d)1[3-9]\\d{9}(?!\\d)"),', '"手机号": re.compile(r"(?!x)x"),'),
    ("记录不扫真实数据", '    _pii("记录", rec, rep)\n', "\n"),
    ("附件不校验", "if att is not None and not (isinstance(att, list)", "if False and not (isinstance(att, list)"),
    ("附件可以写绝对路径", "(?!/)(?!.*", "(?!.*"),
    ("refs 不核对存在", "or not (root / r).exists()", ""),
    ("清单 id 可重复", '            rep.add(where, "id 重复")', "            pass"),
    ("证据情境可以乱写", 'if "evidence_kind_expect" in it and it["evidence_kind_expect"] not in EVIDENCE_KINDS:', "if False:"),
    ("记录可以带额外字段（如分数）", "    if extra:\n        rep.add(\"记录\", f\"多余字段", "    if False:\n        rep.add(\"记录\", f\"多余字段"),
]


def _copy(tmp: Path) -> Path:
    dst = tmp / "lesson-acceptance"
    shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "mutations.py"))
    src = (dst / "check.py").read_text(encoding="utf-8")
    anchor = "ROOT = HERE.parents[1]"
    assert src.count(anchor) == 1
    (dst / "check.py").write_text(src.replace(anchor, f"ROOT = Path({str(REPO)!r})"), encoding="utf-8")
    return dst


def _run(dst: Path) -> int:
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=dst, capture_output=True, text=True,
                       timeout=120, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    return r.returncode


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lesson-acc-mut.") as t:
        if _run(_copy(Path(t))) != 0:
            print("lesson-acceptance-mutation：对照组没有通过")
            return 1
    missed: list[str] = []
    for name, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="lesson-acc-mut.") as t:
            dst = _copy(Path(t))
            f = dst / "check.py"
            src = f.read_text(encoding="utf-8")
            if src.count(old) != 1:
                missed.append(f"变异本身写错（原文找不到或不唯一）：{name}")
                continue
            f.write_text(src.replace(old, new), encoding="utf-8")
            rc = _run(dst)
            if rc != 1:
                missed.append(f"{'漏检' if rc == 0 else f'不是被断言发现（退出码 {rc}）'}：{name}")
    for m in missed:
        print(f"  {m}")
    if missed:
        print(f"lesson-acceptance-mutation：失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"lesson-acceptance-mutation：通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
