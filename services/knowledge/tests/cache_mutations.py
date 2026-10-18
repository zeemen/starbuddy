"""PLT-19 第一步：公共层只读缓存的变异测试。逐条把缓存键、已发布版本核对、错误不入缓存、副本、热切换清空、学生 ID 类字段拦截、
LRU 与计数、开关缺省值、应用接线改坏，断言 tests/test_service_cache.py（不连库）与 tests/test_service_cache_db.py（库上）能发现。

做法与 tests/material_mutations.py 相同（单独成目标 knowledge-cache-mutation：knowledge-mutation 已有 130 多条、约 10 分钟，
且每条都先复制整棵树、对照还要跑全部测试与 kge-compile 评测；这里的变异只需两个测试文件）：把 services/knowledge 复制到临时目录
（保持仓库结构；docs、db、packages、evals、tools 与 services/learning 软链接回仓库），改一处源码，跑指定测试。
先跑一次不变异的对照，必须通过；变异后只有“pytest 退出码 1（有断言失败）”才算被发现；收集错误（退出码 2 及以上）、
原文找不到或不唯一都算变异本身写错。每条限时 120 秒，超时算漏检。库上的变异要一次性 Postgres（同 tests/pgkge.py 的查找办法）；
没有时只跑其余变异，最后以退出码 3 报“部分跳过”（严格模式下 Makefile 判失败）。不联网。
用法（仓库根目录）：services/knowledge/.venv/bin/python services/knowledge/tests/cache_mutations.py
退出码：0 全部被发现；1 有漏检或对照不过；3 没有安装依赖或没有 Postgres（部分跳过）。
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SVC = Path(__file__).resolve().parents[1]
ROOT = SVC.parents[1]
UNIT = "tests/test_service_cache.py"
DB = "tests/test_service_cache_db.py"

# （名字，文件（相对 services/knowledge），原文，替换为，测试文件）
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    ("键漏 edition（node.get）", "knowledge/service/cache.py", '    "node.get": ("node_id", "edition", "region"),',
     '    "node.get": ("node_id", "region"),', UNIT),
    ("键漏 region（node.get）", "knowledge/service/cache.py", '    "node.get": ("node_id", "edition", "region"),',
     '    "node.get": ("node_id", "edition"),', UNIT),
    ("键漏 edition（page.get）", "knowledge/service/cache.py", '    "page.get": ("page_id", "edition", "region"),',
     '    "page.get": ("page_id", "region"),', UNIT),
    ("键漏 region（page.get）", "knowledge/service/cache.py", '    "page.get": ("page_id", "edition", "region"),',
     '    "page.get": ("page_id", "edition"),', UNIT),
    ("键漏 map_version_id", "knowledge/service/cache.py", 'return (tool, subject, str(version_id), commit or "", generation, params)',
     'return (tool, subject, "", commit or "", generation, params)', UNIT),
    ("键漏页面快照 commit", "knowledge/service/cache.py", 'return (tool, subject, str(version_id), commit or "", generation, params)',
     'return (tool, subject, str(version_id), "", generation, params)', UNIT),
    ("键漏快照代号（cache.key 丢掉 generation）", "knowledge/service/cache.py",
     'return (tool, subject, str(version_id), commit or "", generation, params)',
     'return (tool, subject, str(version_id), commit or "", None, params)', UNIT),
    ("键漏快照代号（tools 不传 generation）", "knowledge/service/tools.py",
     "snap.commit if snap is not None else None, generation, params)", "snap.commit if snap is not None else None, None, params)", UNIT),
    ("热切换不换快照代号", "knowledge/service/cache.py", "        self.generation += 1", "        pass", UNIT),
    ("热切换只清空不换代号（install 不调 new_snapshot）", "knowledge/service/hotswap.py", "            cache.new_snapshot()",
     '            cache.drop_tool("page.get")', UNIT),
    ("去掉入缓存前的快照核对（途中换了快照也入缓存）", "knowledge/service/tools.py",
     '        if tool == "page.get" and self.pages is not snap:', "        if False:", UNIT),
    ("键含每请求字段（request_id、caller 入键，永不命中）", "knowledge/service/cache.py",
     "return json.dumps(normalized, sort_keys=True", "return json.dumps(body, sort_keys=True", UNIT),
    ("不按契约缺省值补齐（max_depth 缺省与 3 分成两个键）", "knowledge/service/cache.py",
     "normalized = {**KEY_DEFAULTS.get(tool, {}), **{", "normalized = {**{", UNIT),
    ("不每请求查 published_version（记住上次的版本）", "knowledge/service/tools.py",
     '        v = await self._version(subject, body["context"])  # 每请求都查已发布版本，命中也不例外',
     '        pin = self.__dict__.setdefault("_pin", {}); v = pin.get(subject) or await self._version(subject, body["context"]); '
     'pin[subject] = v', UNIT),
    ("缓存错误结果", "knowledge/service/tools.py",
     "            value = await self.handlers[tool](body, pinned=v)  # type: ignore[call-arg]",
     "            try:\n                value = await self.handlers[tool](body, pinned=v)  # type: ignore[call-arg]\n"
     "            except ServiceError as e:\n                cache.put(key, e)\n                raise", UNIT),
    ("命中返回缓存里的同一对象", "knowledge/service/cache.py", "        return copy.deepcopy(hit[1])", "        return hit[1]", UNIT),
    ("入缓存不复制（调用方改返回值污染缓存）", "knowledge/service/cache.py", "self._entries[key] = (tool, copy.deepcopy(value), size)",
     "self._entries[key] = (tool, value, size)", UNIT),
    ("热切换不清空 page.get 条目", "knowledge/service/cache.py", '        self.drop_tool("page.get")\n        return self.generation',
     "        return self.generation", UNIT),
    ("热切换清空全部工具的条目", "knowledge/service/cache.py", '        self.drop_tool("page.get")\n        return self.generation',
     '        [self.drop_tool(t) for t in ("page.get", "node.get", "node.path")]\n        return self.generation', UNIT),
    ("参数里有学生 ID 类字段仍缓存", "knowledge/service/cache.py", "        found = personal_fields(body)\n        if found:",
     "        found = personal_fields(body)\n        if False:", UNIT),
    ("学生 ID 类字段告警记原始键名", "knowledge/service/cache.py", "out.append(m.group(1).lower())", "out.append(k[:48])", UNIT),
    ("学生 ID 类字段告警带出请求体", "knowledge/service/cache.py", '"categories": sorted(set(found))}',
     '"categories": sorted(set(found)), "body": body}', UNIT),
    ("入键字段以外的未知字段也缓存", "knowledge/service/cache.py", "        if extra:", "        if False:", UNIT),
    ("条目数上限不生效", "knowledge/service/cache.py", "        while len(self._entries) > self.size or self.bytes > self.max_bytes:",
     "        while self.bytes > self.max_bytes:", UNIT),
    ("字节上限不生效", "knowledge/service/cache.py", "        while len(self._entries) > self.size or self.bytes > self.max_bytes:",
     "        while len(self._entries) > self.size:", UNIT),
    ("单条超过上限的一部分也入缓存", "knowledge/service/cache.py", "        if size > self.entry_limit:", "        if False:", UNIT),
    ("同键重放字节数重复累计", "knowledge/service/cache.py", "            self.bytes -= old[2]", "            pass", UNIT),
    ("命中不刷新 LRU 顺序", "knowledge/service/cache.py", "        self._entries.move_to_end(key)\n        self.stats[tool].hits += 1",
     "        self.stats[tool].hits += 1", UNIT),
    ("淘汰不计数", "knowledge/service/cache.py", "            self.stats[gone_tool].evictions += 1", "            pass", UNIT),
    ("没有教材投影的 page.get 也入缓存", "knowledge/service/cache.py", '    if tool == "page.get":\n        result = value[0]',
     '    if False:\n        result = value[0]', UNIT),
    ("快照没配 / 页面不在快照的绕过不计数", "knowledge/service/tools.py", "                cache.note_bypass(tool)  # 另两种",
     "                pass  # 另两种", UNIT),
    ("不打计数日志", "knowledge/service/cache.py", "        if self._requests % self.log_every == 0:", "        if False:", UNIT),
    ("命中不进访问日志", "knowledge/service/tools.py", '            record["cache"] = "hit"\n', "", UNIT),
    ("缓存了 item.search", "knowledge/service/cache.py", '    "page.get": ("page_id", "edition", "region"),\n}',
     '    "page.get": ("page_id", "edition", "region"),\n    "item.search": ("subject", "node_id"),\n}', UNIT),
    ("开关缺省开", "knowledge/service/config.py", 'cache_flag = e.get("KNOWLEDGE_READ_CACHE", "0")',
     'cache_flag = e.get("KNOWLEDGE_READ_CACHE", "1")', UNIT),
    ("容量上限放回 100000", "knowledge/service/config.py", "MAX_CACHE_SIZE = 10_000", "MAX_CACHE_SIZE = 100_000", UNIT),
    ("数值配置用 isdigit（收其他文字的数字）", "knowledge/service/config.py", 'if not re.fullmatch(r"[0-9]+", raw)',
     "if not raw.isdigit()", UNIT),
    ("应用不接缓存", "knowledge/service/app.py", "log_every=settings.read_cache_log_every) if settings.read_cache else None",
     "log_every=settings.read_cache_log_every) if False else None", DB),
    ("应用不按配置的容量", "knowledge/service/app.py", "ReadCache(size=settings.read_cache_size, ", "ReadCache(", DB),
    ("应用不按配置的字节上限", "knowledge/service/app.py", "max_bytes=settings.read_cache_max_bytes,", "", DB),
    ("应用开关关时也接缓存", "knowledge/service/app.py", "log_every=settings.read_cache_log_every) if settings.read_cache else None",
     "log_every=settings.read_cache_log_every)", DB),
]


def have_pg() -> bool:
    if os.environ.get("PGBIN"):
        return os.path.exists(os.path.join(os.environ["PGBIN"], "initdb"))
    return bool(glob.glob("/usr/lib/postgresql/*/bin/initdb"))


def copy_tree(dst: Path) -> None:
    (dst / "services").mkdir(parents=True)
    shutil.copytree(SVC, dst / "services" / "knowledge", ignore=shutil.ignore_patterns(".venv", "__pycache__", ".cache", ".pytest_cache"))
    (dst / "services" / "learning").symlink_to(ROOT / "services" / "learning")
    for d in ("docs", "db", "packages", "evals", "tools"):
        (dst / d).symlink_to(ROOT / d)


def run_tests(root: Path, tests: list[str]) -> int:
    # 库上的测试在严格模式下跑：没有 Postgres 时直接失败，不会因为跳过而把变异算成“漏检”以外的结果
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "KNOWLEDGE_TEST_STRICT": "1"}
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=root / "services" / "knowledge",
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return 124
    return r.returncode


def main() -> int:
    try:
        import fastapi  # noqa: F401, PLC0415
        import pytest  # noqa: F401, PLC0415
    except ImportError:
        print("knowledge-cache-mutation: 跳过（没有安装依赖；见 services/knowledge/README.md）")
        return 3
    pg = have_pg()
    todo = [m for m in MUTATIONS if pg or m[4] != DB]
    if not pg:
        print(f"knowledge-cache-mutation: 本机没有 Postgres，库上的 {len(MUTATIONS) - len(todo)} 条变异跳过")
    with tempfile.TemporaryDirectory(prefix="kge-cache-mut-") as tmp:
        work = Path(tmp)
        control = work / "control"
        copy_tree(control)
        rc = run_tests(control, [UNIT, DB] if pg else [UNIT])
        if rc != 0:
            print(f"knowledge-cache-mutation: 对照（未变异）没有通过（退出码 {rc}），变异结果不可信")
            return 1
        missed = 0
        for i, (name, rel, orig, repl, tests) in enumerate(todo):
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
            rc = run_tests(dst, [tests])
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
            print(f"knowledge-cache-mutation: {missed} / {len(todo)} 条没有被测试发现")
            return 1
        print(f"knowledge-cache-mutation: {len(todo)} 条变异全部被测试发现")
        return 0 if pg else 3


if __name__ == "__main__":
    sys.exit(main())
