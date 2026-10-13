# 舞台第一步 · 实现记录

2026-10-13 起由 Claude 按用户授权自主推进（计划原排阶段 1 · 2 月，提前做接口层）；零新依赖，不改契约，不接 teach、BFF、计量。

## 交付
- `packages/stage`（零依赖，react 为同伴依赖、源码不 import react）：
  - DSL 0.1.0 类型与 JSON Schema：顶层 `{dsl_version, title, canvas, roles, steps}`；七种动作 write_text、write_formula、draw_line、draw_shape、highlight、clear、speaker；无颜色、样式、链接、图片、脚本字段。
  - 确定性校验器：入口先取纯数据快照（不可序列化即拒绝 `not_serializable`），之后只读快照；字段白名单、步数与长度上限、拒绝不可见字符（含落单代理项、私用区、未分配码位）、HTML、链接与脚本写法、禁用 LaTeX 命令；坐标整数且在画布内；引用与 id 唯一性；合格产物深冻结并登记，白板只渲染登记过的对象。
  - 纯函数播放状态机 idle→playing→paused→ended；SVG 白板（白板 ≥ 65% 舞台高度，三套布局令牌深冻结，讲解权用 1.25 线表示，颜色只用 currentColor，无 style、无链接，不读写存储、不发请求）。
- `apps/web` 开发样张页 `/dev/stage`（构建期只在 `STARBUDDY_DEV_PAGES=1` 且 dev/test 时成为页面，运行期另核环境与本机 Host）；生产构建产物不含样张页与 packages/stage（测试断言）。
- 测试：stage 单测、58 个变异全部发现；web 单测（含 build-output）、相关 e2e 19/19、license-check 通过。

## 审查意见的处理（按默认）
- 安全审查无阻断。采纳：校验入口纯数据快照（防 getter / Proxy 两次读取绕过）；next.config.ts 的仓库根推算找不到 packages/stage 即报错；断言 apps/web/src 指向仓库外的相对导入只允许 packages/stage 的两个入口；不可见字符扩充；布局令牌深冻结。

## 与 spec 及 ADR 07 的偏差
1. 渲染器为最小自研实现（ADR 07 选项 C 形态），ADR 07 已采纳的选项 A（锁定 MIT 版本 OpenMAIC 渲染层）待人确认版本号与传递依赖许可证后替换内部实现，对外接口不变。
2. `apps/web/next.config.ts` 在开发、测试构建中把 Turbopack 根目录放到仓库根，使样张页能引用 packages/stage；生产构建不变。

## 待人确认
1. 上述偏差 1：何时、以哪个 MIT 版本引入 `@openmaic/*` 渲染层（禁止 v0.3.0 以前的 AGPL 版本、禁止 mathml2omml），许可证扫描工具选型（ADR 07 #25）。
2. 上述偏差 2：接受 next.config.ts 的做法，或改为 npm 工作区。
3. 布局令牌坐标为按视觉家族规范自拟；规范图手机竖屏白板约 56%，实现按三套都 ≥ 65%，需视觉组确认。
4. 角色占位框文字只是开发占位，不是出场文案；点点占位框在 roles 含 buddy 时一直显示。
5. 文字安全规则偏保守（如 `x<y` 须写成 `x < y`，任何 `//` 都拒）；`\p{Cn}` 取决于 Node 的 Unicode 版本，较新的字符会被当成未分配码位拒绝。
6. 公式仍为纯文本排版；引入 KaTeX（MIT）需另走依赖许可审批（present-item lessons #5）。
7. 不在本步：契约给 `TeachSegment.dsl` 定结构、适配器输出 `whiteboard_dsl`、讲解台词与角色出场规则（人设）、`lesson_mode` 分档与 `lesson_whiteboard` 扣次（计量）、DSL 发布闸门与教研审核、插话转实时（模型），均属必停另起。
8. 子代理顺带发现 `apps/web/src/lib/rich-item.ts` 的 `latexToText` 用 `name in SYMBOL` 判断命令，原型链名字（`\toString` 等）会输出函数源码且被放行；已建议另起任务修复并核对 BFF 同类实现。
