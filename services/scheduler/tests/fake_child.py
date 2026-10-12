"""运行器测试用的假子进程（python -m fake_child <动作> ...）：不连库，只按参数打印与退出。全部是合成输出。

  env              打印自己的环境变量名（JSON：{"env": [...], "path_is_fixed": PATH 是否是固定值}）
  exit <N>         打印一行计数后以 N 退出
  sleep <秒>       睡眠后打印计数
  stale            打印 stale_estimate > 0 的计数，退出码 1（同 learning.jobs mastery-sweep）
  garbage          最后一行不是 JSON
  leak             计数里混进像 UUID 的字符串与长文本（日志清洗要丢掉）
  leakerr          标准错误带 UUID，退出码 2（日志里的错误摘要要抹掉）
  signal           被自己 SIGKILL
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time


def main(argv: list[str]) -> int:
    act = argv[0]
    if act == "env":
        print(json.dumps({"env": sorted(os.environ), "path_is_fixed": os.environ.get("PATH") == "/usr/bin:/bin"}))
        return 0
    if act == "exit":
        print(json.dumps({"job": "fake", "n": 1}))
        return int(argv[1])
    if act == "sleep":
        time.sleep(float(argv[1]))
        print(json.dumps({"job": "fake", "slept": float(argv[1])}))
        return 0
    if act == "stale":
        print(json.dumps({"job": "fake", "failed_units": 0, "stale_estimate": 2}))
        return 1
    if act == "garbage":
        print("not json at all")
        return 0
    if act == "leak":
        print(json.dumps({"job": "fake", "n": 3, "student": "c1000000-0000-4000-8000-0000000000c1", "note": "x" * 200,
                          "nested": {"ok": 1, "ids": ["c1000000-0000-4000-8000-0000000000c1", "aya"]}, "BadKey": 1}))
        print("c1000000-0000-4000-8000-0000000000c1 学生", file=sys.stderr)
        return 0
    if act == "leakerr":
        print("执行失败 c1000000-0000-4000-8000-0000000000c1", file=sys.stderr)
        return 2
    if act == "signal":
        os.kill(os.getpid(), signal.SIGKILL)
    return 9


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
