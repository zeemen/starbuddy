"""页面仓库模板（tools/kge-pages-template）：CI 样例调用 python -m knowledge.lint；ci/lint.sh 在模板副本上真的能跑。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from conftest import MAP_V1, PAGES, ROOT, SQUARE, edit, page_file

TEMPLATE = ROOT / "tools" / "kge-pages-template"
SERVICE = ROOT / "services" / "knowledge"


def test_template_files_present():
    for rel in ("README.md", "BRANCH_PROTECTION.md", "OPERATIONS.md", "repo/README.md", "repo/ci/lint.sh",
                "repo/ci/github-actions.yml.example", "repo/ci/gitlab-ci.yml.example", "repo/.github/PULL_REQUEST_TEMPLATE.md",
                "repo/pages/kp/.gitkeep", "repo/snapshots/README.md", "repo/lexicon/README.md"):
        assert (TEMPLATE / rel).is_file(), rel
    readme = (TEMPLATE / "README.md").read_text(encoding="utf-8")
    assert "ADR 12" in readme and "由人创建" in readme
    assert "python -m knowledge.lint" in (TEMPLATE / "repo/ci/lint.sh").read_text(encoding="utf-8")
    for ci in ("github-actions.yml.example", "gitlab-ci.yml.example"):
        text = (TEMPLATE / "repo/ci" / ci).read_text(encoding="utf-8")
        assert "ci/lint.sh" in text and "BASELINE_DIR" in text and "KNOWLEDGE_TOOLS_REF" in text
        assert "token: ghp_" not in text and "password" not in text.lower()


def _setup(tmp_path):
    repo = tmp_path / "pages-repo"
    shutil.copytree(TEMPLATE / "repo", repo)
    shutil.copytree(PAGES / "kp", repo / "pages" / "kp", dirs_exist_ok=True)
    shutil.copy(MAP_V1, repo / "snapshots" / "current.yaml")
    subprocess.run([sys.executable, "-m", "knowledge.lint", "--export-lexicon", str(repo / "lexicon" / "lexicon.json")],
                   cwd=str(SERVICE), check=True, capture_output=True)
    baseline = tmp_path / "baseline"
    shutil.copytree(repo, baseline)
    env = {**os.environ, "KNOWLEDGE_DIR": str(SERVICE), "BASELINE_DIR": str(baseline), "PYTHON": sys.executable, "CI": "true"}
    return repo, env


def test_lint_sh_passes_on_clean_repo_and_blocks_on_bad_page(tmp_path):
    repo, env = _setup(tmp_path)
    ok = subprocess.run(["bash", str(repo / "ci" / "lint.sh")], env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    report = json.loads((repo / "lint-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["pages"] == 4 and report["summary"]["blocks"] == 0
    edit(page_file(repo / "pages", SQUARE), "a^2 - b^2 % check", "a^2 + b^2 % check")
    bad = subprocess.run(["bash", str(repo / "ci" / "lint.sh")], env=env, capture_output=True, text=True)
    assert bad.returncode == 1 and "阻断 1" in bad.stdout


def test_lint_sh_requires_baseline_in_ci(tmp_path):
    repo, env = _setup(tmp_path)
    env.pop("BASELINE_DIR")
    r = subprocess.run(["bash", str(repo / "ci" / "lint.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 2
    env.pop("KNOWLEDGE_DIR")
    r = subprocess.run(["bash", str(repo / "ci" / "lint.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 2


def test_codification_doc_lists_every_check_type_and_leaves_teaching_content_blank():
    import re
    from knowledge.lint.issues import CHECK_TYPES
    from knowledge.lint.lexicon import MAX_SENTENCE_CHARS
    from knowledge.lint.checks import DEFAULT_MIN_GROUP
    doc = (ROOT / "docs/design/kge/codification-math-v1.md").read_text(encoding="utf-8")
    section9 = doc.split("## 9. 体检结果", 1)[1].split("## 10.", 1)[0]
    listed = set(re.findall(r"`([a-z_]+)`", section9.split("检查类型：", 1)[1].split("\n- ", 1)[0]))
    assert listed == set(CHECK_TYPES)
    assert f"**{MAX_SENTENCE_CHARS} 字**" in doc and f"**{DEFAULT_MIN_GROUP}**" in doc
    teaching = doc.split("## 10. 教学内容要求", 1)[1].split("## 11.", 1)[0]
    assert teaching.count("待教研填写并签发") >= 7
