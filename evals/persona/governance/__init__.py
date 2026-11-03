"""人设治理回归（冲刺 3-F，specs/persona-governance-sprint3/spec.md）：七类家族级检查、长对话漂移、N16 泄露探针、录制回放与报告。

在 evals/persona 目录下运行：python -m governance run …。只用标准库、PyYAML 与 jsonschema；不联网、不读密钥、不调用模型。
"""

import sys as _sys
from pathlib import Path as _Path

# 包里的模块都依赖 packages/personas：先把本份代码所在仓库的 packages/ 放进 sys.path（与 scorer/judge.py 相同的接入方式）
_CODE_ROOT = _Path(__file__).resolve().parents[3]
for _p in (_CODE_ROOT / "packages", _Path(__file__).resolve().parents[1]):
    if str(_p) not in _sys.path:
        _sys.path.insert(0, str(_p))
