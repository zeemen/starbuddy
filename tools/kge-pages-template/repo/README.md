# StarBuddy 编译层页面仓库（试点期）

由 StarBuddy 仓库 `tools/kge-pages-template/` 拷贝而来。页面格式与机器规则见 StarBuddy 仓库
`docs/design/kge/codification-math-v1.md`；分支保护见模板的 `BRANCH_PROTECTION.md`；教研操作见 `OPERATIONS.md`。

- `pages/kp/<节点 ID>.md`：知识点页（页面 ID = 节点 ID）。
- 每个合并请求都会跑 `ci/lint.sh`（程序体检）；有阻断不能合并。
- 本仓库只放公共层页面，永远不放作答、笔记、错因、学情档案（规则 13）。
