# SIE-02b lessons

## 第一步：洞察服务本体（2026-09-30）
范围：新建 `services/insight/`（判断生成与生命周期、`insight.claims / explain / context`、`insight.record` 契约校验、其余五个 501、
首份报告结构化生成与两道检查、中圈指标静默计算）与 `evals/sie-shadow/`（影子评审比对脚本）。BFF 读接口与开发演示页留给第二步。
没有改 `db/`、`packages/contracts`、`docs/personas`、`docs/design/sie` 的词表、`evals/**/gold/**`；没有写判断模板与报告文案（全部是明显占位）。

### 实现要点
- **服务骨架**（待人确认 #1 默认：独立服务）：FastAPI，做法照 `services/knowledge`：令牌决定 caller（bff / orchestrator），sha256 常量时间比较；
  请求体 caller 与令牌一致；契约严格校验请求与响应（不合格回 500）；只监听 127.0.0.1:8791，拒绝通配地址；Host 允许列表；64 KB；
  拒绝 NaN 与重复键。启动断言：登录角色只继承 `starbuddy_insight`，非超级用户、无 BYPASSRLS / CREATEROLE / CREATEDB、不继承其他应用角色、
  非 `pg_` 预置角色成员；注入的连接池同样断言。
- **判断生成**（`insight/claims.py`，纯函数）：输入是 KGE-04 spec 第 13 节形状的掌握行（`MasteryRow`：节点、层次、门槛、p、置信度、证据、
  下次复测时间），库上测试用合成 `mastery_estimate` 行作依据（`basis_id`），节点 × 层次由合成行给出，不猜 KGE-04 第二步的新列。
  每“节点 × 层次”一条“知识掌握”判断；门槛到迁移及以上再有一条“知识迁移”判断。`value` 只放 `{level, gate}`。
  流转规则见模块说明；Python 白名单与 0010 `insight.change_allowed` 逐行一致（测试解析迁移文件核对）。每次写入在同一条语句里带
  `change_reason`，历史由 0010 触发器追加（库上测试核对原因与证据 ID）。未签字策略下永远只到“假设”（性质测试 120 条随机游走）。
- **到期与验证**：到期时间 = 现在 + 有效期（不超过模板上限），若下次复测更早则取复测时间——复测到了判断就到期，复测结果就是验证：
  到期后出现的新证据仍支持 → 成立（需签字与阈值，续期）；不支持 → 推翻，再按新门槛立一条假设。修改（revised）只缩短不延长到期时间。
- **读取**：本服务的读取 SQL 自带与 0010 前端 / 学科策略同一组条件（洞察服务自己的 RLS 全放行，所以这一道是必需的），并对前端一律排除
  metric；库上测试核对它与 0010 的 BFF 行级策略给出同一组判断。前端看不到的判断与“不存在”返回完全相同的 404。
- **首份报告**（`insight/report.py`、`jobs.first_report`）：提示语 + 标题 + 分节（衔接短语 + 判断渲染句，每句带判断 ID）；只收假设与成立、
  可见范围含该受众、未到期、首发学科（数学）的节点判断；学生版假设标 `label: preliminary`、不带置信度，家长版另附 `confidence_band`。
  渲染后跑 `learning.mastery.consistency.check_page`（直接 import，同一个模块对象，有测试）与禁用推断词表（提示语、标题另查），任一不过不出报告。
- **中圈指标**（`insight/metrics.py`）：八项按 SIE 第五章定义与最低样本；结果写 `visibility = {agent}` 的 metric 判断（#6 默认），只到假设；
  指标名与说明计算前过禁用推断词表。合成数据上与手算一致（单测逐项手算，库上测试从一次性 Postgres 读事实再核对）。
- **影子评审**（`evals/sie-shadow/`）：一致率 = 门槛状态相同的可比项 ÷ 可比项，另报覆盖率与三类分歧；合成样例标“流程样例”；
  输出目录不能在 `gold/` 下；真实金标路径 `gold/shadow-review.yaml` 只读，缺失即报错（不当作通过）。

### 偏差（均按“宁可少说、不猜”处理，需要时由人改）
1. **门槛在判断到期前下降**（KGE 设计上只在复测后退级，不应出现）：0010 没有“依据降级”的原因码，假设 / 成立也不能直接推翻，
   本步按撤回处理并借用 `fact_corrected`（判断从所有视图消失，历史留痕）。见待人确认 A。
2. **依据被更正的判定**：该单元没有当前掌握行（节点撤下、传入的 basis_id 在库里不是该学生的当前行），或新掌握行丢掉了判断引用过的证据
   → 撤回。掌握行正常重算（新行、证据只增不减）只做 revised，不撤回。调用约定：`sync_mastery_claims` 传入的必须是该学生的**全部**当前掌握行。
3. **`insight.record` 回 501**：洞察服务没有可用的写入路径（0022、0025 的写入函数只授 BFF，且只认学生本人会话；`recorded_by` 为 orchestrator
   的“连错后退出”没有写入函数）。按 spec“不新增来源”，契约校验通过后回 501。见待人确认 B。
4. **BFF 调用 `insight.explain` 的授权再核对**（契约：洞察服务按 actor 与 student_ref 再核对一次）：洞察服务没有读 `guardian_student_link`、
   `student_profile` 或调用 `platform.can_read_student` 的权限。实现为可注入的 `ActorVerifier`，**缺省一律不通过**（按 404 处理，fail closed）；
   测试注入合成白名单。第二步前需要人决定授权方式（待人确认 C）。
5. **中圈指标判断的 `evidence_ids` 引用事实行 ID**（作答 `attempt_id`、探针 ID、计划与会话事件 ID、错因 ID），不是 `evidence_event` 的证据 ID：
   0028 没有授 `attempt.response_evidence_id` 与 `grade_result.evidence_ids`。0010 只要求“至少一个 ID”；SIE 允许事实被判断引用为证据。见待人确认 D。
6. **独立解题率与迁移差需要题目的节点与认知层次**（`kge.item_tag`），洞察服务没有读权限：`recompute_metrics` 接受调用方注入的
   `(学科, 题目) → (节点, 层次)`；不注入时这两项不出结果、已有判断不动。见待人确认 E。
7. **保持率的分母暂用实际复测数**（按 `evidence_kind = delayed_retest`），“应复测数”要等 KGE-04 的复测任务表。
8. **中圈指标判断到期后转待验证，之后只随新结果 revised**：0010 没有 待验证 → 假设，指标未过效度检验不能“成立”。Agent 读到的指标判断多数时候是待验证状态。
9. **判断的认知层次放在 `value.level`**（`insight.claim` 没有层次列，`target_id` 保持节点 ID 以符合契约的节点语义）；单元身份 = 模板 + 学科 + 节点 + `value.level`。
10. **契约缺口**（不改契约，登记 v1.0 评审）：Problem 的 `code` 没有“判断不存在”，404 沿用 `CONTENT_VERSION_NOT_FOUND`；`ContextEntry` 没有对象字段
    （节点 ID），Agent 需凭 `claim_id` 查 `insight.claims`；`insight.context` 的 `statements` 暂为空；没有文本模板的判断（指标）`insight.explain` 回 404。
11. **首份报告的检查不过即不出报告**（返回阻断编码），不像 KGE-04 档案页那样降级为纯模板版（首份报告本来就只有模板句与固定短语，降级没有意义）。
12. **验证支持时才续期**；假设因证据达标转成立（evidence_threshold）不续期。
13. **影子评审比的是门槛状态**（`value.gate`），不是判断的假设 / 成立（教师判断不了后者）。
14. **依赖与锁文件**：不引入新包，版本与知识服务相同；离线环境里锁文件从 `services/knowledge` 的锁文件逐字摘出同版本同哈希条目，本地 venv 从知识服务
    venv 复制后按锁文件去掉多余包（没有联网）。联网环境可按锁文件头部命令重新生成核对。
15. **跨服务 import**：`insight/shared.py` 把 `services/learning` 加进 `sys.path`（与知识服务接模型路由同一做法），直接复用
    `learning.mastery.consistency` 与 `COGNITIVE_LEVELS`，没有复制实现；部署时洞察服务的制品要带上这个目录。

### 待人确认
A. 0010 是否增加原因码（例如 `basis_downgraded`：假设 / 成立 → 撤回或待验证），替代偏差 1 借用的 `fact_corrected`（必停：迁移）。
B. `insight.record` 的写入路径：给 `starbuddy_insight` 开编排层专用的行为事件写入函数（`exit_after_errors`、`appeal`），还是维持“只经 BFF 写”、
   把 `insight.record` 从 v1.0 契约里去掉或改为只读转发（必停：迁移、契约）。
C. BFF 代表学生 / 家长时洞察服务的授权再核对：建议迁移草稿新增属主为 authz、只返回真假的函数（入参 actor_ref、角色、link_id、student_id），
   只授 `starbuddy_insight`；或确认“BFF 已核对，洞察服务信任 BFF 令牌”并改契约描述（必停：RLS 与权限、契约）。
D. 0028 是否补授 `platform.attempt.response_evidence_id`（列级），让中圈指标判断引用真正的证据 ID（必停：权限）。
E. 0028 是否追加 `kge.item_tag` 的列级只读（`subject, item_id, map_version_id, main_node_id, cognitive_level, tag_review_status`）与策略，
   让独立解题率（按认知层次）与迁移差（同节点）能在库上算（必停：权限）。
F. 判断阈值（`establish.*`）、置信度分档、中圈指标的窗口 / 阶梯长度 / 探针概率映射 / 偏差阈值：SIE 负责人与数学教研签字（#2）；
   有效期 30 天（#5 默认）请产品与教研最终确认。
G. 判断模板（知识掌握、知识迁移、中圈指标）、报告标题、顶部“初步判断，两周内会变准”提示语、固定衔接短语与报告检查词表（程度词、结论词）：
   人写人审后登记（#3）。模板变量目前是 `{node_id}`、`{level}`、`{gate}`（值是编码），文案需要把编码翻成学生能读的话时，请在模板登记时给出映射。
H. 首份报告的对外形态：第二步 BFF 经什么接口取（服务内 HTTP 路由不在契约里；契约新增工具属契约变更）。
I. 中圈指标判断多数时间处于“待验证”（偏差 8）是否可接受；或改为每次重算撤回旧判断、新立一条（需要合适的原因码，同 A）。

### 第二步计划（等 SIE-02a 审查修复合入后再动 services/bff、apps/web）
1. 人确认 C、H 后：BFF 学生端 / 家长端两个读接口（首份报告、单条解释），BFF 先按档案授权（规则 5），再调洞察服务；授权再核对按 C 的结论接入 `ActorVerifier`。
2. 开发环境演示页（只在 dev、合成数据）：报告顶部占位提示语、假设标“初步判断”、家长版置信度分档、待验证虚线（explain）。
3. 触发接入：KGE-04 第二步写节点键掌握行后，由学习服务重算完成事件或定时任务调 `jobs.sync_mastery`（读库的 `MasterySource` 按那时的真实列补上）；
   每天 `jobs.sweep`；每周 `jobs.recompute_metrics`（E 确认后去掉题目打标注入）。
4. 真实首份报告与影子评审一致率：模板审核（G）、教师标注（`evals/sie-shadow/gold/`，#7）到位后跑 `compare_shadow.py`，结论由人定。

### 验证
- `make insight-test insight-mutation CONTRACT_STRICT=1`：单测、性质测试、库上测试、契约严格校验、影子评审脚本测试全部通过；变异 50 条全部被发现
  （最后几行见本次 PR 描述）。
