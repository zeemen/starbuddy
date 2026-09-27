# SIE-00 实现记录

2026-09-27。用户确认“按默认建议，开始实现 SIE-00”。spec 待人确认 #1–#8 全部按默认做法执行：
- 假设不能直接推翻；
- 到期时长不写死在迁移里；
- 判断账本放 `insight.*`，新增 `starbuddy_insight` 角色；
- 禁用推断词表以草稿提交，同时覆盖中英文；
- 首发只开数学“知识掌握”“知识迁移”；
- 保留 `platform.guardian_report` 作家长观察原文；
- 只收地区，不收学校与班级；
- 迁移编号用 0010。

## 交付
- **字段字典**：
  - `docs/design/sie/field-dictionary.v0.9.yaml`：46 个字段，覆盖九组、第四章采集清单、第五章八个中圈指标、第九章 insight 模型；9 个退役旧字段；
  - 人读版 `field-dictionary.md`，由脚本生成，末尾附覆盖对照表。
- **检查脚本**：`docs/design/sie/check_field_dictionary.py`，8 项检查、17 个内置反例、人读版篡改检查。已接入 `make sie-dictionary-check`，并加入 `check-fast`、`check`；CI 走 `make check`。
- **禁用推断词表草案**：`docs/design/sie/forbidden-inference.v0.yaml`，10 类，每类都有中英文模式、禁止示例，另有 12 条防误伤的允许示例。匹配函数与自检在 `docs/design/sie/sie_lexicon.py`。
- **迁移 `0010_insight__claim_ledger`**：
  - `insight` schema：`claim_template`、`claim`、`claim_history`；
  - 状态机触发器、只追加的历史、RLS；
  - `starbuddy_insight` 角色（`db/bootstrap/roles.sql`）。
- **测试**：
  - `db/tests/75_claim_ledger.sql`，约 50 项断言；
  - 攻击面测试加入 insight 角色与判断表的可见性规则；
  - 权限与策略快照更新；
  - 新增 13 个变异，178 个变异全部被发现；
  - 渗透测试夹具给每个学生加一条判断，让跨家庭可见性矩阵不空转。
- **文档**：ERD 增加 insight 一节；ADR 13 草稿与索引；渗透测试范围的 SECURITY DEFINER 函数表增加 2 个（共 30 个）；CLAUDE.md 的仓库结构与规则 2 写明判断账本已建。

## 与 spec 的偏差（按实现取舍，需要时由人改回）
1. **前端与学科服务读判断用 RLS 策略，不另建视图**：spec 写“只能经下面的视图读”。仓库里其他个人层表都是“表 + RLS”，攻击面测试的可见性矩阵也按这个方式工作，所以判断表沿用。效果相同：
   - BFF 按学生本人或绑定家长、按可见范围、只看非终态；
   - 学科服务只看含 agent 的非终态。
2. **“自述字段必须家长可删”留了一个例外**：年级、地区、教材版本是建档必填的基础身份，单独删掉会让档案不能用，所以不单独删，走整档删除。脚本要求这类字段在 note 里写明删除途径。
3. **退役旧字段是 9 条，不是 8 条**：spec 说 8 个字段或取值；实现把枚举类型 `student_seg_t` 本身也列入，以便拦住同名字段。
4. **新增一条可见性规则**：对学生或家长可见的事实，只能由学生或家长自己填写（例如考试成绩按录入算）。上传原件与平台采集的行为事件默认只给 Agent。这是从 spec 验收“可见字段只有两维相关字段与学生家长自己填写的字段”推出来的。
5. **流转原因的传递方式**：洞察服务在同一事务里设置 `insight.transition_reason`，触发器按“原状态 → 新状态 → 原因”的白名单校验。原因取值：created、evidence_threshold、expired、dispute、exam_deviation、verification_supported、verification_refuted、fact_corrected。
6. **触发器的安全属性**：
   - `claim_guard` 是 SECURITY INVOKER：它按调用者身份读模板，只有已审核的模板对洞察服务可见；
   - `claim_log` 与 `claim_history_guard` 是 SECURITY DEFINER，属主 `starbuddy_authz`，这样洞察服务自己写不了历史；
   - `docs/security/check_attack_surface.py` 的函数表正则同步放宽到 `insight` schema。
7. **渗透测试夹具里的判断**：来源写成 objective，没有证据 ID，只为让可见性矩阵有数据，不代表真实判断的形状。渗透测试环境没有加只继承 `starbuddy_insight` 的登录角色（见 ADR 13 待人确认 #4）。

## 待人确认
1. **禁用推断词表**：草稿需要产品负责人、教研与外部合规顾问签字（必停项）。签字前不能用它做发布阻断，只用于字典检查与模板登记前的检查。
2. **判断的默认有效期**：SIE-02 前由产品与教研给出，写进洞察服务配置。
3. **两处可见范围先只给 Agent**：
   - `inner.g2.exam_prediction`：学生与家长的可见范围等对账卡上线（SIE-03）时由人确认；
   - `inner.g3.error_attribution`：家长端的错因趋势同样等人确认。
4. **洞察服务的代码位置**：见 ADR 13 待人确认 #1。
5. **SIE 第九章其余表**：exam_record、behavior_event、结构化的 self_report 与 guardian_report、confidence_probe、learner_metric、exam_prediction、teaching_response、dispute、intervention_log、profile_snapshot 由 SIE-01/02/03 建。字典已经写好它们的列名，建表时核对。
