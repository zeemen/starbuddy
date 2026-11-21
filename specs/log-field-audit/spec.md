# Python 服务日志字段被白名单丢掉的几处，补齐并加静态检查 · 规格草稿

- 状态：**草稿（2026-11-21），等人确认第五节**。
- 来源：`specs/gate-log-fields/` 发现 BFF 的闸门字段在生产日志里被白名单整项丢掉；同一类问题按同样办法查了学习、洞察、知识三个 Python 服务。
- 涉及安全审查 B（日志字段白名单）：只加取值是固定枚举或公共知识层编码的字段，不加学生、账号与内容；请安全负责人知情（待人确认第 2 条）。
  不涉及 CLAUDE.md 的必停项。

## 一、现状（2026-11-21 核对）
Python 服务的日志写法是 `log.xxx(json.dumps({...}))`，`JsonFormatter` 解析后经 `log_fields` 只留 `LOG_FIELDS` 里的顶层键，其余整项丢掉。
用脚本扫了三个服务里所有写进日志的字典字面量与 `record` / `rec` 字典的键（BFF 的 `emit` 与 `note` 已由 `tests/gate-log-fields.test.ts` 覆盖，没有问题），
下面 7 处会被丢：

| 键 | 位置 | 后果 |
|---|---|---|
| `caller` | 洞察服务 `service/tools.py:206`、`service/monthly_route.py:65`；知识服务 `service/tools.py:89` | 工具调用日志的文件头写明“只记 request_id、caller、工具名……”，实际**看不到是哪个调用方**（bff / learning / review） |
| `actor_verifier` | 洞察服务 `service/tools.py:137` | 这一行**没有 `event`**，丢掉之后只剩 `error_class`，看不出是授权核对函数不可用 |
| `node_id`、`cognitive_level` | 学习服务 `mastery/recompute.py:243`（`_unit_log`，掌握重算单元失败的告警） | 看不出是哪个知识点、哪一层出的错 |
| `errors` | 学习服务 `review_console/app.py:357`（审核台响应校验失败） | 看不出是哪个字段不合规（内容是字段路径与错误类型，不含取值） |
| `rows` | 学习服务 `model_router/ledger.py:274`（模型调用记账写库失败） | 看不出丢了几行 |
| `sink` | 学习服务 `model_router/ledger.py:99`（记账去向失败） | 看不出是哪个去向 |

## 二、默认做法
1. **能用已登记字段的，改用已登记字段**（不扩白名单）：
   - `actor_verifier` 那一行改成 `{"event": "actor_verifier_unavailable", "error_class": …}`；
   - 审核台的 `errors` 改放进已登记的 `detail`；
   - 记账的 `rows` 改放进已登记的 `items`；`sink` 改放进已登记的 `detail`。
2. **白名单加三个字段**：`caller`（取值是代码里的调用方枚举）、`node_id`（公共知识层节点编码，不是个人数据）、`cognitive_level`（四个固定取值）。
   BFF 与三份 `obs.py` 同步（`deploy/tests/test_obs_shared.py` 核对四份逐项相同）。
3. **静态检查防再犯**：新增 `tools/log-field-check/check.py`（照 `tools/path-url-check` 的写法，带自检反例），扫三个 Python 服务：
   - `log.<级别>(json.dumps({...}))` 字典字面量的键；
   - 名为 `record` / `rec` 的字典：字面量初始化、`record["键"] = …`、`record.update(键=…)`；
   - 键必须在 `LOG_FIELDS` 里，否则报出文件与行号。接进 `make check-fast` 与 CI 的契约组。
   动态拼出来的键（例如 `record.update(**fields)`）扫不到，在检查的说明里写明。

## 三、测试
- 检查脚本自检：反例（未登记的键）被拦、正例放行。
- 改了日志写法的几处：各服务已有测试照常通过；洞察服务的 `actor_verifier_unavailable` 与工具调用日志带 `caller`、学习服务的 `_unit_log`
  各加一条断言（日志经真实 `JsonFormatter` 输出后有这些字段）。
- `make check-fast`、`deploy-check`、`learning-test`、`insight-test`、`knowledge-test`、`bff-test`（BFF 的 `LOG_FIELDS` 同步改）。

## 四、不做
- 动态键的运行时检查；日志级别、采集与留存；IaC 日志索引（不改）。

## 五、待人确认（括号里是默认）
1. **处理方式**：能用已登记字段的改写法，只为 `caller`、`node_id`、`cognitive_level` 扩白名单（默认）；还是全部扩白名单、不改写法？
2. **安全负责人知情**：白名单多三个字段，其中 `node_id` 是公共知识层编码（不是个人数据）（默认知情即可）；还是先过一轮安全审查？
3. **检查放哪一组**：`make check-fast` 与 CI 契约组（默认，同 `path-url-check`）；还是只放 CI？
