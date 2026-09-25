# 国庆前就绪检查（2026-09-25）

依据：开发计划 v1.0 第十章“国庆前可以完成的十件事”，以及 ClaudeCode 执行方案 v1.0 第八章“准备（国庆前）”。
修订：2026-09-25 下午，按远端分支 `docs/sprint0-inventory`（四科盘点，已确认）与 `docs/adr-09-data-residency` 修正了第 2、3、6、9 项。初版漏看了这两条分支。
口径：只按仓库里能验证的事实打标。仓库外的事项（人员、会议、合同）标“仓库外，无法核验”，不猜。

## 一、开发计划第十章：十件事

| # | 事项 | 状态 | 证据 / 说明 |
|---|---|---|---|
| 1 | 确认团队配置，指定技术负责人与四科盘点负责人 | 仓库外，无法核验 | ClaudeCode 执行方案改为“产品负责人 + 资深工程师 + Claude Code”的最小团队，需确认采用哪一版 |
| 2 | 下发盘点模板，约定 W1 周五交稿 | ✅ 提前完成 | 四科盘点、适配器设计 v0.2、decisions 都在分支 `docs/sprint0-inventory`，负责人已于 9/25 确认。**尚未合并到 main**；`specs/inventory-summary.md` 还没有产出 |
| 3 | 开通平台单仓与三套环境；集中四科仓库只读访问 | 部分完成 | 单仓已建立；四科仓库在负责人本地的 `legacy/` 下都有（知行的主体代码在文砺仓库的 `apps/daofa`，开口成章在 `legacy/echo/Ai-english-claude/echo-quest`）。三套环境：ADR 09 已提出按“dev/staging 境外 + 合成数据、pilot/生产境内”分环境，待签字；环境本身还没开 |
| 4 | 联系外部合规顾问 | 仓库外，无法核验 | — |
| 5 | 排定 W2 周三七项待拍板决策会 | 仓库外，无法核验 | 七项议题见完整方案第十五章 |
| 6 | 起草 ADR 01–08 的标题与决策人 | **已起草，待确认** | `specs/adr-01-10/spec.md`（已按 ADR 09 扩到 01–10）；ADR 09 正文已起草（分支 `docs/adr-09-data-residency`） |
| 7 | 教研审阅错因跨科映射草稿 | 未开始 | 仓库里还没有 `docs/mapping/error-taxonomy.md` 草稿，教研无稿可审 |
| 8 | 视觉：阿鸦母版定稿 | 仓库外 | 有视觉家族规范 v1.2；新增落地页原型 `docs/design/落地页原型-北斗七科-v0.html`，仅作参考 |
| 9 | 语音工程整理开口成章语音链供应商与数据流向 | ✅ 现状已查清 | 没有 Gemini 链路，现状是 Anthropic + 浏览器 Web Speech，没有第三方评测（echo decisions #1）。境内 ASR/TTS/评测三家盲测已纳入 ADR 09 |
| 10 | 确认寒假试点目标人数、学科与招募渠道 | 仓库外，无法核验 | 计划默认约 100 户，数学 + 道法 |

## 二、ClaudeCode 执行方案“准备”四条

| 事项 | 状态 | 说明 |
|---|---|---|
| 单仓放入启动包 | ✅ | `.claude/`、`CLAUDE.md`、`specs/_template` 都在 |
| 修改 guard.py 两处生产标识占位符 | ❌ **未完成** | `.claude/hooks/guard.py:14` 仍是 `PROD_PROJECT_REF_PLACEHOLDER` / `PROD_DOMAIN_PLACEHOLDER`，生产拦截目前只对 `--prod` 生效。需要人填入生产 project ref 与域名（Claude Code 不应持有这些信息以外的生产凭据） |
| 开启 GitHub 分支保护 | 未核验 | 需要在 GitHub 设置里确认主干要求 PR 审查 |
| 四科仓库只读克隆到 `legacy/` | 2 / 4 | 见上表第 3 项 |
| 文档放入 `docs/plan`、`docs/personas` | ✅ | 另有 `docs/subjects` 与 `docs/archive` |
| Supabase 新建开发项目，Claude Code 只配这个项目 | 未核验 | 本会话挂了 Supabase 连接器，**需要确认它指向的是开发项目而不是任何生产项目**；确认前 Claude Code 不会调用它 |

## 三、本次顺手修复
- `Makefile.txt` → `Makefile`：原来文件名不对，`make check` 报 “No rule”，`post_edit.sh` 在第 4 行因为找不到 Makefile 而静默跳过所有改动后检查。现在五个目标（check-fast / check / contract-test / eval-persona / eval-grade）都能运行。未实现的目标会明确打印“跳过”，不会伪装成通过。
- `.DS_Store`（3 个）从版本库移除，并加入 `.gitignore`。

## 四、需要人处理的事项
1. 填写 `guard.py` 生产标识。
2. ~~告知知行与开口成章的代码仓库地址~~：已在盘点中解决。
3. 确认 Supabase 连接器指向的项目。
4. `Downloads/` 下两个 zip（启动包与文档包，1.1 MB）内容已经展开到仓库里，是否删除？（我没有删）
5. `zeemen/ai-yuwen` 看起来也是语文相关仓库，它和文砺是什么关系，要不要一起盘点？
6. 合并 `docs/sprint0-inventory` 与 `docs/adr-09-data-residency` 到 main（后续 ADR 与契约起草都要引用它们）。
7. 文砺盘点提到 wenli 仓库根目录有 `starbuddy-secrets-backup`（守卫拦截了读取，内容未核查）。如果里面是真实密钥，需要移出代码仓库并轮换。
