# 技术债第三批 · 实现记录

2026-11-09 用户确认（“按建议，默认值都同意，开始实现”）：spec 第五节待人确认 1–4 全部按默认——A–G 一起做；发布门禁对非严格模式或带标记的
C9 检查一律拒收；CI 的变异组与额外浏览器由人决定何时手动触发；T11 迁到 `tools/content-register/`，下批做。不涉及必停项。

## 交付

### A. T17：函数越权矩阵扩到全部应用 schema
- `db/tests/80_attack_surface.sql` 第六节：矩阵从 `platform` 改为 `t.app_schemas()`，只收“第一个参数是学生 / 预留 / 家庭、且只有这一个 uuid 参数”的
  BFF 可执行定义者函数（与同一节“未登记”核对的口径一致：多个 uuid 参数的函数必须登记豁免）。
- 新增 `t.function_matrix_null_ok`：insight 下四个“无权返回 NULL”的视图函数（`onboarding_view`、`guardian_goal_view`、`monthly_plan_guardian_view`、
  `monthly_plan_student_view`），另一户与不存在都必须返回 NULL；不登记的新视图函数按 42501 口径核对。
- `t.dummy_arg` 补 `jsonb`、`text[]`、`platform.subject[]`；矩阵失败消息带前三处不一致（之前只有计数，NOTICE 在测试输出里看不到）。
- 扩大后第一次跑：60 处不一致全部来自上面四个视图函数（返回 NULL 而不是 42501，口径本身没有泄露）；其余 insight 函数（建档自述、考试记录、
  诉求等）对另一户与不存在都是同一个 42501。
- 变异：`攻击面：insight 新函数对另一户返回数据`（旧矩阵只看 platform，会漏）、`攻击面：返回 NULL 的视图函数对另一户返回数据`，
  连同原有的两条函数矩阵变异，都按登记的断言被 80 号发现。授权快照无变化。

### B. T01 / T15：发布门禁拒收带 C9 标记的内容包
- `services/learning/review_console/admission_export.py` 的 verify：内容包缺 `hint_scan`、检查项不是 C9、不是严格模式、`flagged_items` 不是数组，
  记一条 `hint_scan_not_strict`；每道被标记的题记一条 `hint_scan_flagged`（只有题号）。这两类排在准入逐题比较的问题前面，任何一条都让 verify 退出码 1。
  报告新增 `hint_scan: {mode, flagged_count}`；不输出提示原文。
- 单测：6 种 C9 状态（含缺失）各一例、库上命令行一例（同一份重建包改成非严格且带标记 → 1）。`review-console-mutation` 新增“发布门禁 C9”组 5 条全部被发现。
- 发布走查样例 `tools/release-walkthrough/sample/admission-verify.json` 随之多了 `hint_scan` 摘要（重生样例，只这一处变化）。
- 适配器的 teach 行为不变（plan、teach 跳过 rejected 题仍待人定）。

### C. T10：联调样例在干净工作区重生
- `git worktree add --detach` 在提交 `cce6263` 起干净工作区（依赖目录用符号链接接回，不计入 `--untracked-files=no`），跑 `make gate0-evidence`。
  结果除 `commit` 与 `worktree_dirty`（true → false）外与原样例**逐字节相同**；只替换 `run_metadata.json`，星图截图沿用。README 补重生做法。

### D. T05 收口
- `specs/contract-conformance-sprint1/lessons.md` 第 4 条附注已核验（run 42564677313）；`docs/gates/gate-0-evidence.md` S3a 附 run 编号与
  `persona-report` 产物（artifact 13650799039）；第 113 行（T33 快照摘要）与 S3b（0039 第二部分）的过时说法加更正。

### E. T22：额外浏览器的 CI 开关
- check.yml 手动触发新增布尔输入 `extra_browsers`（缺省关）与 job `extra-browsers`：只装 webkit、firefox，只跑 `trust-on-webkit`、`trust-on-firefox`
  两个项目（playwright 配置原本就按 `STARBUDDY_E2E_BROWSERS` 生成，只收 `@data-rights`）。`deploy/tests/test_check_workflow.py` 加一条结构核对。
- 本机：`--list` 只列出 2 个用例 × 2 个浏览器；本机没有这两个浏览器，**没有实际运行**，首次运行由人在 Actions 勾选。
  注意：手动触发时 make-check 仍按 scope 跑（只想跑浏览器时选 scope=light 最省）。

### F. 小活
1. T26：`make sie-dictionary-check` 另跑 `services/bff/scripts/check_event_lexicon.py` 与 `--self-test`（脚本解析 BFF 的 events.v0.yaml，仍放在 BFF 目录，
   BFF 单测照旧调用）；`make web-visual` 删去多余的生产构建（e2e.sh 自己做测试构建）。
2. T34：`evals/daofa-grading/transcribe.py`（标准库）：grade 返回（JSON Lines）→ 评测 CSV；错因按证据编号对到维度，一维对上两类即拒绝；
   未判定的点不写（评测按缺点计）；只写 ID 与分数。单测 4 组（含与 `grading.read_marks` 的往返），并入 `make daofa-grading-eval-test`。不碰 `gold/` 与夹具。
3. T52：`specs/_template/spec.md` 补“输入契约”“禁止修改的范围”两节（ADR 14 第 3 节；已有 spec 不补写，ADR 14 待人确认 #2 的默认）。

### G. W13 表现状标注
- `docs/gates/w13-debt-and-decisions.md` 第三节表后加“2026-11-09 核对”一节（逐项现状、卡在人还是必停、依据）。

## 测试结果（本机）
- db-test 全量（63 个迁移 up / down / up，3327 项断言）通过；库上攻击面变异组（`DBMUT_FILTER=攻击面`）49 个全部按登记的断言被发现。
- review-console-mutation“发布门禁 C9”组 5 条全部被发现；admission_export 75 项；release-walkthrough 与样例一致；release-walkthrough 单测 6 项。
- deploy 的 workflow 测试 7 项、sie-dictionary-check、daofa-grading-eval-test（11 项）、attack-surface-check（74 条、539 条引用）、make e2e-cohort 通过。

## 与 spec 的偏差
1. **E**：spec 写“playwright 配置加 webkit、firefox 两个项目”。配置早已支持（`STARBUDDY_E2E_BROWSERS`），只补了 CI 开关与 job，没改配置。
2. **F1**：spec 写“并入 sie-dictionary-check”。脚本没有挪位置，只是由该目标调用（挪走要改 BFF 单测的路径，收益为零）。

## 经验
- 80 号测试里 `RAISE NOTICE` 的明细在 `run.sh` 的输出里看不到（客户端消息级别），排查时把前几处不一致直接拼进 `t.ok` 的消息里最快，已保留为常态。
- 变异要真的改坏行为：`not A or B` 改成 `A and B or ...` 在其余分支仍覆盖时等价，测不出；改成“缺失时当作严格零标记”才是真实的退化。
- `make gate0-evidence` 是确定性的（固定种子）：干净工作区重生只差元数据两项，可以放心按提交重生。

## 待人确认
1. **T22 首次运行**：在 Actions → check → Run workflow 勾选 `extra_browsers`（scope 选 light），约 20–30 分钟；失败时结果补记在这里。
2. **T05 残留**：变异组（scope=mutation，约 500 计费分钟）何时跑，由你定；建议在 PLT-23 的迁移草稿与 PLT-24 合并前各跑一次（规则：改迁移、RLS、鉴权前手动跑）。

## 待人确认的结论（2026-11-09，用户“按建议，待人确认都同意”）
1. T22 首次运行：已手动触发 check（scope=light、extra_browsers=true），run 42637392388（提交 `914932b`）；结果见下。
2. T05 残留：变异组（scope=mutation）在 PLT-23、PLT-24 合并前各跑一次；本批不触发。

## 后续：T11 内容登记脚本迁到 `tools/content-register/`（待人确认 #4 的方向，2026-11-09 实施）
- `git mv adapters/{aya,wenli,zhixing}/scripts/register-content.ts tools/content-register/{aya,wenli,zhixing}.ts`；只改了引用路径与文件头，生成的 SQL 逐字节不变。
  `tools/content-register/package.json`（`type: module`，没有依赖）；类型检查仍由各科 `tsconfig.json` 的 include 带上本科那一个文件。
- 调用点全部改到新位置：BFF 四个集成测试、学习服务三个端到端测试、e2e-cohort 栈、loadtest、release-walkthrough、kge-import 两个库上测试、
  `apps/web/scripts/e2e.sh`、各科 `npm run register:sql`；注释与种子、README 里的路径同步（迁移 0039 的注释不动）。
- 守护：`tools/content-register/tests/placement.test.ts`（适配器目录里除测试、内容包、一致性夹具外没有 `INSERT/UPDATE/DELETE … platform.`；
  三个脚本只引用本科 `src/content.ts`），`make content-register-test` 并入 adapters 分组。阿鸦变异脚本的副本里复制 `tools/content-register/aya.ts`。
- 没有改 CLAUDE.md：规则 1 的字面冲突靠搬位置消除，说明写在 `tools/content-register/README.md`。
- 测试：三科 adapter-*-test、adapter-aya-mutation、content-register-test、学习服务三科端到端、BFF 四个集成测试、release-walkthrough、
  kge-items-test、kge-daofa-import-test、kge-inventory-test、loadtest-test、e2e-cohort 通过（结果见提交说明）。
