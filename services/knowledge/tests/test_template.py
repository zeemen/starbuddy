"""页面仓库模板（tools/kge-pages-template）：CI 样例调用 python -m knowledge.lint；ci/lint.sh 在模板副本上真的能跑。
审查处理：CI 样例不从合并请求头部取 lint.sh、地图快照、词表，合并请求任务不引用任何 secrets；action 固定到提交 SHA；
安装检查工具核 sha256、pip --require-hashes；分支保护清单有代码所有者与路径规则。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

import yaml

from conftest import APPLY, MAP_V1, PAGES, ROOT, SQUARE, edit, page_file

TEMPLATE = ROOT / "tools" / "kge-pages-template"
SERVICE = ROOT / "services" / "knowledge"


def test_template_files_present():
    for rel in ("README.md", "BRANCH_PROTECTION.md", "OPERATIONS.md", "repo/README.md", "repo/ci/lint.sh",
                "repo/ci/install-tools.sh", "repo/ci/kge-tools.pin", "repo/.github/CODEOWNERS",
                "repo/ci/github-actions.yml.example", "repo/ci/gitlab-ci.yml.example", "repo/.github/PULL_REQUEST_TEMPLATE.md",
                "repo/pages/kp/.gitkeep", "repo/snapshots/README.md", "repo/lexicon/README.md"):
        assert (TEMPLATE / rel).is_file(), rel
    readme = (TEMPLATE / "README.md").read_text(encoding="utf-8")
    assert "ADR 12" in readme and "由人创建" in readme
    assert "python -m knowledge.lint" in (TEMPLATE / "repo/ci/lint.sh").read_text(encoding="utf-8")
    for ci in ("github-actions.yml.example", "gitlab-ci.yml.example"):
        text = (TEMPLATE / "repo/ci" / ci).read_text(encoding="utf-8")
        assert "ci/lint.sh" in text and "HEAD_PAGES_DIR" in text and "install-tools.sh" in text
        assert "token: ghp_" not in text and "password" not in text.lower()


# ---------- 审查处理：CI 样例的信任边界 ----------
SHA40 = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _github() -> dict:
    return yaml.safe_load((TEMPLATE / "repo/ci/github-actions.yml.example").read_text(encoding="utf-8"))


def test_github_sample_uses_pull_request_only_and_no_secrets():
    text = (TEMPLATE / "repo/ci/github-actions.yml.example").read_text(encoding="utf-8")
    wf = _github()
    triggers = wf.get("on", wf.get(True))  # YAML 1.1 把 on 读成 True
    assert set(triggers) == {"pull_request"}
    code = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())  # 去掉注释（注释里写着“不得用 pull_request_target”）
    assert "pull_request_target" not in code and "secrets." not in code and "secrets[" not in code
    assert "github.token" not in code and "GITHUB_TOKEN" not in code
    assert wf["permissions"] == {"contents": "read"}
    steps = wf["jobs"]["kge-lint"]["steps"]
    for st in steps:
        if "uses" in st:
            assert SHA40.match(st["uses"]), st["uses"]  # 固定到完整提交 SHA（样例是占位，注释写明必须替换）
        if str(st.get("uses", "")).startswith("actions/checkout@"):
            assert st["with"]["persist-credentials"] is False and "token" not in st["with"]
    assert "必须替换" in text


def test_github_sample_takes_only_pages_from_head():
    steps = _github()["jobs"]["kge-lint"]["steps"]
    checkouts = [st for st in steps if str(st.get("uses", "")).startswith("actions/checkout@")]
    main = next(st for st in checkouts if st["with"].get("ref") == "main")
    head = next(st for st in checkouts if "head.sha" in str(st["with"].get("ref")))
    assert main["with"]["path"] == "main" and head["with"]["path"] == "head"
    assert head["with"]["sparse-checkout"].strip("/") == "pages"
    runs = [str(st.get("run", "")) for st in steps]
    lint = next(st for st in steps if "lint.sh" in str(st.get("run", "")))
    assert lint["run"].strip() == "bash main/ci/lint.sh"  # 体检入口来自 main
    assert lint["env"]["HEAD_PAGES_DIR"].endswith("/head/pages") and "KNOWLEDGE_DIR" in lint["env"]
    assert any(r.strip() == 'bash main/ci/install-tools.sh "$RUNNER_TEMP/kge-tools"' for r in runs)
    for r in runs:  # 头部除了 pages/ 什么都不用、不执行
        for bad in ("head/ci", "head/snapshots", "head/lexicon", "bash head", "source head", "pip install -r head"):
            assert bad not in r, (bad, r)
    assert "rm -rf head" in "\n".join(runs)


def test_gitlab_sample_takes_tools_and_inputs_from_main_without_credentials():
    text = (TEMPLATE / "repo/ci/gitlab-ci.yml.example").read_text(encoding="utf-8")
    job = yaml.safe_load(text)["kge-lint"]
    assert re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", job["image"])
    script = "\n".join(job["script"])
    assert "bash .kge-main/ci/lint.sh" in script and "bash .kge-main/ci/install-tools.sh" in script
    assert "bash ci/lint.sh" not in script and "HEAD_PAGES_DIR=\"$CI_PROJECT_DIR/pages\"" in script
    assert "worktree add --detach .kge-main refs/remotes/origin/main" in script
    code = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
    for cred in ("STARBUDDY_READ", "TOKEN", "PASSWORD", "SECRET"):  # 不引用任何凭据变量（CI_JOB_TOKEN 也不显式用）
        assert cred not in code.upper().replace("CI_JOB_TOKEN_", ""), cred


def test_install_tools_is_pinned_hash_checked_and_credential_free():
    text = (TEMPLATE / "repo/ci/install-tools.sh").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
    assert "--require-hashes -r \"$lock\"" in code and "sha256sum --check" in code and "--proto '=https'" in code
    assert "requirements.lock" in code and "requirements.txt" not in text
    for cred in ("Authorization", "--user", "-u ", "TOKEN", "netrc"):
        assert cred not in code, cred
    assert "source " not in code and ". \"$pin\"" not in code  # 不执行 pin 文件，只取两行值
    pin = (TEMPLATE / "repo/ci/kge-tools.pin").read_text(encoding="utf-8")
    assert re.search(r"^KGE_TOOLS_URL=https://", pin, re.M) and re.search(r"^KGE_TOOLS_SHA256=[0-9a-f]{64}$", pin, re.M)


def test_install_tools_refuses_placeholder_pin(tmp_path):
    repo = tmp_path / "r"
    shutil.copytree(TEMPLATE / "repo", repo)
    r = subprocess.run(["bash", str(repo / "ci" / "install-tools.sh"), str(tmp_path / "tools")], capture_output=True, text=True)
    assert r.returncode == 2 and "占位" in r.stderr  # 占位 sha256 不下载
    (repo / "ci" / "kge-tools.pin").write_text("KGE_TOOLS_URL=http://x.invalid/a.tgz\nKGE_TOOLS_SHA256=" + "a" * 64 + "\n",
                                               encoding="utf-8")
    r = subprocess.run(["bash", str(repo / "ci" / "install-tools.sh"), str(tmp_path / "tools")], capture_output=True, text=True)
    assert r.returncode == 2 and "https" in r.stderr


def test_branch_protection_has_codeowners_and_path_rules():
    doc = (TEMPLATE / "BRANCH_PROTECTION.md").read_text(encoding="utf-8")
    for needle in ("CODEOWNERS", "`ci/`", "`.github/`", "`snapshots/`", "`lexicon/`", "`.gitlab-ci.yml`", "平台负责人",
                   "pull_request_target", "提交 SHA", "--require-hashes"):
        assert needle in doc, needle
    owners = (TEMPLATE / "repo/.github/CODEOWNERS").read_text(encoding="utf-8")
    rules = {ln.split()[0] for ln in owners.splitlines() if ln.strip() and not ln.startswith("#")}
    assert {"/ci/", "/.github/", "/snapshots/", "/lexicon/", "/.gitlab-ci.yml"} <= rules


def _setup(tmp_path):
    """main 的检出（lint.sh、快照、词表、上一审核版）+ 合并请求头部（只用它的 pages/）"""
    main = tmp_path / "main"
    shutil.copytree(TEMPLATE / "repo", main)
    shutil.copytree(PAGES / "kp", main / "pages" / "kp", dirs_exist_ok=True)
    shutil.copy(MAP_V1, main / "snapshots" / "current.yaml")
    subprocess.run([sys.executable, "-m", "knowledge.lint", "--export-lexicon", str(main / "lexicon" / "lexicon.json")],
                   cwd=str(SERVICE), check=True, capture_output=True)
    head = tmp_path / "head"
    shutil.copytree(main, head)
    env = {**os.environ, "KNOWLEDGE_DIR": str(SERVICE), "HEAD_PAGES_DIR": str(head / "pages"), "PYTHON": sys.executable,
           "CI": "true", "LINT_REPORT": str(tmp_path / "lint-report.json")}
    return main, head, env


def _lint(main, env):
    return subprocess.run(["bash", str(main / "ci" / "lint.sh")], env=env, capture_output=True, text=True)


def test_lint_sh_passes_on_clean_repo_and_blocks_on_bad_page(tmp_path):
    main, head, env = _setup(tmp_path)
    ok = _lint(main, env)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    report = json.loads((tmp_path / "lint-report.json").read_text(encoding="utf-8"))
    assert report["summary"]["pages"] == 4 and report["summary"]["blocks"] == 0
    edit(page_file(head / "pages", SQUARE), "a^2 - b^2 % check", "a^2 + b^2 % check")
    bad = _lint(main, env)
    assert bad.returncode == 1 and "阻断 1" in bad.stdout


def test_lint_sh_ignores_head_ci_snapshot_and_lexicon(tmp_path):
    """合并请求头部改了 ci/lint.sh、地图快照、词表：体检照样用 main 的，坏页面照样阻断"""
    main, head, env = _setup(tmp_path)
    edit(page_file(head / "pages", SQUARE), "a^2 - b^2 % check", "a^2 + b^2 % check")
    (head / "ci" / "lint.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (head / "snapshots" / "current.yaml").write_text("map_version: 9.9.9\n", encoding="utf-8")
    (head / "lexicon" / "lexicon.json").write_text("{}", encoding="utf-8")
    edit(page_file(head / "pages", APPLY), "设未知数时没有写单位", "阿鸦提醒，设未知数时没有写单位")
    r = _lint(main, env)
    assert r.returncode == 1
    kinds = {i["check_type"] for i in json.loads((tmp_path / "lint-report.json").read_text(encoding="utf-8"))["issues"]}
    assert {"sympy", "persona"} <= kinds


def test_lint_sh_requires_head_pages_snapshot_and_lexicon_in_ci(tmp_path):
    main, head, env = _setup(tmp_path)
    r = _lint(main, {k: v for k, v in env.items() if k != "HEAD_PAGES_DIR"})
    assert r.returncode == 2
    r = _lint(main, {k: v for k, v in env.items() if k != "KNOWLEDGE_DIR"})
    assert r.returncode == 2
    (main / "snapshots" / "current.yaml").rename(tmp_path / "moved.yaml")
    assert _lint(main, env).returncode == 2  # 地图快照必需
    (tmp_path / "moved.yaml").rename(main / "snapshots" / "current.yaml")
    (main / "lexicon" / "lexicon.json").unlink()
    assert _lint(main, env).returncode == 2  # 词表必需


def test_lint_sh_rejects_symlinks_in_head_pages(tmp_path):
    main, head, env = _setup(tmp_path)
    (head / "pages" / "kp" / "LINK.md").symlink_to("/etc/hostname")
    r = _lint(main, env)
    assert r.returncode == 2 and "符号链接" in r.stderr


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
