# SIE-01 建档 · lessons

## 实现要点
- 迁移 0021（`platform.student_subject_textbook` + `platform.set_subject_textbook`）、0022（`insight.*` 事实层四表、字段白名单、7 个函数）；
  新角色 `starbuddy_report` 写进 `db/bootstrap/roles.sql`，0022 开头断言它已存在。
- BFF 对 `insight.*` 没有任何表授权：写只经函数，读只经 `insight.onboarding_view`（按调用者身份裁剪）。互不可见在库里实现，前端与 BFF 不判断身份。
- 改答案：旧行只允许写 `superseded_at`（触发器 `guard_fact_update`），新增一行；建档进度只追加（`reject_progress_change`）。
- 字段白名单 `insight.report_field` 与字段字典由 `make sie-dictionary-check` 核对（12 项，教材版本另存 platform 表）。
- BFF 接口说明：`services/bff/openapi/onboarding.v0.yaml`（服务内部接口，不是插件契约）。

## 踩过的坑
- 攻击面可见性矩阵要求 BFF 可读的每张表至少有两户数据：`platform.student_subject_textbook` 在 79d 里只写了一户，补了 S2 一行。
- 变异“家长能看到别的家长写的观察”一开始漏检：夹具里没有“同一学生的两位家长”。79d 末尾临时把 gC 绑到 S1、写一条观察、断言互不可见，再解除绑定。
- scope.md 的函数签名要写 `timestamp with time zone`（与授权快照一致），不能写 `timestamptz`。
- TS 的 `erasableSyntaxOnly` 不允许构造函数参数属性（`constructor(readonly x)`），BFF 里改成显式字段。
- 端到端用例之间有先后（答案与进度累积），整文件串行；统计断言依赖前面各用例完成的次数。

## 待人确认（实现中新增）
1. 家长版 spec 里的“既有五个主题的原文（可选）”没有放进建档页：它已由 0006 的 `platform.guardian_report`（自由文本主题）承载，
   建档页再收一遍会出现同一事实两处存放（规则 2）。默认留给家长端既有入口；如要并入建档，需要先定哪张表是真相源。
2. 自由文本（近期目标、痛点补充说明）写入时是否过禁用推断词表做“只标记”（spec 待人确认 #5 的后半）：本任务未做标记，原文只存档、不做任何推断。
3. “约 5 分钟”真实时长：自动化全答约 2 秒，不代表真人；需要产品做可用性测试（spec 待人确认 #7）。
4. 问题与选项全部是占位文案，待教研按 SIE 第九章定稿；教材版本选项目前只有部编版、苏科版两个编码与“不清楚”。
5. 完成率统计 `onboarding_stats` 只出聚合数，但没有最小人数门槛（规则 13 的门槛针对群体回流）：查很短的时段可能只含一两个学生。默认只授洞察服务与报表身份；是否加门槛（如不足 5 人不出数）待定。

## 审查前自查已改
- 学生录考试记录时带排名区间：表约束 `exam_record_rank_guardian` 直接拒绝（23514），不再“照存但读不到”。
  于是学生行不会有排名区间，`onboarding_view` 里按身份裁剪 `rank_band` 成了第二道防线，原变异“学生能看到排名区间”已无法触发，改为变异“学生可录排名区间”（删约束）。
