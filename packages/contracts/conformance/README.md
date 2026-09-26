# 契约一致性测试（conformance）

与学科无关的一致性测试：对任何一科正在运行的适配器，跑同一套测试。
规格见 `specs/contract-conformance-sprint1/spec.md`，实现记录见同目录的 `lessons.md`。

- **用例从契约生成**：schema、Problem 格式、字段 pattern 都直接取自 `openapi/plugin-api.v0.9.yaml`。
- **期望来自消费方**：平台侧核对直接调用共享库 `packages/contracts/python/starbuddy_contract/checks.py`，学习服务落库前用的是同一份代码。
- **各科只提供合成夹具**：写输入和期望的编码，不写期望的完整结构。

| 类别 | 内容 |
|---|---|
| 能力声明 | 符合 schema；学科、契约版本一致；声明已实现的方法，夹具里必须有用例 |
| 服务间令牌 | 不带令牌时返回 401 或 403 |
| 未实现的方法 | 返回 501 `METHOD_NOT_IMPLEMENTED` |
| 坏请求 | 按契约自动变出：缺幂等键、缺计量 ID、契约版本不对、学科不对、`data_class` 为 L0 或 L1、`student_ref` 不是 UUID、多出字段。都必须得到 4xx Problem（content-type 为 `application/problem+json`），错误体不回显学生作答（原文和 `\uXXXX` 转义都查） |
| 每条夹具 | 响应符合 schema，通过平台侧核对，且不比平台判定更松；verdict、复核原因、错因大类、观测结论、置信度上限与夹具一致；不泄露 |
| 幂等 | 同 key、同请求体重试，证据 ID 不变（异步时 job_id 也不变）；换 `request_id` 也不变；换了请求体返回 409 Problem，且不回显作答 |
| 异步 | 适配器返回 202 时，按契约校验 JobAccepted，轮询 `/jobs/{job_id}`，校验 JobStatus，再用任务结果跑上面的检查 |

## 文件
| 文件 | 说明 |
|---|---|
| `cli.py` | 入口：启动被测适配器（随机端口、随机令牌）或参考假适配器，跑测试，写覆盖率报告 |
| `test_conformance.py` | 测试本体 |
| `conftest.py` | 命令行选项、会话级夹具、覆盖率报告（JSON） |
| `support.py` | 夹具加载、请求组装、HTTP 调用（含异步轮询）、泄露检查（HTTP 只用标准库；夹具用 PyYAML） |
| `reference_adapter.py` | 参考假适配器：扮演任何学科，同步或异步，可注入 28 类违约 |
| `mutation.py` | 变异测试：每类违约注入后，一致性测试都必须失败，而且必须是指定的那个测试失败 |
| `fixtures/reference.yaml` | 参考假适配器的夹具（学科写成 zhixing，证明与学科无关） |

各科夹具放在 `adapters/<学科>/conformance/fixtures.yaml`，格式见阿鸦的那份。

## 运行
```bash
make conformance PYTHON=<装了 packages/contracts/requirements-dev.txt 的解释器>
make conformance-mutation PYTHON=...
# 单独测某科：
python packages/contracts/conformance/cli.py --adapter-cmd "node --no-warnings src/main.ts" --adapter-cwd adapters/aya \
    --token-var AYA_ADAPTER_SERVICE_TOKEN --fixtures adapters/aya/conformance/fixtures.yaml --report /tmp/aya.json
```
覆盖率报告示例：`{"subject": "aya", "implemented": 2, "passed": 2, "of": 6, "methods": {...}}`。Gate 0 的契约覆盖率按这个统计（`/gate-check 0`）。

## 检查边界（审查时请注意）
- **平台侧核对与泄露检查只覆盖 grade、diagnose。** 其他四个方法目前只做 schema、坏请求、幂等。共享库对其他方法会明确报“未实现”，不会静默放行。
- **泄露检查是结构白名单**，所以：
  - teach 的讲解文本这类自由文本字段不在范围内；
  - OCR 和 ASR 转写文本只检查位置，不逐字比对；
  - 数字只允许出现在白名单字段里，并且和夹具的秘密逐一比较；
  - 字符串秘密 3 个字符以上按子串查，更短的只按完全相等查，避免和 UUID 偶然重合。所以单字符答案只能查到完全相等的情况。
- **以下是本测试的约定，契约里没有明写**，已列入 v1.0 评审清单 N13：
  - 未实现的方法返回 501；
  - `request_id` 不计入请求体比较；
  - `student_ref` 必须是 UUID；
  - 错误体的 content-type。
- **比契约更严的一处**：要求适配器的准入状态不能比平台登记更松。平台本来会改写，但登记数据来自该科自己，适配器不应依赖平台兜底。
- **同 key 换请求体**：有作答的方法改 `elapsed_sec`；其他方法要在夹具里提供 `conflict_body`，否则测试明确失败并提示补夹具。
- 覆盖率报告里，只要有任何全局用例失败（例如不校验令牌、契约版本不对），所有方法都记为未通过。

## 夹具由谁写
按执行方案第六章，契约测试不能由写适配器的同一个会话手写：
- 通用用例全部从契约生成；
- 学科夹具由另一个会话或该科负责人编写，并由教研抽查期望的 verdict。
