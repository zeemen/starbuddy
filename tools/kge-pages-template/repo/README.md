# StarBuddy 编译层页面仓库（试点期）

由 StarBuddy 仓库 `tools/kge-pages-template/` 拷贝而来。页面格式与机器规则见 StarBuddy 仓库
`docs/design/kge/codification-math-v1.md`；分支保护见模板的 `BRANCH_PROTECTION.md`；教研操作见 `OPERATIONS.md`。

- `pages/kp/<节点 ID>.md`：知识点页（页面 ID = 节点 ID）。
- `pages/material/<节点 ID>.md`：素材卡（KGE-07 第一步，挂素材节点；原文段只放原句占位）。体检已覆盖；
  快照导出与 `page.get` 目前只收知识点页，素材卡合入主分支前须平台负责人确认导出路径（StarBuddy 仓库 KGE-07 待人确认）。
- 每个合并请求都会跑 `main` 上的 `ci/lint.sh`（程序体检，地图快照与人设词表也取自 `main`）；有阻断不能合并。
- `ci/`、`.github/`、`snapshots/`、`lexicon/`（GitLab 另有 `.gitlab-ci.yml`）的改动由平台负责人审核（`.github/CODEOWNERS`）。
- 本仓库只放公共层页面，永远不放作答、笔记、错因、学情档案（规则 13）。
