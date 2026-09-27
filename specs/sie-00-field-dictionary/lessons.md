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
  - `docs/design/sie/field-dictionary.v0.9.yaml`：47 个字段，覆盖九组、第四章采集清单、第五章八个中圈指标、第九章 insight 模型；9 个退役旧字段；
  - 人读版 `field-dictionary.md`，由脚本生成，末尾附覆盖对照表。
- **检查脚本**：`docs/design/sie/check_field_dictionary.py`，8 项检查、20 个内置反例、人读版篡改检查。已接入 `make sie-dictionary-check`，并加入 `check-fast`、`check`；CI 走 `make check`。
- **禁用推断词表草案**：`docs/design/sie/forbidden-inference.v0.yaml`，10 类，每类都有中英文模式、禁止示例，另有 12 条防误伤的允许示例。匹配函数与自检在 `docs/design/sie/sie_lexicon.py`。
- **迁移 `0010_insight__claim_ledger`**：
  - `insight` schema：`claim_template`、`claim`、`claim_history`；
  - 状态机触发器、只追加的历史、RLS；
  - `starbuddy_insight` 角色（`db/bootstrap/roles.sql`）。
- **测试**：
  - `db/tests/75_claim_ledger.sql`：约 60 项断言，其中状态机把 275 种（原状态、新状态、原因）组合逐条核对；
  - 攻击面测试加入 insight 角色与判断表的可见性规则；
  - 权限与策略快照更新；
  - 新增 23 个变异，188 个变异全部被发现；
  - 渗透测试夹具给每个学生加一条判断，让跨家庭可见性矩阵不空转。
- **文档**：ERD 增加 insight 一节；ADR 13 草稿与索引；渗透测试范围的 SECURITY DEFINER 函数表增加 2 个（共 30 个）；CLAUDE.md 的仓库结构与规则 2 写明判断账本已建。

## 与 spec 的偏差（按实现取舍，需要时由人改回）
1. **前端与学科服务读判断用 RLS 策略，不另建视图**：spec 写“只能经下面的视图读”。仓库里其他个人层表都是“表 + RLS”，攻击面测试的可见性矩阵也按这个方式工作，所以判断表沿用。效果相同：
   - BFF 按学生本人或绑定家长、按可见范围、只看非终态；
   - 学科服务只看含 agent 的非终态。
2. **“自述字段必须家长可删”留了一个例外**：年级、地区、教材版本是建档必填的基础身份，单独删掉会让档案不能用，所以不单独删，走整档删除。脚本要求这类字段在 note 里写明删除途径。
3. **退役旧字段是 9 条，不是 8 条**：spec 说 8 个字段或取值；实现把枚举类型 `student_seg_t` 本身也列入，以便拦住同名字段。
4. **新增一条可见性规则**：对学生或家长可见的事实，只能由学生或家长自己填写（例如考试成绩按录入算）。上传原件与平台采集的行为事件默认只给 Agent。这是从 spec 验收“可见字段只有两维相关字段与学生家长自己填写的字段”推出来的。
5. **原因写在判断行里**：原先用会话变量 `insight.transition_reason` 传原因，安全审查证明可以在语句执行途中改写、也会残留到连接池里的下一个事务，已改掉（见下节）。现在每次写入都在 `change_reason` 列带原因。原因取值：
   - 状态变化：created、evidence_threshold、expired、dispute、exam_deviation、verification_supported、verification_refuted、fact_corrected；
   - 不改状态：revised、visibility_changed。
6. **触发器与函数的安全属性**：
   - `claim_guard` 是 SECURITY INVOKER：它按调用者身份读模板，洞察服务能看到已审核与已退役的模板；
   - `claim_log`、`claim_history_guard` 与 `basis_belongs` 是 SECURITY DEFINER，属主 `starbuddy_authz`。前两者让洞察服务自己写不了历史；`basis_belongs` 让洞察服务不用获得 platform 推断表的读权限，就能核对依据，只返回真假；
   - `docs/security/check_attack_surface.py` 的函数表正则同步放宽到 `insight` schema。
7. **渗透测试夹具里的判断**：来源写成 objective，没有证据 ID，只为让可见性矩阵有数据，不代表真实判断的形状。渗透测试环境没有加只继承 `starbuddy_insight` 的登录角色（见 ADR 13 待人确认 #4）。

## 审查与处理（2026-09-27）
安全审查与契约审查的问题，除标为“需人确认”的以外都已修：
- **历史里的原因可以伪造**（安全 A-1）：原因改为写在判断行的 `change_reason` 列里，写入前校验，写入后原样记入历史，不再读会话变量；历史表加检查约束，原因必须在白名单里。
- **不改状态的修改不留痕**（安全 A-2）：
  - 每次写入都写历史，并附可见范围、数值、置信度、证据、依据、到期时间的快照；
  - 改可见范围必须用原因 `visibility_changed`；
  - 模板加 `max_ttl`（1 天至 365 天），新建与续期都不能超过“现在 + 最长有效期”。
- **模板退役后判断撤不下来**（安全 A-3）：转为待验证、推翻、撤回时允许退役模板；前端与学科服务的读取策略经模板表的 RLS 只认已审核模板，退役模板的判断不再可见。
- **会话变量残留**（安全 A-4）：不再使用会话变量。
- **已过期的判断仍可见**（安全 A-5）：前端与学科服务的读取策略都加上 `expires_at > now()`。
- **value 不限大小**（安全 A-6）：加 2 KB 上限，并注明只放模板渲染变量。
- **测试空转**（安全 B）：
  - 其他角色的写入测试先把行准备合法，权限成为唯一的拦截点；
  - 状态机改为全组合逐条核对；
  - 新增退役模板、过期、原因伪造、续期、可见范围、依据、创建时间覆盖等用例；
  - 变异从 13 个增加到 23 个，“未审核模板”一条拆成触发器与 RLS 两个。
  - 另外发现一处：`判断可以不设到期` 变异原先被历史表的非空约束兜住，不算测试失效；现在同时放宽两层，证明测试能发现。
- **判断成为第二真相源**（契约 1）：
  - 新增 `basis_kind / basis_id`，节点与能力类推断必须引用 platform 推断行，并由 `basis_belongs` 核对属于同一学生；
  - 实现时发现 CHECK 约束对 NULL 放行，已用 coalesce 修正，并有测试覆盖。
- **星亮历史落在判断历史里**（契约 1）：改指 `platform.mastery_estimate.state`；检查脚本禁止 storage 指向判断账本。
- **学生与家长说法混存**（契约 1）：
  - 两方都可能提供的字段按来源分开落库；考试记录与异议写 `source_column`（录入人）；
  - 补上 `outer.g8.sleep_schedule`；
  - 写明同一主题只存一处。
- **契约登记**（契约 2）：report 的 trend、highlight_codes、forecast 和 teach 的 segments、verified_claims 已登记到 `specs/contract-conformance-sprint1/v1.0-review.md` 第四节。契约本身没改。
- **旧字段对应**（契约 3）：`ability_state` 只归能力视图；复测时间补上 `point_mastery.next_review_at / review_stage`；预测补上契约 forecast。
- **禁用推断词表**（安全 C）：
  - 补上审查列出的漏网表述：粗心马虎被说成特质、不够努力、玻璃心、叛逆、注意力不集中、阅读障碍、自伤、离婚、性别刻板印象、群体推给个人、careless、sloppy、dyslexia、genius 等；
  - 匹配前做 NFKC、去空白与零宽字符、常用繁体转简体；
  - “崩溃”只在情绪语境下命中；
  - 允许示例“那次是粗心”改为事实描述；
  - 字典检查的词表范围扩大到备注、教学决策、指标定义。
- **字段备注**（安全 C）：
  - 异议默认按提出人可见；
  - 家校沟通提示不要写学校与老师姓名；
  - 补习情况只用于避免重复布置；
  - 上传原件只用境内已备案模型解析。
- **CLAUDE.md**（安全 E）：措辞改回“迁移 0010 与 ADR 13 均为草稿、待人执行”。

## 待人确认
1. **禁用推断词表**：草稿需要产品负责人、教研与外部合规顾问签字（必停项）。签字前不能用它做发布阻断，只用于字典检查与模板登记前的检查。
2. **判断的默认有效期**：SIE-02 前由产品与教研给出，写进洞察服务配置。
3. **两处可见范围先只给 Agent**：
   - `inner.g2.exam_prediction`：学生与家长的可见范围等对账卡上线（SIE-03）时由人确认；
   - `inner.g3.error_attribution`：家长端的错因趋势同样等人确认。
4. **洞察服务的代码位置**：见 ADR 13 待人确认 #1。
5. **SIE 第九章其余表**：exam_record、behavior_event、结构化的 self_report 与 guardian_report、confidence_probe、learner_metric、exam_prediction、teaching_response、dispute、intervention_log、profile_snapshot 由 SIE-01/02/03 建。字典已经写好它们的列名，建表时核对。
6. **学科服务读判断不按学生过滤**（安全 A-7）：与现有 learning 策略一致，按学生隔离由调用方负责；spec 原写“按学生隔离”。见 ADR 13 待人确认 #1。
7. **待验证的判断对前端可见**（安全 A-8）：星图画虚线、不作为陈述，靠应用层保证。见 ADR 13 待人确认 #2。
8. **字段默认可见范围的四处**（安全 C）：异议按提出人可见、家校沟通的自由文本、补习情况、上传原件，需人确认。
9. **模板登记前的词表检查**：目前只有匹配函数和命令行（`sie_lexicon.py "文本"`），数据库不强制。登记流程在 SIE-02 首批模板时定。
10. **繁体转简体**：只覆盖词表会用到的常用字，完整转换留给词表定稿后的实现。

