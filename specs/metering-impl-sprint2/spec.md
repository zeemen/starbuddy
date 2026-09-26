# 冲刺 2-E：计量实现（预留、结算、回滚、过期、24 小时复看）

状态：**草稿，等人确认后才开始实现**（CLAUDE.md：先 spec，确认后再动手）。确认人、日期：＿＿＿

关联：
- **开发计划 冲刺 2-E**：“计量实现：开始预留、成功结算、失败回滚；24 小时同题复看不重复扣；服务错误重试不扣第二次”。负责人是 TS 后端，验收是“幂等与回滚测试全绿（含并发与断线）”。
- **Gate 0 硬条件**：“家长隔离与扣次幂等通过测试”。本冲刺交付其中的“扣次幂等”。
- **表结构**：冲刺 1-E（#18），迁移 `0008`、设计文档 `docs/design/metering-schema.md`（下称“设计文档”）。设计文档第 4、5 节就是本冲刺的实现说明书。
- **学习服务写入接口**：冲刺 1-C（`services/learning`、`packages/contracts/openapi/learning-internal.v0.yaml`）。其中“可重试错误属于结果未知”的约定是本冲刺的对接前提。
- **ADR**：ADR 06（计量一致性，选项 A）、ADR 01（计量在 BFF 侧，BFF 用 TypeScript）、ADR 03（定时任务调度）。
- **CLAUDE.md 第 4 条**：消耗类请求必须带 `usage_id` 与 `idempotency_key`，开始预留、成功结算、失败回滚。
- **遗留项**：`specs/metering-schema-sprint1/lessons.md` 待人确认 #1–#12；契约 v1.0 评审清单 N8、N17。

## 目标
1-E 已经建好表，并在数据库层挡住了“写错就多扣”的情况。本冲刺实现计量的写入函数与 BFF 侧的计量流程，用测试证明 Gate 0 要求的六类场景**不多扣、不少扣、不重复扣**：
1. 并发扣同一额度；
2. 预留后断线；
3. 同一幂等键重试；
4. 结算前失败回滚；
5. 预留过期释放；
6. 24 小时复看同一内容。

完成后，一次错题分析或诊断请求可以走完整条链路：BFF 授权 → 预留 → 学习服务写入（同一个 key）→ 结算或释放。家长和学生查到的余额与流水一致。

## 现状（起草时核实）
- 仓库里**还没有 `services/bff`**。本冲刺要新建它的骨架，但只做计量模块，不做登录、会话或统一壳接口。
- 学习服务只有 `grade`、`diagnose` 两个写入接口，没有 `teach`。所以讲评课、语音、AI 时长这三种按 `teach` 计的资源，本冲刺只实现数据库函数，不接 BFF 流程。
- `subject_enrollment` 现在允许 BFF 以学生或家长身份直接 INSERT（schema spec 待人确认 #11）。1-E 的默认是在 2-E 把选科改成只能经函数写入，按档位校验上限（见范围第 6 条）。
- 学习服务的请求指纹用 Python 的 `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`，见 `services/learning/learning/fingerprint.py`。BFF 用 TS 算计量指纹时，必须逐字节复现这套规范化规则，数字的格式化最容易出差异。

## 设计要点

### 1. 写入函数（迁移 `0009`，`SECURITY DEFINER`，属主 `starbuddy_authz`）
所有函数共同遵守设计文档 4.0 节：
- 授权从 `platform.current_account_id()` 推导；
- 不接受外部传入的 `entitlement_id`，额度桶由函数自己查；
- 加锁顺序固定为：先额度桶，再预留；
- 终态操作幂等；
- 写完后在同一事务里核对本桶。

| 函数 | 调用方 | 作用 |
|---|---|---|
| `meter_reserve(student_id, resource_type, idempotency_key, request_fingerprint, quantity, content_fingerprint, source_method)` | BFF（用户请求） | 授权 → 查当前有效额度桶 → 同 key 处理 → 24 小时复看判断 → 锁桶校验余额 → 插预留、写 reserve 流水、加计数 → 核对本桶。返回 `usage_id`、状态、剩余额度 |
| `meter_settle(usage_id, settled_qty)` | BFF | 先改预留状态为 `settled`，再写 settle 流水、改计数。同一终态重复调用返回首次结果 |
| `meter_release(usage_id, reason_code)` | BFF | 确定失败时释放 |
| `meter_expire_due(batch_size)` | 定时任务 | 不加锁地取出到期的 `usage_id`，逐笔按固定顺序加锁、重查状态后过期。返回处理条数 |
| `meter_open_subscription(student_id, tier, config_version, source)` | BFF（注册、试点开通） | 只允许 `trial`、`pilot_virtual`。建订阅、建本周期的全部额度桶，并按 `plan_allowance` 写 grant 流水 |
| `meter_renew_due(batch_size)` | 定时任务 | 推进到期订阅的周期，应用 `next_tier` / `next_config_version`，建新桶、写 grant 流水 |
| `meter_check_entitlement(entitlement_id)` | 函数内部 | 只核对一个桶：计数 = 流水与预留聚合，不扫全表。不一致就抛错，整个事务回滚 |

**usage_id 与 key**：
- `usage_id = uuidv5(计量命名空间, idempotency_key)`，由数据库函数计算，BFF 也能在预留前算出来。
- key 必须是 UUIDv4 字符串，由前端为每次用户动作生成。
- 迁移需要实现 uuidv5（基于 `pgcrypto` 的 `digest`，或纯 SQL 实现，见待人确认 #3）。

**同 key 处理**（设计文档场景 3）：
- 学生、资源类型或计量指纹有任何一项与已有记录不同 → 返回冲突，不透露已有记录的任何信息；
- 全部相同时按已有状态返回：
  - `reserved` → 同一个 `usage_id`，调用方继续；
  - `settled` → 首次结果，不扣第二次；
  - `released` / `expired` → 返回该终态，BFF 按第 3 节处理。

**24 小时复看**（设计文档场景 6，只对 `mistake_analysis` 生效）：
- 先对 `dedup_key` 取 `pg_advisory_xact_lock`；
- 有仍在 `reserved` 的同键记录 → 返回“处理中”，BFF 转成 409 `DEDUP_IN_PROGRESS`；
- 有窗口内已扣费的首次 → 插一笔数量为 0 的复看预留，BFF 结算时结 0；
- 都没有 → 正常扣费。

**多个额度桶**（主订阅与附加权益）：默认先扣主订阅，扣完再扣附加权益（待人确认 #4）。本冲刺不开放附加权益的写入，只实现选择顺序。

### 2. 授权规则
- 消耗动作（诊断、错题分析）只能由**学生本人**发起：`platform.is_self_student(student_id)`。
- 家长能否代为发起，比如替孩子上传错题照片，见待人确认 #1。默认不允许。
- 开通订阅：只有能读该家庭的家长（`platform.can_read_household`）才能开通。`pilot_virtual` 另外要求一个 BFF 配置开关，防止普通注册拿到试点额度（待人确认 #5）。
- 定时任务的函数：只授权给一个新角色 `starbuddy_scheduler`（NOLOGIN，由人在 bootstrap 里创建）。BFF 角色**不能**调用。

### 3. BFF 计量模块（`services/bff`，TypeScript）
- **骨架**：Node 22（原生运行 TS，与阿鸦适配器同一工具链）、`node:test`、`tsc` 类型检查。数据库客户端用 `pg`（node-postgres，MIT，待人确认 #2）。
- **模块** `services/bff/src/metering/`：
  - `fingerprint.ts`：计量指纹。把请求体规范化，去掉 `request_id` 和 `usage_id` 后算 SHA-256。规范化规则与 `services/learning` 的 `canonical()` 逐字节一致，由共享测试向量保证（`packages/contracts/python/tests` 和 BFF 测试读同一份向量文件）。
  - `content-fingerprint.ts`：内容指纹。上传图片取原始字节的 SHA-256，题库题取 `item_ref`，格式 `<来源>:<摘要>`。
  - `meter.ts`：`meteredCall(accountId, action, callLearning)`，流程如下：
    1. 在一个事务里 `SET LOCAL app.account_id`，调用 `meter_reserve`；
    2. 用同一个 key、带上 `context.usage_id` 调学习服务；
    3. 按结果处理：
       - 确定成功 → `meter_settle`；
       - 确定失败（`retryable: false`）→ `meter_release`；
       - 结果未知 → 按退避用同一个 key 重试，直到确定或到达 `expires_at`；仍不确定就不结算，留给过期任务。
  - `expiry-job.ts`、`renew-job.ts`：定时任务入口，以 `starbuddy_scheduler` 身份调用。本冲刺只提供一次性执行入口，接入调度器是 ADR 03 的事。
- **过期后同 key 重试**：BFF 用原 key 调学习服务拿重放结果，不再预留、不扣费，并记指标 `late_success_after_expiry` 或 `replay_after_expiry`。学习服务明确失败之后，BFF 才告诉前端可以换新 key。已知缺口见待人确认 #6（N17）。
- **不做**：登录与会话（BFF 的鉴权中间件是另一个冲刺），`accountId` 由调用方传入；统一壳 HTTP 接口；支付。

### 4. 选科上限（1-E 待人确认 #6 的默认，放在本冲刺）
- 迁移 `0009` 收回 BFF 对 `subject_enrollment` 的直接 INSERT 权限，删掉 `enrollment_write` 策略，改为只能经函数 `enroll_subject(student_id, subject)` 写入。
- 函数按学生当前有效订阅的 `plan_feature.max_active_subjects` 校验 `active` 科目数：
  - `all_launched` 按已上线学科数解释。上线清单来自哪里，见待人确认 #7。
  - 同一学生的并发选科按学生加锁，避免两个请求同时通过校验。
- 暂停选科 `pause_subject` 同样经函数。
- 这是 RLS 变更，按规则 5 必须附测试。

### 5. 测试（Gate 0 六类场景）
**数据库测试** `db/tests/70_metering_functions.sql`：单连接内能测的都在这里测：
- 授权：家长不能发起消耗；另一户家长、未登录都不能调用；BFF 不能调用定时任务函数；
- 同 key 的四种状态；
- 冲突时不泄露已有记录；
- 复看的三个分支；
- 终态幂等；
- 结算数量超出预留被拒；
- 每一步之后 `meter_check_entitlement` 与两个对账视图都为零行；
- 选科上限与 RLS。

**BFF 集成测试** `services/bff/tests/`：连接一次性 Postgres，多个连接并发；学习服务用进程内的假服务，可以模拟成功、422、503、超时、延迟成功：

| 场景 | 测试 |
|---|---|
| 1 并发扣同一额度 | 额度只剩 1，20 个连接同时预留：恰好 1 个成功，其余“额度不足”，计数与流水一致 |
| 2 预留后断线 | 预留提交后杀掉连接：① 同 key 重试拿回同一笔预留；② 不重试，过期任务退回额度 |
| 3 同 key 重试 | 同 key 并发 N 次：一笔预留、只扣一次；换请求体、换学生分别得到 409 |
| 4 失败回滚 | 学习服务 422 → 释放；503 后重试成功 → 只扣一次；503 一直不恢复 → 不结算，到期释放 |
| 5 过期释放 | 推进时钟过期；过期后迟到的成功不扣；过期任务与结算并发时只有一方生效，而且不死锁 |
| 6 24 小时复看 | 首次扣 1；23 小时后扣 0；25 小时后扣 1；首次处理中时复看得到 409；两个连接同时首次只扣 1；兄弟姐妹各扣各的 |

- **时钟**：函数里的“现在”统一取 `platform.meter_now()`。测试库里可以把它替换成读 GUC 的版本，以便推进时钟；生产版本直接返回 `now()`。替换在测试夹具里做，不进迁移。
- **一次性 Postgres**：把 `db/tests/run.sh` 里启动临时实例的部分抽成 `db/tests/pg-ephemeral.sh`，db-test 和 bff-test 共用。只连本机 Unix socket，结束即删（规则 9）。
- **变异测试**：`db/tests/mutations.sh` 为每个函数的关键判断各加变异，例如：
  - 去掉 `FOR UPDATE`：要能被并发测试发现，所以放进 BFF 测试的变异集；
  - 去掉授权检查；
  - 去掉复看中“处理中”的分支；
  - 终态幂等改成报错。

### 6. 对学习服务的改动
- 学习服务不访问计量表，本冲刺不改它的权限。
- 需要确认 `context.usage_id` 已经参与学习服务的请求指纹（它在请求体里）。BFF 必须保证同一个 key 每次重试带同一个 `usage_id`，uuidv5 派生天然满足。
- 如果评审决定先做 N17（只查询的重放接口），它属于 `learning-internal` 契约变更，要另开 PR（必停）。本冲刺默认不做。

## 范围

### 做
1. 迁移 `0009_platform__metering_functions.{up,down}.sql`：
   - 第 1 节的全部函数与 `meter_now()`；
   - 选科函数与 `subject_enrollment` 的 RLS 变更；
   - `starbuddy_scheduler` 的授权（角色本身由人在 bootstrap 里创建）；
   - 给 `starbuddy_authz` 的列级授权（设计文档第 7 节）；
   - 同步改写 60 §八 里“授权函数属主读不到”的断言。
2. `services/bff` 骨架与计量模块（第 3 节）。
3. 共享指纹测试向量，Python 与 TS 两边都跑。
4. 测试：db-test、BFF 集成测试、变异测试（第 5 节）。`Makefile` 新增 `bff-test`，并纳入 `make check` 与 CI。
5. 设计文档补一节“实现与设计的差异”（如有），并更新 `lessons.md`。

### 不做
- 讲评课、语音、AI 时长的 BFF 流程：学习服务还没有 `teach` 接口，本冲刺只实现和测试它们的数据库函数。
- 升级（adjust 补差）、付费订阅、附加权益的发放、退款与人工更正：都依赖支付或运营后台。更正流水的数据库约束 1-E 已经有了。
- 余额展示接口与统一壳页面（开发计划“额度可见”在后续冲刺）。
- BFF 登录、会话、鉴权中间件。
- 调度器接入（ADR 03）。
- N17 只查询重放接口（契约变更）。

## 验收标准（可测试）
- [ ] 六类场景的 BFF 集成测试全部通过，其中并发和断线用真实的多连接 Postgres。这是开发计划的验收。
- [ ] `db/tests/70_metering_functions.sql` 通过。每个测试步骤之后，`meter_check_entitlement` 与两个对账视图都为零行。
- [ ] 变异测试：每个函数的授权、加锁、状态判断、复看分支都有变异，并且全部被发现。
- [ ] Python 与 TS 对共享测试向量算出的指纹逐字节一致。
- [ ] BFF 角色不能直接写计量表，也不能调用定时任务函数；学习服务、复核角色对计量函数没有 EXECUTE 权限。以上都由测试断言。
- [ ] 选科：超过档位上限被拒；BFF 不能再直接 INSERT `subject_enrollment`。有 RLS 测试和变异。
- [ ] 严格模式 `make check` 通过；CI 跑 `bff-test`。

## 涉及的必停项（见 CLAUDE.md）
**是**，实现 PR 首行写 `[NEEDS-HUMAN]`：
- `db/migrations/0009`：函数、授权、`subject_enrollment` 的 RLS 变更；
- 计量与权益逻辑；
- 新增角色 `starbuddy_scheduler`（bootstrap 由人执行）；
- 新增依赖 `pg`（MIT）。它不是第三方服务，但按规则 10 要检查许可证。

不涉及契约（N17 不在本冲刺）、人设、数据出境。

## 依赖与输入
- 1-E 的表结构已合并（#18）。设计文档签字未完成，如果签字结果改了表结构，本冲刺跟着调整。
- `specs/metering-schema-sprint1/lessons.md` 待人确认 #1（筛查是否计量）、#3（按学生去重）、#4（迟到的成功不扣费）影响函数行为。本 spec 按 1-E 的实现写。
- 学习服务 1-C 的接口与重试语义。

## 待人确认
1. **家长能否代为发起消耗**，比如替孩子上传错题照片让系统分析？默认不允许，只有学生本人能发起。允许的话，扣的仍是孩子的额度，流水的 `actor` 仍是 `bff`，另记发起账号。
2. **BFF 的数据库客户端**：默认用 `pg`（node-postgres，MIT，使用最广）。另一个选择是 `postgres`（porsager，Unlicense）。
3. **uuidv5 的实现**：默认启用 `pgcrypto` 扩展，用 `digest` 做 SHA-1。它随 Postgres 发行，许可证是 PostgreSQL License，但启用扩展需要超级用户，属于迁移执行时的额外步骤。另一个选择是在 BFF 算好 `usage_id` 传进来，由函数校验它等于派生值。
4. **多个额度桶的扣减顺序**：默认先扣主订阅，扣完再扣附加权益。
5. **试点虚拟额度怎么开通**：默认由 BFF 的配置开关加一份邀请名单控制，函数只校验 `source = 'pilot_virtual'` 时调用方是家长。名单放在哪里、谁维护，待定。
6. **过期后同 key 重试**：默认 BFF 用原 key 调学习服务。如果学习服务那次其实失败过，会重新执行一次，而这次没有预留（N17 的缺口），由指标 `replay_after_expiry` 监控。另一个选择：过期后直接让前端换新 key，但如果学习服务那次其实成功了，就会出现重复判分和重复证据。
7. **`all_launched` 的上线学科清单**：默认按 `curriculum_version` 里存在 `published` 版本的学科计。另一个选择是单独建一张上线配置表。
8. **结果未知时的重试预算**：默认按 1、2、4、8 秒退避，总时长不超过预留过期时间的一半，剩下的一半留给过期前的最后确认。具体数值待产品和 SRE 确认。
9. **定时任务角色**：新增 `starbuddy_scheduler`，要改 `db/bootstrap/roles.sql`（由人执行）。另一个选择是复用 `starbuddy_authz`，但它是函数属主，不应该用来登录。
