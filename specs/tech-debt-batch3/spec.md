# 技术债第三批（W13 第三节 T17、T01 / T15、T10、T05、T22、T26、T34、T52）· 规格草稿

- 状态：**草稿，待人确认**（第五节）。不涉及必停项：不改迁移、RLS 与权限策略、计量、契约、判分与掌握、人设与禁语表、金标数据，不新增第三方。
- 背景：PLT-24 做完后，2026-11-09 逐项核对了 W13 技术债表（`docs/gates/w13-debt-and-decisions.md` 第 257–308 行，写于 10 月初，之后没更新）。
  T04、T06–T09、T12、T18–T21、T41、T46、T47、T49 已做完；其余大多要人（教研签字、法务、产品阈值、真实集群）或碰必停。
  剩下“不碰必停、不需要人、在仓库里能直接做”的，就是本批这几项。
- 依据：各项来源见下；CLAUDE.md 规则 1、8 与必停清单。

## 一、范围

### A. T17（剩余）：BFF 函数越权矩阵扩到 insight schema（硬 2 回归深度）
- 现状：`db/tests/80_attack_surface.sql` 第六节的矩阵只循环 `platform` schema（第 578 行 `p.pronamespace = 'platform'::regnamespace`）；
  insight 下授给 BFF 的 SECURITY DEFINER 函数由 79o、79m 等逐条手写“另一户 / 不存在同一个 42501”。复核、调度角色的矩阵、写矩阵、按主键比较已有。
- 做法：矩阵改为循环全部应用 schema（`t.app_schemas()`）里 BFF 可执行、第一个参数是学生（或家庭、预留）的定义者函数；
  `insight.onboarding_view` 这类“无权返回 NULL”的函数按 spec 来源（`specs/sie-03-dispute-reconcile/lessons.md` 第 67–68 行）加一类特例：
  另一户与不存在都返回 NULL 即算通过；其余登记进 `t.function_matrix_exempt` 并写明理由。
- 变异：`db/tests/mutations.sh` 至少两条（insight 下某函数对另一户返回数据、矩阵退回只看 platform），断言 80 号发现。
- 只改 `db/tests/`，不改迁移与授权快照（不新增授权）。

### B. T01 / T15：发布闸门拒收带 C9 标记的内容包
- 现状：阿鸦内容包在非严格模式下可以带 `hint_scan.flagged_items`（提示疑似含答案的题），teach 仍会返回原文；
  `specs/release-gate-sprint4/lessons.md` 第一部分待人确认 #5 的默认是“只在本地迭代时用非严格，发布闸门拒绝 `flagged_items` 非空的内容包”，第二部分 #7 记“本 PR 未做”。
- 做法：发布闸门（`services/learning/review_console/admission_export.py` 的 verify，与 `tools/release-walkthrough`）对 `hint_scan.mode != strict`
  或 `flagged_items` 非空的内容包拒绝（退出码与现有拒绝一致，消息列出题目 ID，不打印提示原文）；单测与走查用例各补正反两例；变异至少一条。
- 不改适配器的 teach 行为（plan、teach 跳过 rejected 题是方法行为变更，T15 另一半，要人定）。

### C. T10：联调样例在干净工作区重生
- 现状：`tools/e2e-cohort/sample/run_metadata.json` 记 `worktree_dirty: true`（第 34、41 行），不能证明样例来自某个提交。
- 做法：在干净的工作区（独立 worktree，与当前分支同一提交）跑 `make gate0-evidence`，替换 `tools/e2e-cohort/sample/`（星图截图不重截：不带 `GATE0_STARMAP=1`，
  沿用已有截图与 provenance）；提交说明写明生成用的提交哈希。只含合成数据。

### D. T05：CI 首次真实运行的收口记录
- 2026-11-09 手动触发的 check（light，run 42564677313）11 个分组全部通过：Ubuntu 24.04 上 postgresql-16 安装、非 root 一次性库都已验证。
- 做法：在 `specs/contract-conformance-sprint1/lessons.md` 第 47 行的条目与 W13 表上记“已核验”和 run 编号；`docs/gates/gate-0-evidence.md`
  第 149 行（S3a）附 run 编号；同文件第 113、153 行已过时的说法（0039 已有 `change_admission`、T33 快照摘要已做）改正。
- 变异组（`scope=mutation`，约 500 计费分钟）**不在本批触发**，见待人确认第 3 条。

### E. T22：WebKit、Firefox 跑数据权利用例
- 现状：`apps/web/playwright.config.ts` 第 37–38 行已支持 `STARBUDDY_E2E_BROWSERS=webkit,firefox`（只跑标 `@data-rights` 的两个用例，
  `e2e/trust-on.spec.ts` 第 172、250 行），但从没跑过：CI 只装 chromium，本机只有预装的 Chromium、不能另装浏览器。
- 做法：CI 的 check.yml 在手动触发时多一个输入 `extra_browsers`（缺省关），打开时只在 bff-web 分组安装 webkit、firefox 并设
  `STARBUDDY_E2E_BROWSERS`；缺省行为不变。本机只验证 `--list` 能按项目选中这两个用例、workflow 结构校验通过。
  首次在 CI 跑由人触发（会花 CI 分钟，见待人确认第 3 条）。

### F. 三项小活
1. **T26**：BFF 的事件词表检查脚本 `services/bff/scripts/check_event_lexicon.py` 并入 `make sie-dictionary-check`（行为事件词表与字段词典同一处检查），
   不改词表内容；删掉 `make web-visual` 前多余的那次生产构建（来源 `specs/behavior-events-sprint2/lessons.md` 第 49 行）。
2. **T34**：道法判分转写脚本：把适配器 `grade` 返回（JSON，逐维度结果）转成 `evals/daofa-grading/grading.py` 要的适配器 CSV（`dimension_id` = 采分点 `point_id`）；
   脚本放 `evals/daofa-grading/` 的工具位置（不碰 `gold/` 与夹具），用合成判分结果做单测，并入 `make daofa-grading-eval-test`。
3. **T52（模板部分）**：`specs/_template/spec.md` 补 ADR 14 要求的“输入契约”“禁止修改的范围”两节（只改模板，不改 CLAUDE.md）。

### G. W13 表的现状标注
- 在 `docs/gates/w13-debt-and-decisions.md` 第三节表后加“2026-11-09 核对”一节：逐项写 已完成 / 部分完成（剩什么）/ 未做，以及卡在人还是必停，
  附证据位置（本批核对的结论）；表本身不改行，便于对照历史。

## 二、不做
- T11（登记脚本写 `platform.*`）：迁到 `tools/` 还是写明“内容登记例外”要人定方向（待人确认第 4 条）。
- T13 地图版本交叉核对：碰证据写入路径，即使只记日志也要先定口径，另起。
- T35 审核台前端类型生成：收益低，排后。
- 其余项要人或必停，见 G 节的标注。

## 三、验收
- A：80 号测试在矩阵扩大后通过；新增变异被发现；授权快照无变化。
- B：admission_export 与 release-walkthrough 的新用例通过；变异被发现；现有内容包（严格模式）照常通过。
- C：样例 `run_metadata.json` 的 `worktree_dirty` 为 false，提交哈希可查；`make e2e-cohort` 通过。
- D、G：文档改动与证据位置一一对应。
- E：本机 `STARBUDDY_E2E_BROWSERS=webkit,firefox npx playwright test --list` 在两个新项目下只列出两个 @data-rights 用例；check.yml 结构校验通过；缺省（不选 extra_browsers）行为不变。
- F：`make sie-dictionary-check`、`make daofa-grading-eval-test` 通过。
- 严格模式 `make check-light` 中涉及的目标与变异（db 的相关分片、learning 发布闸门、web）通过。

## 四、PR
- 一个提交（或按 A–G 分几个小提交），不涉及必停，首行不写 `[NEEDS-HUMAN]`；PR 先不开（沿用用户的安排）。

## 五、待人确认（括号里是默认）
1. **范围**：A–G 一起做（默认）；还是先只做 A、B（与上线风险最相关的两项）？
2. **B 的拒收口径**：非严格模式或 `flagged_items` 非空一律拒收（默认）；还是只拒收 `flagged_items` 非空、允许“非严格但零标记”？
3. **CI 花费**：本批不触发 `scope=mutation`（约 500 分钟）与 E 的额外浏览器；由你决定何时手动触发（默认）。还是本批做完后我就各触发一次？
4. **T11 方向**：三科 `register-content.ts` 迁到 `tools/content-register/`（内容登记属平台运维工具，不是适配器运行时）并在规则 1 的说明里注明（默认，下批做）；
   还是保留在适配器目录、只在 CLAUDE.md 加“内容登记脚本例外”（改 CLAUDE.md 要你点头）？
