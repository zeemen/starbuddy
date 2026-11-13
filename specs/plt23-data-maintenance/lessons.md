# PLT-23 · 数据维护 · 实现记录

2026-11-09 用户确认（“按建议，默认值都同意，开始实现”），待人确认 1–5 全部按默认。**必停**：两个新迁移草稿（0062、0063）与调度时间表；
提交首行 `[NEEDS-HUMAN]`；迁移只写草稿，由人在副本演练后执行；两个调度任务写在合成时间表里，签字前不启用。

## 交付

### A. 分区续建（0062，T18）
- `platform.ensure_partitions_ahead(p_now)`：SECURITY DEFINER，**属主是执行迁移的账号（四张父表的属主）**；父表写死为 evidence_event、attempt、
  usage_ledger、model_call_log；只建 p_now 所在 UTC 月起 7 个月（当月 + 6）里缺的分区，不删不改；p_now 在 now() 往前 1 天之内且不晚于 now()；
  函数级 `TimeZone = UTC`（分区边界是 UTC 月初）；只授 starbuddy_scheduler。建出的分区没有任何授权（只经父表访问）。
- `platform.partition_horizon()`：SECURITY INVOKER，只读系统目录，返回四张表各自建到的最后一个月；只授 starbuddy_scheduler
  （不授报表身份：79zh 锁定了报表身份在 platform 下能执行的函数集合）。
- 告警：不新增指标。`partition-ensure` 续建后读水位，任何一张表不足“当月 + 3 个月”（或缺表）就退出码 1，由调度器已有的任务失败告警发出；
  任务不跑则由已有的“任务多久没成功”告警发现。

### B. 保留期清理（0063，T41）
- 五个函数 `platform.retention_purge_ingest_request`、`retention_purge_auth_otp`、`retention_purge_auth_session`、`retention_purge_auth_rate_counter`、
  `insight.retention_purge_behavior_event`：写法照 0060 的 `model_call_log_purge`（SECURITY DEFINER、属主 authz、每次至多一批、p_now 与批量核对、
  只按时间删、只授调度器）。参数核对抽成 `platform.retention_args_ok`（只授 authz）。
- 保留期：写入幂等 30 天（进行中且租约未过期的不删）、验证码过期后 1 天、会话失效（到期、空闲超时、注销三者最早）后 30 天、限流计数 2 天、
  行为事件写入（服务端时间 created_at）后 180 天。
- 授权：authz 新得 auth_otp_challenge、auth_session、behavior_event 的 DELETE，ingest_request 的 DELETE 与**五列**的 SELECT
  （主键、创建时间、状态、租约到期；读不到学生、attempt_id、指纹与结果摘要，50 号测试核对）。

### C. 调度器
- `insight.scheduled` 新增 `partition-ensure`、`retention-purge`（调度器身份；输出一行 JSON，只有计数）；`services/scheduler` 的调度器身份放行这两个子命令，
  合成时间表加两个任务（03:10、03:40，北京时间，待人签字）。
- 指标标签：调度任务指标的 task 标签只收共享枚举（learning、insight、knowledge 的 obs.py 与 BFF 的 metrics.ts 四处逐字相同，deploy 测试核对），
  四处都加了 `partition-ensure`、`retention-purge`，否则这两个任务的失败会以 task = other 计入告警。

### D. 测试与变异
- 库上 `db/tests/79zj_data_maintenance.sql`；`70`（调度器能调用的函数清单）、`79p`、`80`（SECURITY DEFINER 属主例外、清单表删除例外）随之登记；
  授权快照重新生成（33 行新增，逐条是本任务的授权）；`docs/security/pentest/scope.md` 函数表 139 → 145；攻击面清单 H1 补引用。
- 变异：库 11 条（数据维护 8、攻击面 3）、洞察服务 4 条（另修正 1 条因代码改动失配的旧变异）、调度器 3 条（另修正 2 条失配的旧变异），全部被发现。

## 测试结果（本机）
- 完整的 db-mutation（四个分片 316 + 316 + 315 + 315 = 1262 个变异）全部被发现；提交 4285fdf 时它还在跑，结果补记在这里。
- 其余见提交说明：db-test（63 个迁移 up / down / up，3327 项断言）、attack-surface-check、deploy-check、scheduler-test 与 scheduler-mutation、insight-test。

## 与 spec 的偏差
1. **函数名**：spec 草稿里写的 `*_purge` 后缀，有三个会以 `auth_` 开头，撞上 79k“BFF 以外的应用角色不能调用任何 auth_ 函数”的规则；统一改为 `retention_purge_` 前缀。
2. **“剩余不足 3 个月”告警**：spec 写的是“调度器把水位换算成剩余月数作指标”。实现改为任务退出码 1（用已有的任务失败告警），不新增指标族与告警规则；
   原因是续建每天都会把水位推到当月 + 6，只有续建本身失败时才会不足，而那时任务已经失败。需要单独看水位时可调 `platform.partition_horizon()`。
3. **写入幂等表的 SELECT**：只授判断要用的五列，不是整表（50 号测试原有“authz 读不到写入幂等表”的断言因此仍然成立）。

## 经验
- 80 号测试原本要求每个 SECURITY DEFINER 函数属主都是 authz（删除执行函数除外）。建分区必须是父表属主，这条规则挡住了 spec 的做法；
  处理是登记**唯一**一个例外，并核对它的属主就是四张父表的属主、只收一个时钟参数，另加一条“不属 authz / eraser 的定义者函数恰好一个”。
- psql `\gset` 存下的布尔值是裸的 `t`，再用 `:var` 展开会被当成列名；用 `:'var'::boolean`。
- attempt 的分区被别的表的外键引用，测试里删分区要 CASCADE（整个文件在事务里，结束回滚）。
- 保留期的边界夹具不要正好压在截止线上（179 天整与 180 天截止相差恰好一天，“保留期缩短一天”的变异就测不出来）；用 179 天 12 小时。

## 待人确认
1. **续建函数的属主**（安全评审）：现在是执行迁移的高权限账号（四张父表的属主），在 staging 是 RDS 的高权限账号，函数里写死了能做的事，但定义者身份本身很宽。
   更窄的做法：新建一个只拥有这四张父表的 NOLOGIN 角色，把父表与函数的属主改给它（要改表属主，影响授权快照，且要核对 RDS 上高权限账号能否 `ALTER ... OWNER TO`）。
   建议在副本演练时一并评估，演练通过前不执行 0062。
2. **保留期取值**须合规负责人在隐私政策、同意书与用途授权里写同一组数（尤其行为事件 180 天）；改取值只改 0063 的函数（新迁移）。
3. **空的旧分区**仍由人处理（spec 默认）：0060 的调用记录留存删除与本任务的续建都不删分区。
4. **调度时间**：03:10、03:40 与批量 1000、批次上限 10000 都是默认值，随时间表签字。

## 待人确认的结论（2026-11-09，用户“人都确认都同意”）
1. 续建函数的属主：按建议，在副本演练时一并评估“专用分区属主角色”的做法；演练通过前不执行 0062。
2. 保留期取值由合规负责人确认；隐私政策、监护人同意书与用途授权写同一组数（写入幂等 30 天、验证码过期后 1 天、会话失效后 30 天、
   限流计数 2 天、行为事件 180 天）。
3. 空的旧分区仍由人在分区维护时处理。
4. 调度时间按默认签字：partition-ensure 每日 03:10、retention-purge 每日 03:40（北京时间），批量 1000、批次上限 10000；
   由人生成签字时间表 `schedule.signed.yaml` 时写进去（仓库里只有合成时间表，签字表随部署由人提供）。
