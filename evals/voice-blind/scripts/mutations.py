"""语音盲测评测框架的变异测试（specs/voice-blind-test-sprint4；仿 evals/persona/scripts/mutations.py）。

每个变异把指标计算、规范化、自助法、盲测、清单校验或报告的一处关键判定改坏，复制 evals/voice-blind 到临时目录里跑测试，
断言测试能发现。只有测试断言失败（退出码 1）算发现；收集或导入错误不算。先跑一次不变异的对照，必须通过。
框架自包含（只依赖标准库、PyYAML、pytest），临时目录里只放 evals/voice-blind 的副本（保持 evals/voice-blind 两层结构）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]  # evals/voice-blind
M, T, S, R, B, V, MF, RUN = ("voiceblind/metrics.py", "voiceblind/text.py", "voiceblind/stats.py", "voiceblind/report.py",
                            "voiceblind/blind.py", "voiceblind/vendor.py", "voiceblind/manifest.py", "run_blind.py")

MUTATIONS: dict[str, tuple[str, str, str]] = {
    # 编辑距离与 CER / WER
    "替换不计代价": (M, "sub = prev[j - 1] + (ref[i - 1] != hyp[j - 1]) * sub_cost", "sub = prev[j - 1]"),
    "插入不计入错误": (M, "            ins += 1", "            ins += 0"),
    "空参考算 0 错": (M, "        if self.ref_len == 0:\n            return None", "        if self.ref_len == 0:\n            return 0.0"),
    "空参考计入分母": (M, "        total += c.ref_len", "        total += max(c.ref_len, 1)"),
    "中文不做全半角规范化": (T, 'if cfg.nfkc:\n        text = unicodedata.normalize("NFKC", text)\n    if cfg.lowercase:\n        text = text.lower()',
                   "if cfg.lowercase:\n        text = text.lower()"),
    "中文标点开关失效": (T, "if not cfg.strip_punct:", "if False:"),
    "拉丁串开关失效": (T, "elif cfg.latin_as_word and", "elif False and"),
    "英文大小写开关失效": (T, "text = text.casefold()", "text = text"),
    "英文撇号开关失效": (T, "(cfg.keep_apostrophe and c == \"'\")", "False"),
    # 分位数、相关系数、音素一致性
    "分位数差一位": (M, "k = max(1, math.ceil(p * len(xs)))", "k = max(1, math.floor(p * len(xs)))"),
    "Spearman 不取秩": (M, "return pearson(ranks(x), ranks(y))", "return pearson(x, y)"),
    "并列不取平均秩": (M, "avg = (k + e) / 2 + 1", "avg = k + 1"),
    "召回算成精确": (M, "return self.tp / (self.tp + self.fn) if self.tp + self.fn else None",
               "return self.tp / (self.tp + self.fp) if self.tp + self.fp else None"),
    "漏判不计漏检": (M, "            if not e.ok:\n                fn += 1", "            if not e.ok:\n                pass"),
    "音素对齐不优先相同标签": (M, "PHONE_SUB_COST = 3", "PHONE_SUB_COST = 1"),
    # 自助法
    "自助法种子不固定": (S, "rng = random.Random(seed)", "rng = random.Random()"),
    "区间上界取错分位": (S, "hi = vals[int(math.ceil((1 - alpha) * m)) - 1]", "hi = vals[-1]"),
    # 盲测
    "ASR 请求带参考文本": (V, "    item_id: str\n    audio: Audio\n    language: str\n\n\n@dataclass(frozen=True)\nclass AsrOutput:",
                     "    item_id: str\n    audio: Audio\n    language: str\n    reference_text: str = \"\"\n\n\n"
                     "@dataclass(frozen=True)\nclass AsrOutput:"),
    "提交顺序不随机": (B, 'random.Random(f"order:{seed}:{code}").shuffle(order)', "pass"),
    "报告用供应商 ID 代替编号": (RUN, "codes = blind.assign_codes([v.vendor_id for v in asr_vendors], a.blind_seed)",
                        "codes = {v.vendor_id: v.vendor_id for v in asr_vendors}"),
    # 清单校验
    "音频引用放行任意协议": (MF, 'AUDIO_REF = re.compile(r"^(synthetic|store)://', 'AUDIO_REF = re.compile(r"^[a-z]+://'),
    "合成标志与引用不核对": (MF, 'if synthetic != ref.startswith("synthetic://"):', "if False:"),
    "参考文本不扫个人信息": (MF, "hits = [name for name, rx in PII.items() if rx.search(ref)]", "hits = []"),
    # 报告与产物位置
    "报告带出转写原文": (R, '"ins": sum(c.ins for c in cs), "missing"',
                 '"ins": sum(c.ins for c in cs), "hyp": [outs[s.id].text for s in sub if s.id in outs], "missing"'),
    "合成样例不标注": (R, "notices = [SAMPLE_NOTICE] if manifest.synthetic else []", "notices = []"),
    "报告允许写进 gold": (R, 'if "gold" in parts or any(p.lower() == "gold" for p in d.parts):', "if False:"),
    "真实结果允许放在仓库里": (RUN, "if not m.synthetic and _inside(rd, ROOT):", "if False:"),
}
WORKERS = int(os.environ.get("VOICE_BLIND_MUTATION_WORKERS", "4"))
TEST_ENV = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}


def _copy(tmp: Path) -> Path:
    dst = tmp / "evals" / "voice-blind"  # 保持两层目录：run_blind.ROOT（仓库根）落在临时目录上
    shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "scripts"))
    return dst


def _pytest(d: Path) -> int:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=d, capture_output=True,
                       text=True, env=TEST_ENV)
    return r.returncode


def _one(name: str, rel: str, pattern: str, replacement: str) -> tuple[str, str | None]:
    with tempfile.TemporaryDirectory(prefix="voice-blind-mut.") as t:
        d = _copy(Path(t))
        target = d / rel
        src = target.read_text(encoding="utf-8")
        if src.count(pattern) != 1:
            return name, "变异本身写错（原文找不到或不唯一）"
        target.write_text(src.replace(pattern, replacement, 1), encoding="utf-8")
        rc = _pytest(d)
        if rc != 1:
            return name, "漏检" if rc == 0 else f"不是被断言发现（退出码 {rc}）"
        return name, None


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="voice-blind-mut-control.") as t:
        rc = _pytest(_copy(Path(t)))
        if rc != 0:
            print(f"voice-blind-mutation: 对照（未变异）没有通过（退出码 {rc}），变异结果不可信")
            return 1
    with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as ex:
        results = list(ex.map(lambda kv: _one(kv[0], *kv[1]), MUTATIONS.items()))
    missed = [(n, why) for n, why in results if why]
    for n, why in missed:
        print(f"  {why}：{n}")
    if missed:
        print(f"voice-blind-mutation: 失败，{len(missed)}/{len(MUTATIONS)} 个变异没有被发现")
        return 1
    print(f"voice-blind-mutation: 通过（{len(MUTATIONS)} 个变异全部被发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
