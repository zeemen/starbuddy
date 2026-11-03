"""冲刺 5 压测基线、单模型失效降级与成本测算（specs/load-cost-baseline-sprint5/spec.md）。

只连本机一次性栈（规则 9），只用合成数据（规则 8）；不引入新依赖（asyncio + httpx + asyncpg，仓库已有）。
报告只含汇总；供应商与模型一律用合成代号，不写模型标识。
只读复用：tools/e2e-cohort（合成队列、适配器编排、内容登记）、services/learning/tests/pgharness.py（一次性 Postgres）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT / "tools/e2e-cohort", _ROOT / "services/learning/tests", _ROOT / "services/learning"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))
