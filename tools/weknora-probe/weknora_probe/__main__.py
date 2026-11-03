"""命令行：对 WeKnora 测试库运行技术验证探针，输出 Markdown + JSON 报告。

    cd tools/weknora-probe && python -m weknora_probe --out <报告目录> [--probes V03,V07] [--keep]

环境变量（地址与密钥由数据开发团队发放，只限测试库；不写进仓库）：
    WEKNORA_TARGET=test           必须，防止误对生产库运行
    WEKNORA_BASE_URL              WeKnora 地址（只开内网）
    WEKNORA_API_KEY               测试库的写入 + 检索密钥
    WEKNORA_KB_ID                 测试库 ID
    WEKNORA_RETRIEVE_API_KEY      可选：只有检索能力的密钥（V03）
    WEKNORA_OTHER_KB_ID           可选：只读密钥不应能访问的另一个库（V03）
    WEKNORA_PARTITION_KB_IDS      可选：逗号分隔，至少 2 个测试库（V07 的按维度分库做法）
    WEKNORA_INDEX_TIMEOUT         可选：等待建索引的秒数，默认 120
退出码：0 没有失败项；1 有 fail / error；2 参数或环境错误。
报告不含地址、密钥、正文与查询原文。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .client import ENV_TARGET, ConfigError, WeKnoraClient
from .probes import KEY_PROBES, PROBES, ProbeContext, ProbeResult, canary_scan_sql, run

REPORT_FORMAT = "weknora-probe-report/1"
STATUS_LABEL = {"pass": "通过", "fail": "不通过", "conditional": "有条件通过", "manual": "待人工核对", "error": "出错",
                "skipped": "跳过"}


def context_from_env(env: Mapping[str, str], *, keep: bool = False) -> ProbeContext:
    client = WeKnoraClient.from_env(env)
    kb = env.get("WEKNORA_KB_ID", "").strip()
    if not kb:
        raise ConfigError("缺少 WEKNORA_KB_ID（测试库 ID）")
    retrieve = WeKnoraClient.from_env(env, key_var="WEKNORA_RETRIEVE_API_KEY") if env.get("WEKNORA_RETRIEVE_API_KEY") else None
    parts = tuple(x.strip() for x in env.get("WEKNORA_PARTITION_KB_IDS", "").split(",") if x.strip())
    try:
        timeout = float(env.get("WEKNORA_INDEX_TIMEOUT", "120"))
    except ValueError:
        raise ConfigError("WEKNORA_INDEX_TIMEOUT 必须是秒数") from None
    return ProbeContext(client, kb, retrieve_client=retrieve, other_kb_id=env.get("WEKNORA_OTHER_KB_ID") or None,
                        partition_kb_ids=parts, index_timeout=timeout, keep=keep)


def build_report(results: Sequence[ProbeResult], cleanup: Mapping[str, int], run_id: str) -> dict[str, Any]:
    counts = {s: sum(1 for r in results if r.status == s) for s in STATUS_LABEL}
    stop = [r.id for r in results if r.id in KEY_PROBES and r.status in ("fail", "error")]
    return {"format": REPORT_FORMAT, "run_id": run_id, "target": "test", "counts": counts, "cleanup": dict(cleanup),
            "stop_for_user": stop,
            "results": [{"id": r.id, "title": r.title, "status": r.status, "summary": r.summary, "evidence": r.evidence}
                        for r in results]}


def render_markdown(rep: Mapping[str, Any]) -> str:
    lines = [f"# WeKnora 技术验证探针报告（运行 {rep['run_id']}）", "",
             "> 只对测试知识库运行，只写合成数据；报告不含地址、密钥、正文与查询原文。结论汇入 weknora-validation-report.md，由人确认。", ""]
    if rep["stop_for_user"]:
        lines += [f"> **关键项不通过：{'、'.join(rep['stop_for_user'])}。按计划先停下，交用户决定。**", ""]
    c = rep["counts"]
    lines += ["- 汇总：" + "；".join(f"{STATUS_LABEL[k]} {v}" for k, v in c.items() if v), f"- 清理：{rep['cleanup']}", "",
              "| 项 | 名称 | 结论 | 说明 |", "|---|---|---|---|"]
    for r in rep["results"]:
        lines.append(f"| {r['id']} | {r['title']} | {STATUS_LABEL[r['status']]} | {r['summary']} |")
    lines += ["", "## 证据", ""]
    for r in rep["results"]:
        lines += [f"### {r['id']} {r['title']}", "", "```json", json.dumps(r["evidence"], ensure_ascii=False, indent=2, sort_keys=True),
                  "```", ""]
    return "\n".join(lines)


def write_report(rep: Mapping[str, Any], out: Path, results: Sequence[ProbeResult]) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / "weknora-probe.md", out / "weknora-probe.json"
    md.write_text(render_markdown(rep), encoding="utf-8")
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths = [md, js]
    for r in results:
        if r.id == "V17" and "canary" in r.evidence:
            sql = out / "v17-canary-scan.sql"
            sql.write_text(canary_scan_sql(r.evidence["canary"]), encoding="utf-8")
            paths.append(sql)
    return paths


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WeKnora 技术验证探针（只对测试库）")
    ap.add_argument("--out", required=True, help="报告目录")
    ap.add_argument("--probes", default=",".join(PROBES), help=f"逗号分隔，可选：{','.join(PROBES)}")
    ap.add_argument("--keep", action="store_true", help="不删除本次写入的合成文档（便于人工查看）")
    args = ap.parse_args(argv)
    ids = [p.strip().upper() for p in args.probes.split(",") if p.strip()]
    unknown = [p for p in ids if p not in PROBES]
    if unknown or not ids:
        print(f"未知的探针：{'、'.join(unknown) or '（空）'}", file=sys.stderr)
        return 2
    try:
        ctx = context_from_env(os.environ if env is None else env, keep=args.keep)
    except ConfigError as e:
        print(f"探针无法运行：{e}", file=sys.stderr)
        return 2
    results, cleanup = run(ctx, ids)
    rep = build_report(results, cleanup, ctx.run_id)
    paths = write_report(rep, Path(args.out), results)
    print("已生成：" + "、".join(p.name for p in paths))
    if rep["stop_for_user"]:
        print(f"关键项不通过：{'、'.join(rep['stop_for_user'])}，按计划先停下交用户决定", file=sys.stderr)
    return 1 if (rep["counts"]["fail"] or rep["counts"]["error"]) else 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "context_from_env", "build_report", "render_markdown", "ENV_TARGET"]
