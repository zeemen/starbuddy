# 计量与权益 v2 · 记录

2026-09-28。用户确认“按默认建议，开始实现”：spec 待人确认 #1–#8 全部按默认执行。

## 交付
- **迁移 `0015_platform__metering_v2`**（草稿，待人执行）：
  - 资源：`upload`、`storage_bytes`、`deep_report`；资源类型加 `curfew_applies`、`bff_only`；单位加 `byte`；去掉“可去重只能单次 1”的约束；
  - 权益代码：`core_subjects`、`elective_slots`、`daily_minutes_cap`，带取值约束；发布齐全检查改为“四档资源集合相同且非空、每档有必要权益”；
  - 表：`subject_catalog`（九科，含上线状态与产品命名空间映射）、`trial_claim` 与 `trial_claim_phone`（手机号只存 HMAC 摘要）、`meter_secret`（摘要密钥，只有函数属主能读）、`student_time_guard`、`practice_log`；`subject_enrollment.paused_at`；
  - 函数：`meter_reserve` 改为包装 `meter_reserve_impl`（新增留存、宵禁、单日上限三项拒绝）；新增 `meter_reserve_upload`、`meter_practice`、`set_time_guard`、`meter_student_state`、`meter_upgrade` 等；替换 `enroll_subject`、`pause_subject`、`meter_open_subscription`、`meter_renew_due`、`guard_plan_config`；
  - 配置 `v12-pilot@1`（草稿）：规格书 §4 的四档额度，试用不单列。
- **测试**：`db/tests/78_metering_v2.sql`（试用、留存、自练、单日上限、宵禁、上传与存储、选科、升级、宽限期、配置发布）；60、80 号测试随新资源与新表更新；变异新增 30 个，全部被发现；0009 的 9 个变异改指 `meter_reserve_impl` 与新的选科函数。
- **BFF**：预留结果与计量调用结果加 `retained_no_ai`、`curfew`、`daily_cap_reached`；集成测试新增“试用到期后被拒”“宵禁被拒”，原有两个用例按新规则改为试点家庭。
- **其他**：渗透测试夹具补自练与时间设置；`scope.md` 函数表（48 个）；授权快照；设计文档 `docs/design/metering-schema.md` 第 9 节。

## 与 spec 的偏差
1. **订阅状态值不改名**：spec 写“扩为 trialing / active / grace / canceled / expired 并在迁移里映射”。改为保留 active / grace / ended，trialing、retained 等由 `meter_state_of` 推出。理由：状态值被 0008 的唯一索引、余额视图、0009 的全部函数与已有测试引用，改名要重写这些对象；推导也避免存可推导的副本（规则 2）。`canceled`（取消续费）随支付接入再加。
2. **宽限期的额度**：spec 写“grace 期内额度照常可用”。实现只做状态流转（active → grace → 7 天后 ended）；宽限期内不发放新周期额度，上一周期的额度桶已到期不可用。付费订阅要等支付接入才会出现，宽限期额度随支付 spec 定。
3. **换科规则也适用于核心科目**：spec 写“每个周期换选修 1 次”。`pick_one` 下基本档换核心科目同样受“换下的本周期仍占槽、每周期换下 1 科”约束，否则基本档可以在一个周期内轮换语数。
4. **设备指纹不记录**：spec 待人确认 #3 写“设备指纹只记录”。没有设备表，本任务不记录，随 PLT-01 的设备表。
5. **存储按周期累计**：`storage_bytes` 是周期内累计上传字节，删除文件不退回。规格 §7“超出时提示清理旧文件”更像占用量，需要文件表与删除回调，随上传通道实现再定（待人确认 #4）。
6. **升级只对付费订阅**：试用转付费是新订阅，不走升级。
7. **学科上线状态种子**：语文、数学、道法为 beta（规格“开发测试”），英语与其余科目为 planned。上线（改 live）是人的操作；在那之前只有试点家庭能选科。

## 审查意见的处理
（待审查后补）

## 待人确认
1. **核心包理解**（spec #1）：按“英语搁置期间基本档任选 1 科、Plus 及以上开放全部已上线核心科目；英语上线后改回打包”实现，配置可切换。
2. **学科何时改 live**：改 live 后试用与付费用户才能选科；由产品负责人决定并由人执行。
3. **宽限期内是否可用上一周期的剩余额度**（偏差 2）。
4. **存储口径**：周期累计还是当前占用（偏差 5）。
5. **初筛与错题分析的额度**：`v12-pilot@1` 里初筛给 30 次、错题分析与上传同额度，BFF 迁到上传计量后再定是否退役。
6. **试用领取记录的删除**：家庭注销时领取记录随家庭删除，重新注册可再领试用。是否要在注销后保留手机号摘要一段时间防刷，需合规确认。
7. **HMAC 密钥轮换**：密钥在迁移时随机生成、存库内；轮换会使已有摘要失效，轮换策略随 ADR 09 / 10 的密钥管理定。
