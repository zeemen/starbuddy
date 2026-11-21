# Python 服务日志字段补齐与静态检查 · 实现记录

- 2026-11-21 用户“按建议，默认值都同意，开始实现”：能用已登记字段的改写法，只为 `caller`、`node_id`、`cognitive_level` 扩白名单；
  安全负责人知情；检查放进 `make check-fast` 与 CI 契约组。

## 做了什么
- **改写法**（不扩白名单）：
  - 洞察服务 `service/tools.py`：授权核对函数不可用的那一行改为 `{"event": "actor_verifier_unavailable", "error_class": …}`；
  - 审核台 `review_console/app.py`：响应校验失败的出错位置从 `errors` 改放进 `detail`；
  - 学习服务 `model_router/ledger.py`：写库失败丢的行数从 `rows` 改放进 `items`，失败的去向从 `sink` 改放进 `detail`。
- **扩白名单**：`caller`、`node_id`、`cognitive_level` 加进 BFF `log.ts` 与三份 `obs.py`（四份逐项相同）。
- **静态检查** `tools/log-field-check/check.py`：解析（不导入）`learning/obs.py` 的 `LOG_FIELDS`，扫学习、审核台、洞察、知识、调度器的源码：
  日志调用里 `json.dumps({...})` 的键；同一函数里经 `log.<级别>(json.dumps(record))` 写出的 `record` / `rec` 字典的键。
  带自检（8 个反例、8 个正例）。接进 `make check-fast` 与 `CHECK_GROUP_CONTRACTS`（CI 契约组）。
  退回改动前的代码跑一遍，9 处全部报出（spec 列的 7 类，`caller` 占 3 处）。
- **测试**：学习服务 `tests/test_obs.py` 两条（新字段与改写后的几处经真实 `JsonFormatter` 保留；`_unit_log` 带节点与层级）；
  洞察服务 `tests/test_obs.py` 两条（`actor_verifier_unavailable` 有事件名、不带学生 ID；工具调用日志保留 `caller`）。

## 与 spec 的差异
- 检查的 `record` / `rec` 规则收窄为“同一函数里确实经日志写出”：第一版把知识服务 `atoms.py` 里同名的快照字典也当成日志记录，误报一处。
  收窄后加了一条对应的正例。

## 验证
- 本机六项全部通过：`make check-fast`（含新的 `log-field-check`）、`make deploy-check`（四份白名单逐项相同）、`make bff-test`、
  `make learning-test`、`make insight-test`、`make knowledge-test`。

## 待人确认
1. **安全负责人知情**：日志白名单（安全审查 B）多了 `caller`（调用方枚举）、`node_id`（公共知识层节点编码，不是个人数据）、
   `cognitive_level`（四个固定取值）。`node_id` 只出现在掌握重算单元失败的告警里，同一行没有学生标识。
2. 检查扫不到动态拼出的键（例如 `record.update(**fields)`、学习服务 `plan_today` 返回的 `fields`）；这几处的键来自代码里的固定集合，目前都在白名单里，
   以后改动时请留意。

## 合并前契约检查的两条建议（2026-11-21，用户“按建议，两条建议都做”）
1. **BFF 的学生散列与知识点不同行**：`services/bff/src/observability/log.ts` 的 `logFields` 末尾加一条规则：一行里有 `student` 或 `account`
   （散列）时，丢掉 `node_id`、`cognitive_level`（`NOT_WITH_PERSON`）。这样以后即使有人在同一行写出“学生散列 + 知识点”，生产日志里也只剩散列，
   个人层的学习事实不进日志（规则 13）。测试：`tests/gate-log-fields.test.ts` 新增两条（这条规则本身；BFF 直接调用 `emit` / `#warn` / `warning:`
   写的字典字面量的键都在白名单里）；变异“闸门日志：学生散列与知识点同行”被发现（这一组 4 条全部被发现）。
2. **`node_name` 的兼容口径**：`openapi/practice.v0.yaml`、`retest.v0.yaml` 的字段说明写明“本版 BFF 一定带这个键（可为空），更早的 BFF 不带，
   客户端须把缺键当作空处理”。前端单测补“缺键”的用例（再练；复测原有用例的桩本来就不带这个键，补了注释）。
- 验证：前端单测 273 项、BFF 相关四个测试文件 45 项、`make check-fast` 通过。
