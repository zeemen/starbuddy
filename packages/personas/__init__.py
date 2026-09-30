"""人设治理组件（冲刺 3-F，specs/persona-governance-sprint3/spec.md）。

- registry：Prompt 登记表、指纹锁与 persona-registry-check；
- guard：禁语分类器（规则层 + 结构上下文层；模型判定层只登记、本冲刺不执行）。

只用 Python 标准库与 PyYAML；不联网、不读任何密钥。导入方式：把仓库的 packages/ 放进 sys.path，再 `import personas`。
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
REGISTRY_REL = "packages/personas/registry.yaml"
GUARD_VERSION = "0.1.0"


class PersonaConfigError(ValueError):
    """登记表、锁文件或禁语词表不合法（fail closed：不静默返回空结果）"""
