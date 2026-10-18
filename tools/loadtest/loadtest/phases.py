"""PLT-20 按阶段的汇总（纯函数，不碰网络）：阶段边界的墙钟、按边界切 BFF 访问日志（闸门排队分档）、
pg_stat_statements 快照相减（各阶段慢查询）、连接峰值，与 metrics.by_stage 合成报告的“各阶段”表。

归属口径：请求延迟、吞吐、错误率按**到达阶段**（同 metrics.by_stage）；连接峰值、慢查询、闸门分档按**墙钟**所在阶段
（采样时刻、边界快照、访问日志写入时刻），二者在阶段交界处可能差一个会话时长。报告里注明。
"""
from __future__ import annotations

from collections.abc import Sequence

from .dbstats import diff_statements
from .metrics import gate_summary


def boundaries(durations: Sequence[float]) -> list[float]:
    """各阶段的结束时刻（相对开始，秒）"""
    out, t = [], 0.0
    for d in durations:
        t += d
        out.append(t)
    return out


def stage_at(elapsed: float, durations: Sequence[float]) -> int:
    """墙钟时刻所在阶段；超过最后一个阶段的结束（收尾的会话）记在最后一个阶段"""
    for i, end in enumerate(boundaries(durations)):
        if elapsed < end:
            return i
    return len(durations) - 1


def _align(data: bytes, off: int, lo: int) -> int:
    """切点对齐到 off 之前最后一个换行之后（写了一半的行归下一段）"""
    end = min(max(off, lo), len(data))
    nl = data.rfind(b"\n", lo, end)
    return lo if nl < 0 else nl + 1


def split_log(data: bytes, marks: Sequence[int]) -> list[list[str]]:
    """按阶段边界记下的日志字节偏移切段：marks[0] = 开始时的文件大小，marks[i + 1] = 第 i 阶段结束时；
    共 len(marks) - 1 段，最后一段一直取到文件末尾（收尾的会话）。开始之前的行（启动日志）不算。"""
    n = len(marks) - 1
    if n < 1:
        return []
    cuts = [_align(data, marks[0], 0)]
    for i in range(1, n):
        cuts.append(_align(data, marks[i], cuts[-1]))
    cuts.append(len(data))
    return [data[cuts[i]:cuts[i + 1]].decode("utf-8", errors="replace").splitlines() for i in range(n)]


def assemble(stages: list[dict], labels: Sequence[str], concurrency: Sequence[int], activity_by_stage: dict | None,
             snaps: Sequence[dict] | None, gate_segments: Sequence[list[str]] | None, top: int = 3) -> list[dict]:
    """metrics.by_stage 的每一行 + 连接峰值 + 本阶段慢查询前 top + 闸门分档（只含汇总，不含任何 ID）"""
    out = []
    for s in stages:
        i = s["stage"]
        row = dict(s)
        row["label"] = labels[i] if i < len(labels) and labels[i] else f"{concurrency[i]} 并发"
        row["concurrency"] = concurrency[i]
        row["db_peaks"] = (activity_by_stage or {}).get(str(i), {})
        if snaps is not None and len(snaps) > i + 1:
            row["top_queries"] = diff_statements(snaps[i], snaps[i + 1], top)
        else:
            row["top_queries"] = []
        row["gate"] = gate_summary(gate_segments[i]) if gate_segments is not None and i < len(gate_segments) else {}
        out.append(row)
    return out
