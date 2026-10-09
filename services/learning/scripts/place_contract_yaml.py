"""非可编辑安装时放置契约 YAML（W13 技术债 T07，specs/tech-debt-batch1）。

契约共享库 starbuddy_contract 按自己文件的位置找契约：OPENAPI = <contract.py 往上两级>/openapi。可编辑安装时那就是
packages/contracts/openapi；装进 site-packages（非可编辑，例如 Dockerfile）后变成 <前缀>/lib/pythonX.Y/openapi，那里没有文件，
服务启动加载契约时立即失败（app.py 的 lifespan，安全审查 S6）。

本脚本不改共享库（packages/contracts 的变更属于必停项）：先导入已安装的共享库、取它自己报告的 OPENAPI 路径，
把 --source 下的全部 *.yaml 原样复制过去，再逐份核对——插件契约各版本、学习服务内部契约各版本的文件都在且能解析，
缺省版本的插件契约与 v0、v1 内部契约能建校验器。源与目标是同一目录（可编辑安装）时只核对、不复制。
源目录、源文件、目标目录、目标处已有的同名文件是符号链接时一律拒绝（退出码 1），不跟随链接读写。Dockerfile 里用 python -I 运行。

用法（Dockerfile 里、或任何非可编辑安装之后）：
    python services/learning/scripts/place_contract_yaml.py --source packages/contracts/openapi
退出码：0 = 放好且核对通过；2 = 参数不对或源目录不存在；1 = 源目录没有 YAML 或核对失败。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def place(source: Path) -> Path:
    from starbuddy_contract import contract as c

    # 符号链接一律拒绝（源目录、源文件、目标目录、目标文件）：防止把别处的文件当契约放进去，或经目标处的链接写到别处
    if source.is_symlink():
        raise RuntimeError(f"源目录是符号链接：{source}")
    src = source.resolve()
    names = sorted(p.name for p in src.glob("*.yaml"))
    if not names:
        raise RuntimeError(f"{source} 下没有 *.yaml")
    links = [n for n in names if (src / n).is_symlink()]
    if links:
        raise RuntimeError(f"源文件是符号链接：{'、'.join(links)}")
    dst = c.OPENAPI
    if dst.is_symlink():
        raise RuntimeError(f"目标目录是符号链接：{dst}")
    if dst.resolve() != src:
        dst.mkdir(parents=True, exist_ok=True)
        links = [n for n in names if (dst / n).is_symlink()]
        if links:
            raise RuntimeError(f"目标处已有符号链接：{'、'.join(links)}")
        for n in names:
            shutil.copyfile(src / n, dst / n, follow_symlinks=False)
    verify()
    return dst


def verify() -> None:
    """核对共享库登记的每一份契约文件都在、能解析；缺省版本能建校验器（与学习服务启动时加载的一致）。"""
    import yaml
    from starbuddy_contract import contract as c

    for path in [*c.PLUGIN_CONTRACTS.values(), *c.INTERNAL_CONTRACTS.values()]:
        if not path.is_file():
            raise RuntimeError(f"契约文件不在：{path}")
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "components" not in doc:
            raise RuntimeError(f"契约文件不是 OpenAPI 文档：{path}")
    c.plugin_contract()
    c.internal_contract()
    c.internal_contract("1.0.0")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", required=True, type=Path, help="契约 YAML 所在目录（仓库里的 packages/contracts/openapi）")
    args = ap.parse_args(argv)
    if not args.source.is_dir():
        print(f"place_contract_yaml: 源目录不存在：{args.source}", file=sys.stderr)
        return 2
    try:
        dst = place(args.source)
    except RuntimeError as e:
        print(f"place_contract_yaml: 核对失败：{e}", file=sys.stderr)
        return 1
    print(f"place_contract_yaml: 契约已就位并核对通过：{dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
