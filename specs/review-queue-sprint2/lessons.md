# 冲刺 2-G 统一复核队列 · lessons

## 实现要点
- **迁移 0023（草稿，人执行）**：
  - `review_reason` 加 `profile_dispute`；`review_task.source_kind` 加 `claim`，约束“指向判断 ⇔ 画像异议”；
  - 新列 `assigned_to`、`assigned_at`，约束“待处理没有领取人、处理中必须有”；
  - 结案码白名单表 `review_resolution_code`（5 个码，每个码写明适用的任务类型）。
- **函数**（属主都是 authz，共新增 6 个 SECURITY DEFINER，scope.md 同步到 72 个）：
  - 领取 `take_review_task`（重复领取自己的是幂等；别人领取的回 409）、放回 `release_review_task`；
  - 结案 `resolve_review_task`：替换 0006 的版本，改为先领取、只有领取人能结案、码必须在白名单里且适用于这类任务；
  - 审核台读函数 `review_queue` / `review_task_detail`：
    - 学生只给不透明指针 `s-` 加 12 位哈希，不给 student_id、领取人 ID、结案人 ID，只标“是不是我”；
    - 判分给结构化字段与逐维分；判断只给模板、状态、置信度，不给 value；
  - 抽检 `review_sample_spot_checks`（只授调度角色）：按判分 ID 的 md5 确定性抽样，比例上限 0.5，最多往前 31 天，不抽已被取代的判分；
  - 画像异议入队 `enqueue_claim_dispute`（只授洞察服务）：判断须存在、属于某一科、不是推翻或撤回；未结案时返回原任务，已复核过的再入队拒绝。
- **权限收紧**：
  - 审核台 `starbuddy_review` 不再能直接 SELECT `review_task`（表里有 student_id），只经两个读函数看；
  - 学习服务的进队策略加了三条：只能为判分、观测、错因进队；判分必须真实存在且学生、学科一致；不能放抽检与画像异议。
- **学习服务**：`_insert_grade` 在写判分的同一事务里进队。原因取判分的 review_reasons，去掉 spot_check 与 profile_dispute：
  适配器自己标的抽检不据此进队，抽检只由调度角色按比例抽。
- **审核台后端**：`/review/v0/queue`（按学科、状态、原因筛选，按创建时间翻页）、`/queue/resolution-codes`、`/queue/{id}`、`/take`、`/release`、`/resolve`。
- **测试**：
  - DB：`db/tests/79e_review_queue.sql`，新增 69 项断言；0023 的变异 21 条，另改写 1 条旧变异（“结案不校验教研身份”），都能被发现；
  - 40、70 两个测试文件的旧断言按新口径改写：先领取再结案；调度角色可执行的函数多了抽检；
  - 学习服务：`test_review_queue.py` 6 条（进队、重放不重复、不复核不进队、申诉、适配器自标抽检不进队、审核台闭环、抽检与画像异议）；
    `tests/review_loop.py` 的 `close_loop` 并进阿鸦、知行的端到端（真实适配器进程）。文砺的端到端在 ADP-语文-01 合并时加。

## 与 spec 的偏差
- `enqueue_claim_dispute` 只收判断 ID，没有 spec 写的 `p_kind`：异议来自谁（学生或家长）属于 `insight.dispute`（SIE-03）的字段，现在没有地方存。
- 跨科判断（subject 为空）暂不能入队：`review_task.subject` 非空，而且队列按学科分。
- 审核台读队列改成只经函数（收回了直接 SELECT）。spec 只说列表不显示可识别信息，这里在库层收紧了。
- 结案码白名单不设外键，只在结案函数里核对。原因是 0023 之前结案的任务可能带自由码，加外键会让迁移失败。

## 踩过的坑
- `ALTER TYPE … ADD VALUE` 加的新枚举值在同一事务里不能用。迁移按单事务执行，所以策略和约束里比较 `profile_dispute` 时一律先转成 text；函数体是运行时才解析，不受影响。
- 回滚删不掉枚举值。down 脚本只删用到它的对象；画像异议任务存在时，恢复 source_kind 约束会失败，这是有意的，不静默删复核记录。
- 知行的严格检查（check26）跑到一半时 0023 落盘，渗透环境测试报授权快照多出一列。和知行无关，由本任务的严格检查一起覆盖。

## 待人确认（实现中新增）
1. **改判放行口径**：0005 的 supersede 规则只要求“staff 结过案”，没有要求结案码是 `regrade_required`。要不要收紧属于判分逻辑（必停项），默认不改。
2. **已复核过的判断再次被异议**：默认拒绝再入队，一条判断只有一条复核任务（UNIQUE）。需要多轮复核时要改唯一约束，或者改由 `insight.dispute` 建多条，随 SIE-03 定。
3. **学生指针**：用的是固定前缀加 student_id 做 sha256，没有密钥。能拿到 student_id 的人可以反查，审核台本身拿不到 student_id。要不要加密钥（需要库里存密钥）待定。
4. **抽检的调度频率**：调用方（定时任务）还没有，本任务只提供函数。按学科、按天、5% 调用，属于部署配置。
5. **审核台看作答原文**：默认不给。教研判主观题需要看原文时，另起任务做单条授权查看并留审计（spec 待人确认 #4）。
6. 以上涉及迁移、RLS、复核口径与画像可见范围（必停项），PR 首行 `[NEEDS-HUMAN]`。
