# 执行手册：预发压测与真实成本重算

对应 `specs/load-cost-baseline-sprint5/spec.md` 设计要点 4。**本仓 CI 不执行本手册**；本机基线见 `README.md`。
任何一步不满足前提就停止。本手册不给出任何环境地址、凭据或连接串。

## 1. 预发压测

### 1.1 前提（缺一不可）

| # | 条件 | 负责人 | 记录 |
|---|---|---|---|
| 1 | 技术负责人书面批准本次预发压测（目的、时间窗、规模上限、执行人） | 技术负责人 | 批准编号 |
| 2 | 预发环境与生产**网络隔离**、独立数据库实例、独立供应商账号；压测期间不与任何生产系统互通（规则 9） | 平台负责人 | 环境名 |
| 3 | 预发库只有合成数据（本工具的合成家庭，+999 号码，synthetic-0 同意文本）；**没有任何真实学生数据**（规则 8） | DBA | 核对记录 |
| 4 | 模型供应商：只用假供应商，或已在白名单登记、签字的境内供应商的**测试账号**，且额度上限已设（新第三方服务是必停项） | 合规 / 平台 | 登记编号 |
| 5 | 性能通过线（如核心接口 p95、批改异步时限）已由技术负责人签字（spec 待人确认 #1）；没有通过线时只出基线 | 技术负责人 | 签字值 |
| 6 | 计量：只读断言，不改额度与规则；预发的额度配置是测试副本（`loadtest-plan@1` 同形），不是已发布给用户的配置 | 产品 / 平台 | 配置版本 |
| 7 | 监控已接好（服务与数据库的 CPU、内存、连接数、错误率），有人值守 | 平台 | 值守人 |

### 1.2 步骤

1. 在执行机（受控设备）拉取与预发部署**同一提交**，记下提交号；
2. 先在本机跑一次同参数的小规模基线（`make loadtest-baseline`），确认工具与提交可用；
3. 预发不使用 `stack.py`（它只起本机一次性栈）：由平台负责人按预发部署方式起服务，执行人只运行负载驱动部分，
   目标地址经环境变量传入（**不写进仓库、不写进报告**）。驱动器与预发之间的适配（地址与服务间令牌来源）另起任务实现，
   本任务不提供连接预发的代码；
4. 分阶段加压：10 → 50 → 100 → 200 并发会话，每阶段 ≥ 120 秒，阶段之间观察 1 分钟；
5. 结束后跑计量零泄漏核对（快进时钟只在测试库可行；预发改为等待预留有效期过后由定时任务回收，再核对对账视图为零行）；
6. 报告只保留汇总（同本机报告），首行标“预发基线，不代表生产容量”，存入受控目录；不入仓库。

### 1.3 停止条件（任一出现立即停止加压并回收）

- 错误率（非 2xx 且非 402）连续 30 秒 > 5%，或出现任何 5xx 以外的数据错误（约束冲突、对账视图非零）；
- 数据库连接数 ≥ 上限的 90%，或锁等待持续 > 10 秒；
- 供应商侧出现非预期计费、限流告警，或出现任何发往未登记供应商 / 境外地址的请求；
- 预发与生产之间出现任何流量，或发现真实学生数据；
- 值守人或技术负责人要求停止。

停止后记录停止时刻、阶段、现象，登记到 `specs/load-cost-baseline-sprint5/lessons.md`（不含地址与数据）。

## 2. 故障演练（预发）

与 1 同样的前提。只在假供应商上注入（`fake_provider.py` 的 `/_control/faults`）；**不对真实供应商做任何故障注入**。
期望行为以 `loadtest/degradation.py` 的清单为准；清单外的行为登记待人确认。

## 3. 真实成本重算的数据导出口径

前提：试点已上线且有真实会话；签字的供应商与价格已由人填入价格表（`config/prices.example.yaml` 复制为正式价格表，`example: false`）。

**只导出调用记录的元数据**（模型路由的调用记录 `CallRecord`，`learning.model_router.ledger`）：

| 字段 | 导出 | 说明 |
|---|---|---|
| subject、task、data_class、outcome | 是 | 平台编码 |
| input_tokens、output_tokens | 是 | 供应商返回的计数（不是估算） |
| elapsed_ms、attempts 的个数 | 是 | 只要个数，不要尝试明细 |
| 会员档 | 是 | 由计量侧按 usage_ref 关联后只留档位编码（关联在受控环境内完成，导出不带 usage_ref） |
| model_version、reported_model | **否** | 成本按任务与价格表代号计；报告不写模型标识 |
| usage_ref、idempotency_ref、source_version | **否** | 关联用摘要，不出受控环境 |
| 任何提示词、对话、作答、输出、学生 / 家庭 ID | **绝不** | 调用记录本来就不含；导出脚本也不得关联回来 |

导出为 JSON Lines（字段同 `sample/call-records.jsonl`，可另加 `tier`），放执行机受控目录；然后：

```bash
make cost-report COST_RECORDS=<导出文件> COST_OUT=<受控目录>     # 价格表与画像用 python -m loadtest cost --prices/--profile 指定
```

- 使用分布：试点数据到位后，用各档实际使用率分布替换 `cost_profile.yaml` 的 30% / 60% / 100% 三档与权重（spec 待人确认 #5）；
- 报告首行改为“真实会话测算（试点，时间窗 …）”由人确认后再对外；导出文件用完即删。

## 4. 成本仪表盘（PLT-17）：试点消耗聚合行的只读导出

对应 `specs/cost-dashboard-plt17/spec.md` 范围 3。**本仓 CI 不执行本节，也没有连接任何数据库的代码**；本仓只有合成管线
（`make cost-dashboard-test`；样例 `sample/cost-dashboard.md`，标“合成，不是试点结论；价格为示例价”）。

> **在下面两件事都完成之前，不得做任何真实导出：**
> 1. 只读导出函数（草案 [`sql/cost-usage-export.draft.sql`](sql/cost-usage-export.draft.sql)，**不是迁移**）已由另起的 [NEEDS-HUMAN] PR
>    落为迁移、附 RLS / 权限测试、合并并经人执行；
> 2. 工具的 synthetic 守卫已由另起任务、经人确认放开（当前版本只接受 `synthetic=true` 且伪名为 `syn-` 开头的消耗表）。
>
> 在那之前，流程演练**只用合成数据**：`python -m loadtest dashboard-synth --out <目录>` 生成输入，再 `make cost-dashboard`。
> 模型调用日志的落库（`model_call_log`）另起任务；在那之前调用记录按第 3 节从结构化日志导出元数据。

### 4.1 前提（缺一不可，任何一项不满足就停止）

| # | 条件 | 负责人 | 记录 |
|---|---|---|---|
| 1 | 试点学生的监护人同意已签署，用途包含成本与服务改进的统计（`platform.consent_record` 的 `pilot_participation`） | 合规 / 产品 | 授权记录编号 |
| 2 | 试点环境在境内、已完成部署评审；只连试点库，**不连生产**（规则 9） | 平台负责人 | 环境名 |
| 3 | 学科分摊口径与未归因消耗的计入方式（D5）、最小组人数与取整粒度已由产品确认（现在是占位值，见 `config/cost_dashboard.yaml`） | 产品 | 确认记录 |
| 4 | 价格表：签字价格已由人填入（D7），并写 `example: false` 与 `signoff_version`；缺任一项报表照写“价格为示例价” | 产品 / 合规 | 价格表版本 |
| 5 | 导出函数已按上文第 1 件事合并执行；属主是专用 NOLOGIN 角色（非 BYPASSRLS、只有所需列的 SELECT 与逐表策略），EXECUTE 只授给专用只读导出角色（不是 `starbuddy_report`，也不是任何应用角色） | DBA + 平台负责人 | PR 编号 |
| 6 | 工具的 synthetic 守卫已按上文第 2 件事放开 | 平台负责人 | PR 编号 |
| 7 | 执行机是试点环境内的受控设备；导出文件与输出目录不在任何同步盘或仓库里；两人在场（一人执行、一人核对） | 平台负责人指定 | 姓名不写进任何产物 |

### 4.2 口径（详见 SQL 草案注释；均待人确认）

- **窗口**：每名学生导出时主订阅（与 `platform.meter_main_subscription` 同口径，**排除加购订阅** `source = 'addon'`）的**当前计费周期**
  （滚动 30 天，导出时可能未满，与自然月不对齐；`month` 列是周期开始的月份）。不导出更早的周期。
- **档位为导出时档位，期内升降档未拆分**：`subscription.tier` 是当前档位（升级在原行上改、没有历史），升级前的消耗也算在升级后的档位上。
  报表首段同样写明。建议在周期接近结束、续期之前导出，使用率才接近整周期。
- **额度配置**：主订阅的 `config_version` 必须等于传入的 `config_version`，否则函数报错停止（不混算两种额度）。
- 消耗 = 结算流水 + 更正这些结算的反向流水；授予、预留、释放、过期、调整不算。
- 科目：结算流水的 `source_method` 为 grade / diagnose 时经 `source_idempotency_key` 关联 `platform.ingest_request.subject`（同一学生）；
  teach、上传、存储、深度报告、分钟类资源与关联不上的一律 `unattributed`。可归因的结算一条都没归上时函数报错停止。
- 名单与伪名同 `tools/kge-calibrate/RUNBOOK.md` 第 1 节：试点参与同意（且所签文档版本在白名单里）、排除已删除与删除请求进行中；
  名单为空时函数报错停止；salt 库内现场生成，伪名不可跨次对齐。
- 调用记录：真实日志（`event=model_call`）只有尝试明细的个数，**没有“是否回退”字段**，导出里不带 `fallback`，
  报表的回退占比写“记录不可得”；“多次尝试占比”含重试后仍失败的调用，与失败占比重叠。`tier` 是与成本报告共享格式的保留字段，仪表盘不用。

### 4.3 导出与运行（只在 4.1 全部满足后；连接串只从环境变量读，不写命令行、不进日志）

两份输入都放在同一个 `$WORK` 里，脚本退出时（含出错）整体清理：

```bash
umask 077
WORK=$(mktemp -d)                                   # 受控设备上的临时目录，不在仓库与同步盘里
trap 'rm -rf "$WORK"' EXIT                          # 个人层数据：无论成败都清理（输出另放受控目录 $OUT）
# 1) 消耗聚合行（只读函数；窗口为当前计费周期，不收日期参数）
psql -X -q -v ON_ERROR_STOP=1 -v config_version="$QUOTA_CONFIG" -v consent_versions="$CONSENT_VERSIONS" \
     -f tools/loadtest/sql/cost-usage-export.readonly.sql -o "$WORK/body.csv"
{ printf '# format=cost-dashboard-usage/1 synthetic=false quota_config=%s\n' "$QUOTA_CONFIG"; cat "$WORK/body.csv"; } > "$WORK/usage.csv"
# 2) 调用记录（第 3 节的字段表；同一计费周期的学习服务结构化日志 $LEARNING_LOG，只取元数据、尝试只取个数）
grep '"model_call"' "$LEARNING_LOG" | sed 's/^[^{]*//' \
  | jq -c 'select(.event == "model_call") | {subject, task, data_class, outcome, input_tokens, output_tokens, elapsed_ms,
           attempts: (.attempts | length)}' > "$WORK/call-records.jsonl"
# 3) 汇总：非合成输入必须给输出目录（目录须不存在或为空；文件 0600 新建）
make cost-dashboard DASH_USAGE="$WORK/usage.csv" DASH_RECORDS="$WORK/call-records.jsonl" DASH_OUT="$OUT"
```

`QUOTA_CONFIG` 必须与 `config/cost_profile.yaml` 的 `quota_config` 相同（试点发布的额度配置不同时，先由人改画像再跑）；
`CONSENT_VERSIONS` 是合规给出的同意文档版本白名单（数组字面量）。元数据行的 `synthetic=false` 在守卫放开之前会被拒绝（退出码 2）。
调用记录里不得出现 `model_version`、`reported_model`、`usage_ref`、`idempotency_ref`、`source_version`（工具会拒绝）。

**导出频率与留痕**：同一试点环境每个计费周期至多导出一次；每次导出在受控记录里登记时间、执行人、核对人、名单人数（取整到 10）
与输出指纹，不登记任何伪名。相邻两次导出的名单只差少数人（例如 < 10 人）时，后一次的报表**不对外**（两次相减可能指向个人；
表键扰动能挡住删一人的差分，但不是差分隐私）。

### 4.4 输出核对与销毁

- 输出只有 `cost-dashboard.json` 与 `cost-dashboard.md`：只含聚合；每名学生一个点；组人数加 ±2 的确定性扰动，扰动后 < 22 只写
  “样本不足”（真实 ≥ 20）；公开人数为扰动后取整到 10；分位数为相邻两秩插值后粗化（消耗 5% 额度一档、使用率 5%、成本 0.1 元），
  P90 以上不足 5 人时 P90 写“样本不足”；按任务的调用数 < 20 写“样本不足”、调用数取整到 10；工具出口检查拒绝写出任何伪名形状的串。
- 核对人逐项确认：没有伪名、没有学生 ID、没有不足门槛的组给出了数；首段标注由数据决定（非合成输入写“试点数据，须经人核对后才可对外”；
  价格表没有签字版本号时一律写“示例价”）。
- `$WORK` 由 `trap` 清理；`$OUT` 里的报表按受控文件保管。个人层数据不复制、不上传、不进仓库（规则 13）。

### 4.5 停止条件

任一前提缺失、连接的不是试点库、输出里出现任何伪名或学生级字段、需要写入才能继续——立即停止，记录在
`specs/cost-dashboard-plt17/lessons.md` 的“待人确认”。
