"""输出目录与文件的安全写入（安全审查 L3）：不给路径时用 mkdtemp；给了路径则要求不是符号链接、属于当前用户；
写文件用 O_NOFOLLOW，不跟随已存在的符号链接。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def prepare(path: str | Path | None, prefix: str) -> Path:
    if path is None or str(path) == "":
        return Path(tempfile.mkdtemp(prefix=f"{prefix}."))
    p = Path(path)
    if p.is_symlink():
        raise ValueError(f"输出目录不能是符号链接：{p}")
    if p.exists():
        if not p.is_dir():
            raise ValueError(f"输出路径不是目录：{p}")
        if p.stat().st_uid != os.getuid():
            raise ValueError(f"输出目录不属于当前用户：{p}")
    else:
        p.mkdir(parents=True, mode=0o700)
    return p


def write_text(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
