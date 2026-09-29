"""生成对照评测的合成页面快照（page-snapshot/）：把 services/knowledge/tests/fixtures/pages/kp 的合成页面按
knowledge.compiler.snapshot 的导出格式（manifest.json + pages/kp/*.md）放好。提交号是页面内容的 sha1（合成，不对应任何仓库）。
测试核对这里的文件与重新生成的结果逐字节一致（夹具不会与知识服务的合成页面走样）。

    python make_fixture.py            # 重新生成
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[2] / "services" / "knowledge" / "tests" / "fixtures" / "pages" / "kp"
OUT = HERE / "page-snapshot"


def build() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    entries = []
    seed = hashlib.sha1(b"kge-compile synthetic page snapshot\n")
    for p in sorted(SOURCE.glob("*.md")):
        raw = p.read_bytes()
        seed.update(raw)
        head = yaml.safe_load(raw.decode("utf-8").split("\n---\n", 1)[0].removeprefix("---\n"))
        rel = f"pages/kp/{p.name}"
        files[rel] = raw
        entries.append({"page_id": head["page_id"], "path": rel, "page_version": str(head["page_version"]),
                        "map_version": str(head["map_version"]), "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {"format": "kge-page-snapshot/1", "commit": seed.hexdigest(), "exported_at": "2026-09-29T00:00:00+08:00",
                "synthetic": True, "pages": entries}
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return files


def main() -> int:
    for rel, raw in build().items():
        dst = OUT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(raw)
    print(f"已生成：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
