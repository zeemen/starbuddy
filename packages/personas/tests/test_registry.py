"""登记表、指纹锁与 persona-registry-check（spec 设计要点 1、验收第 1、2 条）。

未登记 Prompt 的扫描测试会在临时目录里写含“你是……”的长字符串：这些字符串在测试代码里拆开拼接，
免得本文件（packages/ 在扫描范围内）自己被判成未登记的 Prompt。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from conftest import PKG_PARENT, ROOT
from personas.registry import (check, extract_symbol, iter_prompts, load_registry, prompt_fingerprint, scan_unregistered, sha256,
                               write_lock)

FIX = "evals/persona/fixtures/governance/registry.yaml"
YOU_ARE = "你" + "是"          # 拆开写，见文件头
SYS = "sys" + "tem"
LONG = "一位合成的测试助手，只用来验证未登记 Prompt 的启发式扫描能发现问题，不是任何角色的设定。"


def test_repo_registry_passes():
    res = check(ROOT)
    assert res["ok"], res["errors"]
    assert res["prompts"] == ["kge-compile-kp", "wenli-essay-grade", "zhixing-m3-grade"]
    reg = load_registry(ROOT)
    ids = [p["persona_id"] for p in reg["personas"]]
    assert ids == ["diandian", "aya", "miaobi", "fangyuan", "chunqiu", "athena", "aji", "xboshi"]
    assert all(not p["prompts"] for p in reg["personas"]), "仓库里还没有任何真实人设 Prompt（本冲刺不写）"
    assert {e.owner_type for e in iter_prompts(reg)} == {"task"}


def test_symbol_fingerprints_follow_code():
    """task 类 Prompt 是代码常量：指纹只看登记的符号，改了符号外的代码不影响；版本常量与登记一致"""
    reg = load_registry(ROOT)
    zx = next(e for e in iter_prompts(reg) if e.prompt_id == "zhixing-m3-grade")
    text = extract_symbol(ROOT / zx.path, "buildM3GradeMessages", zx.path)
    assert text.startswith("export function buildM3GradeMessages(") and text.rstrip().endswith("}")
    assert "======== 学生作答" in text
    assert extract_symbol(ROOT / zx.path, "OUTPUT_FORMAT", zx.path).endswith("`;")
    kge = next(e for e in iter_prompts(reg) if e.prompt_id == "kge-compile-kp")
    assert extract_symbol(ROOT / kge.path, "SYSTEM_PROMPT", kge.path).startswith("SYSTEM_PROMPT = (")
    assert prompt_fingerprint(zx, ROOT).startswith("sha256:")


def test_normalization_ignores_line_endings_and_trailing_spaces():
    assert sha256("a  \r\nb\t\n\n") == sha256("a\nb")
    assert sha256("a\nb") != sha256("a\nc")


# ---------- 在临时副本上改动（合成夹具）----------
def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(PKG_PARENT / "personas", root / "packages" / "personas", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    shutil.copytree(ROOT / "evals/persona/fixtures/governance", root / "evals/persona/fixtures/governance")
    assert write_lock(root, FIX) == []
    assert check(root, FIX)["ok"]
    return root


def _prompt(root: Path) -> Path:
    return root / "evals/persona/fixtures/governance/prompts/syn-tutor.prompt.md"


def _set_version(root: Path, v: str) -> None:
    p = root / FIX
    p.write_text(p.read_text(encoding="utf-8").replace("prompt_version: 1.0.0", f"prompt_version: {v}"), encoding="utf-8")


def test_change_without_bump_fails_and_lock_refuses(tmp_path):
    root = _root(tmp_path)
    _prompt(root).write_text(_prompt(root).read_text(encoding="utf-8") + "【合成】多一行。\n", encoding="utf-8")
    res = check(root, FIX)
    assert not res["ok"] and any("Prompt 变更必须升版本" in e for e in res["errors"])
    assert any("先升 prompt_version" in e for e in write_lock(root, FIX))


def test_whitespace_only_change_is_not_a_prompt_change(tmp_path):
    root = _root(tmp_path)
    _prompt(root).write_text(_prompt(root).read_text(encoding="utf-8").replace("\n", "  \r\n"), encoding="utf-8")
    assert check(root, FIX)["ok"]


def test_bump_without_lock_fails_then_lock_passes(tmp_path):
    root = _root(tmp_path)
    _prompt(root).write_text(_prompt(root).read_text(encoding="utf-8") + "【合成】多一行。\n", encoding="utf-8")
    _set_version(root, "1.0.1")
    res = check(root, FIX)
    assert any("运行 make persona-lock 并跑回归" in e for e in res["errors"])
    assert write_lock(root, FIX) == [] and check(root, FIX)["ok"]


def test_version_rollback_and_bad_semver(tmp_path):
    root = _root(tmp_path)
    _set_version(root, "0.9.0")
    assert any("回退" in e for e in check(root, FIX)["errors"])
    _set_version(root, "1.0")
    p = root / FIX
    p.write_text(p.read_text(encoding="utf-8").replace("prompt_version: 0.9.0", "prompt_version: 1.0"), encoding="utf-8")
    assert any("不是 semver" in e for e in check(root, FIX)["errors"])


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["personas"][1]["prompts"][0].__setitem__("prompt_id", "syn-tutor-main"), "prompt_id 重复"),
    (lambda d: d["personas"][0]["prompts"][0].__setitem__("path", "evals/persona/fixtures/governance/prompts/none.md"), "不存在"),
    (lambda d: d["personas"][0].__setitem__("type", "teacher"), "type 必须是"),
    (lambda d: d["personas"][0].__setitem__("forbidden_classes", ["A", "H"]), "不是已知的禁语类"),
    (lambda d: d["personas"][0]["prompts"][0].__setitem__("version_scheme", "integer"), "只有 type: task 允许整数版本号"),
    (lambda d: d["personas"][0].__setitem__("status", "not_started"), "未立项的角色不能登记 Prompt"),
    (lambda d: d["personas"][0].__setitem__("spec_doc", "docs/none.md"), "规格书"),
])
def test_registry_schema_problems(tmp_path, mutate, needle):
    root = _root(tmp_path)
    p = root / FIX
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    mutate(doc)
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    res = check(root, FIX)
    assert not res["ok"] and any(needle in e for e in res["errors"]), res["errors"]


def test_lock_entry_for_unregistered_prompt(tmp_path):
    root = _root(tmp_path)
    lp = root / "evals/persona/fixtures/governance/prompts.lock"
    doc = json.loads(lp.read_text(encoding="utf-8"))
    doc["prompts"]["ghost"] = {"version": "1.0.0", "sha256": "sha256:" + "0" * 64}
    lp.write_text(json.dumps(doc), encoding="utf-8")
    assert any("登记表之外的 prompt_id" in e for e in check(root, FIX)["errors"])


def test_lexicon_change_requires_manifest_version_bump(tmp_path):
    root = _root(tmp_path)
    g = root / "packages/personas/forbidden/G.yaml"
    g.write_text(g.read_text(encoding="utf-8").replace('examples_negative: ["依据不变。"]', 'examples_negative: ["依据不变，记入复核。"]'), encoding="utf-8")
    res = check(root, FIX)
    assert any("禁语词表变更必须升 manifest 的 version" in e for e in res["errors"])
    assert any("manifest 的 version 没升" in e for e in write_lock(root, FIX))
    m = root / "packages/personas/forbidden/manifest.yaml"
    m.write_text(m.read_text(encoding="utf-8").replace("version: 0.3.0", "version: 0.3.1"), encoding="utf-8")
    assert write_lock(root, FIX) == [] and check(root, FIX)["ok"]


def test_task_version_symbol_must_match(tmp_path):
    root = _root(tmp_path)
    src = root / "adapters/fake/src/prompt.ts"
    src.parent.mkdir(parents=True)
    src.write_text('export const PROMPT_TEMPLATE = "fake-grade";\nexport const PROMPT_VERSION = "0.1.0";\n'
                   'export const RULES = `合成判分规则 ${"x"} {不是人设}`;\n'
                   'export function build(item: { a: string }): string {\n  return `${RULES}\\n${item.a}`;\n}\n', encoding="utf-8")
    p = root / FIX
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["tasks"] = [{"task_id": "fake", "type": "task", "prompts": [{"prompt_id": "fake-grade", "path": "adapters/fake/src/prompt.ts",
                     "symbols": ["RULES", "build"], "prompt_version": "0.1.0", "version_symbol": "PROMPT_VERSION", "template_symbol": "PROMPT_TEMPLATE"}]}]
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert write_lock(root, FIX) == [] and check(root, FIX)["ok"]
    # 符号外的代码变化不影响指纹
    src.write_text(src.read_text(encoding="utf-8") + "export const OTHER = 1;\n", encoding="utf-8")
    assert check(root, FIX)["ok"]
    # 代码里的版本号改了、登记表没跟上
    src.write_text(src.read_text(encoding="utf-8").replace('PROMPT_VERSION = "0.1.0"', 'PROMPT_VERSION = "0.2.0"'), encoding="utf-8")
    assert any("与登记的 prompt_version 0.1.0 不一致" in e for e in check(root, FIX)["errors"])
    # 找不到符号
    src.write_text(src.read_text(encoding="utf-8").replace("export function build(", "export function build2("), encoding="utf-8")
    assert any("找不到符号 build" in e for e in check(root, FIX)["errors"])


# ---------- 未登记 Prompt 的启发式扫描（验收第 2 条）----------
def test_unregistered_system_string_fails(tmp_path):
    root = _root(tmp_path)
    f = root / "adapters/fake/prompt_builder.py"
    f.parent.mkdir(parents=True)
    f.write_text(f'SYSTEM = "{YOU_ARE}{LONG}"\n\ndef build():\n    return [{{"role": "{SYS}", "content": SYSTEM}}]\n', encoding="utf-8")
    res = check(root, FIX)
    assert not res["ok"]
    assert any("prompt_builder.py:1" in e and "你是" in e for e in res["errors"])
    assert any("prompt_builder.py:4" in e and "system" in e for e in res["errors"])


def test_ignore_marker_needs_reason_and_is_reported(tmp_path):
    root = _root(tmp_path)
    f = root / "adapters/fake/copy.ts"
    f.parent.mkdir(parents=True)
    f.write_text(f'// persona-registry: ignore\nexport const X = "{YOU_ARE}{LONG}";\n', encoding="utf-8")
    assert any("豁免必须写原因" in e for e in check(root, FIX)["errors"])
    f.write_text(f'// persona-registry: ignore 合成的前端文案样例，不发给模型\nexport const X = "{YOU_ARE}{LONG}";\n', encoding="utf-8")
    res = check(root, FIX)
    assert res["ok"] and res["exemptions"] == ["adapters/fake/copy.ts:2: 合成的前端文案样例，不发给模型"]


@pytest.mark.parametrize("rel,body,flagged", [
    ("adapters/fake/prompts/teacher.md", "【合成】随便什么内容\n", True),
    ("adapters/fake/tutor.prompt.txt", "【合成】随便什么内容\n", True),
    ("adapters/fake/short.py", f'X = "{YOU_ARE}谁"\n', False),                           # 太短，不像 Prompt
    ("adapters/fake/question.ts", f'const Q = "请讲给老师听：{YOU_ARE}怎么想的，为什么这样做。最后写出答案。再检查一遍步骤。"\n', False),  # “你是”不在句首
    ("adapters/fake/short_system.py", 'M = [{"role": "system", "content": "合成系统提示"}]\n', False),  # 短字面量的 system 消息（测试夹具常见）
    ("adapters/node_modules/x/index.js", f'module.exports = "{YOU_ARE}{LONG}"\n', False),               # 依赖目录不扫
])
def test_scan_heuristics(tmp_path, rel, body, flagged):
    root = _root(tmp_path)
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8")
    hits, _ = scan_unregistered(root, load_registry(root, FIX))
    assert bool(hits) is flagged, hits


def test_repo_scan_has_no_unregistered_prompt():
    hits, exempt = scan_unregistered(ROOT, load_registry(ROOT))
    assert hits == [] and exempt == []


def test_cli_exit_codes(tmp_path):
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415
    root = _root(tmp_path)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(PKG_PARENT)}
    run = lambda *a: subprocess.run([sys.executable, "-m", "personas.registry", *a, "--root", str(root), "--registry", FIX],  # noqa: E731
                                    capture_output=True, text=True, env=env)
    assert run("check").returncode == 0
    _prompt(root).write_text("【合成】改了\n", encoding="utf-8")
    r = run("check")
    assert r.returncode == 1 and "Prompt 变更必须升版本" in r.stdout
    assert run("lock").returncode == 1
