# PLT-25 · 地图版本交叉核对（W13 技术债 T13）· 规格草稿

- 状态：**已确认（2026-11-09，用户“按建议，默认值都同意，开始实现”）**：第六节待人确认 1–4 全部按默认。实现记录见 lessons.md。
- 编号：PLT-25 为仓库排队号（PLT-24 之后）。
- 依据：`docs/gates/w13-debt-and-decisions.md` T13；`specs/evidence-attributes-sprint1/lessons.md` 待人确认第 4、6、10 条（契约审查建议 8、10，安全审查建议 4）；
  插件契约 v1.0 第 41 行（`source_version` 的地图段写作 `graph:<学科>-map@x.y.z`，与知识地图版本对应）与 `SourceVersion` 定义；
  CLAUDE.md 规则 2、3、6。

## 一、现状

T13 有两半：

1. **地图版本两个来源不交叉核对**：
   - 证据的地图版本：学习服务写入时按请求的内容版本查 `platform.curriculum_version.map_version_id`（`services/learning/learning/store.py` 的 `map_version_for`），
     写进 `platform.evidence_event.map_version_id`；
   - 适配器自报的地图版本：`source_version` 里的 `graph:` 段（契约 v1.0 允许出现在任何方法的返回里）；
   - 两者从不比对。将来某科适配器按一张地图判分、平台却把证据记在另一张地图上，掌握计算就会用错节点，而且事后看不出来。
   - 今天的实际情况：只有阿鸦的 **plan** 会带 `graph:` 段，而且要内容包设了 `skill_links.map_ref`；当前内容包没设，所以一次都不出现。
     grade、diagnose 三科都不带。也就是说，核对加上之后**眼下不会改变任何一次写入的结果**，它防的是以后。
2. **v0 申诉不写能力观察**：v0 写入已在 LT-04 下线（一律 410），这一条已经没有对象，本任务只在文档上关掉它。
   与它相邻的“申诉期间原来那条 A 级证据要不要临时降级”（`evidence-attributes-sprint1/lessons.md` 第 4 条）属于掌握口径，**不在本任务**。

## 二、默认做法

### A. grade、diagnose 写入时核对（学习服务）
在适配器返回、平台核对（`verify`）之后、落库之前（与“键盘作答出现识别转写即拒收”同一处）：

| 情况 | 判定 | 结果 |
|---|---|---|
| 返回里没有 `graph:` 段 | `absent` | 照常写入（现状） |
| 有 `graph:` 段，请求的内容版本没有绑定地图 | `unbound` | 照常写入，证据的 `map_version_id` 仍为空 |
| 有 `graph:` 段，名称不是 `<本科>-map`，或有两个以上 `graph:` 段 | `mismatch` | **拒收**：502 `ADAPTER_RESULT_REJECTED`（detail 写 `map version mismatch`），不落库 |
| 有 `graph:` 段，版本号与绑定的地图版本（`kge.map_version.version`）不同 | `mismatch` | **拒收**（同上） |
| 版本号相同 | `match` | 照常写入 |

- 拒收走现有“适配器结果被平台核对拒绝”的路径：确定没有写入，BFF 按既有规则释放预留（不新增计量逻辑）。
- 学习服务已有 `kge.map_version` 的 SELECT（授权快照第 1258 行），不新增授权、不改迁移。
- 计数：新指标 `starbuddy_learning_map_version_check_total{method, outcome}`（outcome ∈ match / mismatch / unbound / absent），
  登记进指标清单；日志只记 outcome，不记版本号以外的内容。

### B. plan 只计数、不拒收
`/internal/v1/plan`（今日计划，只读、不落库）汇总各科 plan 时做同样的判定，只计数（method = plan），不改返回：计划不写证据，
拒收会让学生当天没有计划，代价大于收益。

### C. 测试与变异
- 学习服务单测：五种情况各一例（假适配器返回带或不带 `graph:` 段）；拒收时证据、作答、判分表行数不变、幂等记录可重试；`match` 时证据的 `map_version_id` 与绑定一致。
- plan：带不一致的 `graph:` 段时返回不变、计数加一。
- 变异：去掉版本比对、去掉名称核对、把 mismatch 当 match、拒收变成只记日志，至少四条，接进 `internal-v1-mutation`。
- 契约一致性测试不动（不改 packages/contracts）。

### D. 文档
- `specs/evidence-attributes-sprint1/lessons.md` 第 6、10 条加结论附注；W13 表 T13 标注；指标清单与告警（不新增告警规则：mismatch 只会在适配器升级时出现，由该次发布的走查发现）。

## 三、范围与不做
- 做：A–D。
- 不做：申诉期间原证据降级（掌握口径，要人定）；要求适配器必须带 `graph:` 段（改契约，必停）；回填已有证据的地图版本。

## 四、必停与 PR
- 不改 db/migrations、RLS 与权限、packages/contracts、计量逻辑、判分与掌握计算（只是在落库前多一道拒收核对，判分本身不变）。
  一个提交，不开 PR（沿用用户的安排）。

## 五、验收
- 第二节表格的五种情况都有用例并通过；拒收不落库；plan 不受影响只计数。
- 指标清单核对（observability-check）、`learning-test`、`internal-v1-mutation` 全部通过。

## 六、待人确认（括号里是默认）
1. **不一致时怎么办**：grade、diagnose 拒收（502，不落库）（默认）；还是只记计数和日志、照常写入？
   拒收更安全（证据不会记到错的地图上），代价是适配器与平台地图版本不同步的那段时间该科写入全部失败。
2. **内容版本没绑地图、适配器却声明了地图（unbound）**：照常写入（默认）；还是也拒收？
3. **plan**：只计数、不拒收（默认）；还是也拒收（那一科当天显示“暂无安排”）？
4. **T13 的另一半**：v0 申诉一条因 v0 已下线直接关掉（默认）；“申诉期间原证据是否临时降级”另起任务，由教研与掌握口径负责人定。
