# 舞台进生产构建 · 实现记录

## 交付
- `apps/web/next.config.ts`：
  - 默认导出改成按阶段返回配置的函数 `nextConfig(phase)`；
  - `turbopackRootFor(phase)` 只在构建（`phase-production-build`）与开发（`phase-development-server`）两个阶段返回仓库根；
  - 运行阶段（`phase-production-server`）等其余阶段不放宽，也不调用 `repoRoot()`；
  - 其余配置三个阶段相同（安全头那段原文不动，其他测试按原文核对它）。
- 讲评课路由：`page.dev.tsx` 改为 `page.tsx`，去掉 `devRuntimeAllowed(host)`，只挂 `LessonPage`。开关与会话仍在 `LessonPage` 里先后核对。
- 测试：
  - `tests/stage.test.ts`：
    - 三个阶段的根目录取值；
    - 生产口径的构建同样放宽；
    - 三个阶段的配置除 `turbopack` 外相同；
    - 去掉注释后 `repoRoot()` 只调用一处，且只在构建、开发阶段；
    - 讲评课页面组件只由 `page.tsx` 引用。
  - `tests/build-output.test.ts`：
    - 不再把 `data-stage-part` 当成禁止出现的标记，样张页标记与合成样例照旧禁止；
    - 新增一条：生产构建的路由表里有讲评课页面，产物里有白板。
  - `tests/lesson.test.ts`：路由文件是 `page.tsx`，只挂组件，不碰开关、会话、请求头与网络。
  - 安全头巡检：
    - `e2e/crawl-pages.ts` 登记讲评课页面（按 `practiceOn`）；
    - `targets()` 的最终地址统一去掉查询参数，因为巡检比较的是 `pathname`，而讲评课地址带 `?v=&node=`；
    - `e2e/lesson.spec.ts` 加一条：开关打开时讲评课页面的安全头。
- `deploy/docker/web.Dockerfile`：只改注释，构建阶段本来就拷了 `packages/stage`。

## 验证
- 生产口径 `next build`（不设 `STARBUDDY_DEV_PAGES`、`STARBUDDY_ENV`）成功，路由表有讲评课页面，没有 `/dev/*`；
  `make web-test` 里端到端之前的各步都通过：许可证检查、三项产物 grep、单测 299 条（含 build-output 4 条）。
- 运行阶段：
  - 把构建产物拷到一个上两级没有 `packages/stage` 的目录里起 `next start`，按测试环境设好运行环境变量后 `/readyz` 200；
  - 讲评课地址在开关关时是 404。
- 端到端：
  - lesson、practice-off、headers、mobile（不含 `@visual`）18 条通过；
  - 全量（不含 `@visual`）的结果见 PR。
- `make stage-test`、`make deploy-check` 通过。镜像构建（`DEPLOY_BUILD=1`）本机没有 docker 守护进程，没有执行。

## 与 spec 的差异
- 第三节第 3 条要加的“`apps/web/src` 不得导入仓库其他目录”，现有测试已经覆盖：仓库外的相对导入只放行 stage 的两个接口文件，变异里也有改到 `services/bff` 的一条。所以没有另写。
- 安全头巡检登记表的 `targets()` 去掉查询参数：规格没写，实现时发现。

## 经验
- `next start` 也会加载 `next.config.ts`。配置里会碰文件系统的部分，要按阶段区分，否则构建能过、镜像起不来。
- 带查询参数的页面登记进巡检表时，要注意巡检比较的是 `pathname`。

## 待人确认
- 无新增。讲评课在 staging 打开另起规格（spec 第四节）。
