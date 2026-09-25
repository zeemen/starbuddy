# 国庆前就绪检查（2026-09-25）

依据：开发计划 v1.0 第十章“国庆前可以完成的十件事”，以及 ClaudeCode 执行方案 v1.0 第八章“准备（国庆前）”。
口径：只按仓库里能验证的事实打标。仓库外的事项（人员、会议、合同）标“仓库外，无法核验”，不猜。

## 一、开发计划第十章：十件事

| # | 事项 | 状态 | 证据 / 说明 |
|---|---|---|---|
| 1 | 确认团队配置，指定技术负责人与四科盘点负责人 | 仓库外，无法核验 | ClaudeCode 执行方案改为“产品负责人 + 资深工程师 + Claude Code”的最小团队，需确认采用哪一版 |
| 2 | 下发盘点模板，约定 W1 周五交稿 | 部分完成 | 模板已在 `.claude/skills/subject-inventory`；阿鸦、文砺已由 Claude Code 起草盘点（见 `specs/inventory-*`），需要各科负责人确认 |
| 3 | 开通平台单仓与三套环境；集中四科仓库只读访问 | 部分完成 | 单仓已建立。四科仓库：阿鸦 → `zeemen/ayaheshui`、文砺 → `zeemen/wenli` 已克隆到 `legacy/`（软链、gitignored）；**知行、开口成章没有找到对应仓库**。dev / staging / pilot 环境：仓库里没有配置，无法核验 |
| 4 | 联系外部合规顾问 | 仓库外，无法核验 | — |
| 5 | 排定 W2 周三七项待拍板决策会 | 仓库外，无法核验 | 七项议题见完整方案第十五章 |
| 6 | 起草 ADR 01–08 的标题与决策人 | **已起草，待确认** | `specs/adr-01-08/spec.md` |
| 7 | 教研审阅错因跨科映射草稿 | 未开始 | 仓库里还没有 `docs/mapping/error-taxonomy.md` 草稿，教研无稿可审 |
| 8 | 视觉：阿鸦母版定稿 | 仓库外 | 有视觉家族规范 v1.2；新增落地页原型 `docs/design/落地页原型-北斗七科-v0.html`，仅作参考 |
| 9 | 语音工程整理开口成章语音链供应商与数据流向 | 阻塞 | 缺开口成章代码仓库 |
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
2. 告知知行（道法）与开口成章（英语）的代码仓库地址。
3. 确认 Supabase 连接器指向的项目。
4. `Downloads/` 下两个 zip（启动包与文档包，1.1 MB）内容已经展开到仓库里，是否删除？（我没有删）
5. `zeemen/ai-yuwen` 看起来也是语文相关仓库，它和文砺是什么关系，要不要一起盘点？
