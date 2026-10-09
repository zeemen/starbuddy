"""非可编辑安装下的启动测试（W13 技术债 T07，specs/tech-debt-batch1）。

契约共享库装进 site-packages（非可编辑，如 services/learning/Dockerfile）后，它按自己文件的位置找契约 YAML，
仓库里的 packages/contracts/openapi 不再被读到。这里在临时目录里摆出与非可编辑安装相同的布局
（<前缀>/lib/pythonX.Y/site-packages/starbuddy_contract/），用子进程（PYTHONPATH 指向它，排在 .venv 的可编辑安装前面）断言：
1. 不放契约时，学习服务启动（lifespan 加载契约）立即失败，报的是临时布局里的路径，说明确实没有回落到仓库；
2. 跑 scripts/place_contract_yaml.py 之后，同一布局下启动通过，且共享库确实来自临时布局。
不连库（注入假连接池）、不联网、不读密钥；真实的 pip 非可编辑安装与镜像构建见 services/learning/README.md“镜像（dev / staging）”。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parents[1]
LIB = REPO / "packages" / "contracts" / "python" / "starbuddy_contract"
OPENAPI = REPO / "packages" / "contracts" / "openapi"

# 子进程：起学习服务的 lifespan（假连接池让迁移检查通过），报告共享库的位置与结果
START = r"""
import asyncio, json, sys
import starbuddy_contract
from learning.app import create_app
from learning.config import AdapterTarget, Settings

class Pool:
    async def fetchval(self, q, *a):
        return True

async def main():
    s = Settings(env="test", database_url="unused", service_credential="synthetic-bff-to-learning-credential-0001",
                 adapters={"aya": AdapterTarget("http://aya-adapter.invalid", "synthetic-learning-to-adapter-credential-01")})
    app = create_app(s, pool=Pool())
    out = {"lib": starbuddy_contract.__file__}
    try:
        async with app.router.lifespan_context(app):
            out["started"] = True
    except FileNotFoundError as e:
        out["started"] = False
        out["missing"] = str(e.filename)
    print(json.dumps(out))

asyncio.run(main())
"""


def _layout(tmp: Path) -> tuple[Path, Path]:
    """摆出非可编辑安装的目录布局：返回 (site-packages, 共享库应当去找契约的目录)。"""
    pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = tmp / "prefix" / "lib" / pyver / "site-packages"
    shutil.copytree(LIB, site / "starbuddy_contract", ignore=shutil.ignore_patterns("__pycache__"))
    return site, tmp / "prefix" / "lib" / pyver / "openapi"


def _env(site: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("STARBUDDY_")}
    env["PYTHONPATH"] = os.pathsep.join([str(site), str(SERVICE)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _start(site: Path) -> dict:
    r = subprocess.run([sys.executable, "-c", START], cwd=SERVICE, env=_env(site), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_noneditable_layout_fails_fast_then_starts_after_placing_contracts(tmp_path):
    site, expected = _layout(tmp_path)

    before = _start(site)
    assert Path(before["lib"]).is_relative_to(site), f"共享库应来自临时布局，实际 {before['lib']}"
    assert before["started"] is False, "不放契约也启动了：说明回落到了仓库里的文件"
    assert Path(before["missing"]).is_relative_to(expected), before["missing"]

    r = subprocess.run([sys.executable, str(SERVICE / "scripts" / "place_contract_yaml.py"), "--source", str(OPENAPI)],
                       cwd=SERVICE, env=_env(site), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.name for p in expected.glob("*.yaml")) == sorted(p.name for p in OPENAPI.glob("*.yaml"))
    for p in expected.glob("*.yaml"):
        assert p.read_bytes() == (OPENAPI / p.name).read_bytes(), p.name

    after = _start(site)
    assert Path(after["lib"]).is_relative_to(site)
    assert after == {"lib": after["lib"], "started": True}


def test_place_contract_yaml_rejects_empty_source(tmp_path):
    site, expected = _layout(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    r = subprocess.run([sys.executable, str(SERVICE / "scripts" / "place_contract_yaml.py"), "--source", str(empty)],
                       cwd=SERVICE, env=_env(site), capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and "没有 *.yaml" in r.stderr, r.stdout + r.stderr
    assert not expected.exists()
    r = subprocess.run([sys.executable, str(SERVICE / "scripts" / "place_contract_yaml.py"), "--source", str(tmp_path / "nope")],
                       cwd=SERVICE, env=_env(site), capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def _place(site: Path, source: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SERVICE / "scripts" / "place_contract_yaml.py"), "--source", str(source)],
                          cwd=SERVICE, env=_env(site), capture_output=True, text=True, timeout=120)


def test_place_contract_yaml_rejects_symlinks(tmp_path):
    """审查意见 3：源目录、源文件、目标处已有的文件是符号链接时拒绝，不跟随链接读写。"""
    site, expected = _layout(tmp_path)
    # 源目录是符号链接
    link_dir = tmp_path / "openapi-link"
    link_dir.symlink_to(OPENAPI, target_is_directory=True)
    r = _place(site, link_dir)
    assert r.returncode == 1 and "源目录是符号链接" in r.stderr, r.stdout + r.stderr
    assert not expected.exists()
    # 源目录里有一份是符号链接（指向别处的合成文件）
    src = tmp_path / "src"
    src.mkdir()
    for p in OPENAPI.glob("*.yaml"):
        (src / p.name).write_bytes(p.read_bytes())
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text("合成：不是契约\n", encoding="utf-8")
    victim = sorted(src.glob("*.yaml"))[0]
    victim.unlink()
    victim.symlink_to(elsewhere)
    r = _place(site, src)
    assert r.returncode == 1 and "源文件是符号链接" in r.stderr and victim.name in r.stderr, r.stdout + r.stderr
    assert not expected.exists()
    # 目标处已有同名符号链接：拒绝，且不经链接写到别处
    expected.mkdir(parents=True)
    target_victim = tmp_path / "target-victim.yaml"
    target_victim.write_text("合成：原样保留\n", encoding="utf-8")
    name = sorted(OPENAPI.glob("*.yaml"))[0].name
    (expected / name).symlink_to(target_victim)
    r = _place(site, OPENAPI)
    assert r.returncode == 1 and "目标处已有符号链接" in r.stderr, r.stdout + r.stderr
    assert target_victim.read_text(encoding="utf-8") == "合成：原样保留\n"
    assert sorted(p.name for p in expected.iterdir()) == [name], "拒绝时一份都不复制"
