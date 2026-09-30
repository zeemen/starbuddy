"""KGE-04 掌握 v0 的纯函数部分（specs/mastery-estimate-sprint4/spec.md，第一步）。

不连库、不接服务接口：配置加载与校验（config）、证据分类（evidence）、简化 BKT 与衰减（bkt）、
五道门槛重放状态机与解释（gates）、写入判定与写库前自检（write）、档案页陈述—判断一致性检查（consistency）。
库表、学习服务接入、mastery.get / explain 接口与档案页编译是第二步（见 lessons.md）。
代码与配置里不含任何教研签字数值：配置目录 services/learning/config/mastery/ 只有草案骨架（数值留空）与合成占位配置。
"""
from .config import ALGORITHM_VERSION, CONFIG_DIR, ConfigError, MasteryConfig, load_config, load_config_from_env, parse_config
from .evidence import Classification, Evidence, Unit, classify
from .gates import GATES, Replay, UnitResult, explain_gates, replay, star_lit
from .write import EvidenceFacts, InvariantViolation, StoredEstimate, precommit_check, write_decision

__all__ = ["ALGORITHM_VERSION", "CONFIG_DIR", "ConfigError", "MasteryConfig", "load_config", "load_config_from_env",
           "parse_config", "Classification", "Evidence", "Unit", "classify", "GATES", "Replay", "UnitResult",
           "explain_gates", "replay", "star_lit", "EvidenceFacts", "InvariantViolation", "StoredEstimate",
           "precommit_check", "write_decision"]
