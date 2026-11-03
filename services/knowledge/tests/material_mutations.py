"""KGE-07 第一步：编译层素材卡的变异测试。逐条把素材卡的页面类型、模板、体检与编译器改动改坏，断言 tests/test_material.py 能发现。

做法与 tests/mutations.sh 相同（那份由其他任务维护，本文件单独成目标 knowledge-material-mutation）：把 services/knowledge 复制到
临时目录（保持仓库结构；docs、db、packages、evals、tools 与 services/learning 软链接回仓库），改一处源码，跑指定测试。
先跑一次不变异的对照，必须通过；变异后只有“pytest 退出码 1（有断言失败）”才算被发现；收集错误（退出码 2 及以上）、
原文找不到或不唯一都算变异本身写错。每条限时 120 秒，超时算漏检。不连库、不联网。
用法（仓库根目录）：services/knowledge/.venv/bin/python services/knowledge/tests/material_mutations.py
退出码：0 全部被发现；1 有漏检或对照不过；3 没有安装依赖。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SVC = Path(__file__).resolve().parents[1]
ROOT = SVC.parents[1]
TESTS = "tests/test_material.py"

# （名字，文件（相对 services/knowledge），原文，替换为）
MUTATIONS: list[tuple[str, str, str, str]] = [
    ("素材卡不是已实现的页面类型", "knowledge/pages/format.py", 'PAGE_TYPES = ("kp", "material")', 'PAGE_TYPES = ("kp",)'),
    ("原文段写进原句", "knowledge/compiler/templates.py", 'body += [f"- {{{{quote:{a.id}}}}} {_cite(a)}" for a in items]',
     "body += [f\"- {a.record.get('quote_text') or a.id} {_cite(a)}\" for a in items]"),
    ("出处漏了课", "knowledge/compiler/templates.py", "{p['volume']}-{p['chapter']}-{pages}", "{p['volume']}-{pages}"),
    ("易混项与关联漏了关联", "knowledge/compiler/templates.py", '(("易混：", "confusable"), ("关联：", "related"))', '(("易混：", "confusable"),)'),
    ("素材卡的释义要点不进词条", "knowledge/compiler/templates.py", '    if n.get("includes"):\n        body += ["", "释义要点：',
     '    if False:\n        body += ["", "释义要点：'),
    ("编译器按知识点页渲染素材卡", "knowledge/compiler/rebuild.py", "render_sections(snapshot, node_id, known_pages, ptype)",
     "render_sections(snapshot, node_id, known_pages)"),
    ("体检不按页面类型核对模板", "knowledge/lint/checks.py", 'render_sections(snap, node_id, ctx.page_ids, str(page.header.get("page_type")))',
     "render_sections(snap, node_id, ctx.page_ids)"),
    ("素材卡挂错节点不报孤立页", "knowledge/lint/checks.py", 'elif pf.page.header.get("page_type") == "material" and (', "elif False and ("),
    ("素材卡可以挂能力点", "knowledge/lint/checks.py", 'MATERIAL_NODE_KINDS = ("material",)', 'MATERIAL_NODE_KINDS = ("material", "ability_kp")'),
    ("出处变更判为低风险", "knowledge/compiler/rebuild.py", 'TEXTBOOK_SECTIONS = ("textbook", "source_text", "provenance")', 'TEXTBOOK_SECTIONS = ("textbook",)'),
    ("易混另一端改名不重编素材卡", "knowledge/compiler/jobs.py",
     "            touched |= _material_neighbors(pages, by_node, old, new, key[5:])\n", ""),
    ("易混关联邻居的扩展对知识点页也生效", "knowledge/compiler/jobs.py",
     'return {n for n in linked if n in by_node and pages[by_node[n]].header.get("page_type") == "material"}',
     "return {n for n in linked if n in by_node}"),
    # 现行行为锁定：快照导出与 page.get 只收知识点页（变异在副本里改，仓库里的 service/ 不动）
    ("快照导出放行 pages/material", "knowledge/compiler/snapshot.py", 'p.parts[1] != "kp"', 'p.parts[1] not in ("kp", "material")'),
    ("知识服务载入放行 pages/material 路径", "knowledge/service/pages.py", 'rel != f"pages/kp/{nid}.md"',
     'rel not in (f"pages/kp/{nid}.md", f"pages/material/{nid}.md")'),
    ("知识服务载入不查页面类型", "knowledge/service/pages.py",
     'h.get("node_id") != nid or h.get("page_type") != "kp" or str(h.get("page_version")) != ver',
     'h.get("node_id") != nid or str(h.get("page_version")) != ver'),
    ("素材卡放进全量重编", "knowledge/compiler/full.py", '    if old.header.get("page_type") != "kp":\n', "    if False:\n"),
]


def copy_tree(dst: Path) -> None:
    (dst / "services").mkdir(parents=True)
    shutil.copytree(SVC, dst / "services" / "knowledge", ignore=shutil.ignore_patterns(".venv", "__pycache__", ".cache", ".pytest_cache"))
    (dst / "services" / "learning").symlink_to(ROOT / "services" / "learning")
    for d in ("docs", "db", "packages", "evals", "tools"):
        (dst / d).symlink_to(ROOT / d)


def run_tests(root: Path) -> int:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", TESTS], cwd=root / "services" / "knowledge",
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return 124
    return r.returncode


def main() -> int:
    try:
        import pytest  # noqa: F401, PLC0415
        import yaml  # noqa: F401, PLC0415
    except ImportError:
        print("knowledge-material-mutation: 跳过（没有安装依赖；见 services/knowledge/README.md）")
        return 3
    with tempfile.TemporaryDirectory(prefix="kge-mat-mut-") as tmp:
        work = Path(tmp)
        control = work / "control"
        copy_tree(control)
        rc = run_tests(control)
        if rc != 0:
            print(f"knowledge-material-mutation: 对照（未变异）没有通过（退出码 {rc}），变异结果不可信")
            return 1
        missed = 0
        for i, (name, rel, orig, repl) in enumerate(MUTATIONS):
            dst = work / f"m{i}"
            copy_tree(dst)
            path = dst / "services" / "knowledge" / rel
            text = path.read_text(encoding="utf-8")
            if text.count(orig) != 1:
                print(f"  变异本身写错（原文找不到或不唯一）：{name}")
                missed += 1
                shutil.rmtree(dst)
                continue
            path.write_text(text.replace(orig, repl), encoding="utf-8")
            rc = run_tests(dst)
            if rc == 0:
                print(f"  漏检：{name}")
                missed += 1
            elif rc == 124:
                print(f"  超时（算漏检）：{name}")
                missed += 1
            elif rc != 1:
                print(f"  变异本身写错（pytest 退出码 {rc}）：{name}")
                missed += 1
            shutil.rmtree(dst)
        if missed:
            print(f"knowledge-material-mutation: {missed} / {len(MUTATIONS)} 条没有被测试发现")
            return 1
        print(f"knowledge-material-mutation: {len(MUTATIONS)} 条变异全部被测试发现")
        return 0


if __name__ == "__main__":
    sys.exit(main())
