# 舞台进生产构建：`packages/stage` 怎么进 apps/web 的生产构建 · 规格草稿

- 状态：**已确认（2026-11-27，用户“按建议，默认值都同意，开始实现”）**：第五节四条都按默认。实现记录见 lessons.md。
- 来源：`specs/stage-lesson-session` lessons ③b-5 待人确认 L10：讲评课页面只能做成开发页，因为生产构建解析不到 `packages/stage`；上 staging 之前要定办法。
  更早的同一问题：`specs/stage-whiteboard-step1` lessons 待人确认第 2 条（“接受 next.config.ts 的做法，或改为 npm 工作区”），当时没有定。
  用户 2026-11-27 说“按建议起草 L10 的规格”。
- 依据：
  - ADR 07 决定 7：舞台放在 `packages/stage`，其他模块只通过它的接口使用；决定 1、6：将来锁定 MIT 版本的 OpenMAIC 渲染层，引入依赖要过许可证检查；
  - `apps/web/next.config.ts`：开发专用页面与 Turbopack 根目录的现有写法；
  - `deploy/docker/web.Dockerfile`：构建阶段已经拷了 `packages/stage` 的 `package.json`、`src`、`schema`，运行阶段没拷；
  - CLAUDE.md 规则 9、10。

## 一、现状（2026-11-27 核对）
- **引用方式**：
  - `apps/web` 以相对路径引用 `packages/stage` 的两个接口文件（`src/index.ts`、`src/Whiteboard.tsx`），没有 npm 工作区，也没有包依赖；
  - 只有两条链路引用它：样张页 `/dev/stage` 与讲评课页面，都是开发页（`page.dev.tsx`）；
  - `tests/stage.test.ts` 锁定了这两条链路，并限定 `apps/web/src` 指向仓库外的相对导入只能是这两个文件。
- **`packages/stage` 零依赖**：
  - 只有同伴依赖 react，源码不 import react，由调用方把 `React.createElement` 传进 `createWhiteboard`；
  - 它的类型检查借用 `apps/web/node_modules/.bin/tsc`（`make stage-test`）。
- **Turbopack 根目录**：
  - 默认是 `apps/web`（锁文件所在目录），根目录以外的文件解析不到；
  - `next.config.ts` 只在含开发页的构建里（`STARBUDDY_DEV_PAGES=1` 且 dev / test）把根目录放到仓库根。
- **实测（2026-11-27，本机，临时改动已还原）**：
  - 把讲评课路由临时改成 `page.tsx`、按生产口径构建（不设 `STARBUDDY_DEV_PAGES`）：
    - 默认根目录：失败，`Module not found: Can't resolve '../../../../packages/stage/src/index.ts'`（`Whiteboard.tsx` 同样）；
    - 根目录放到仓库根：成功，路由表里有 `/s/[studentId]/learn/[subject]/lesson/[lessonId]`，没有 `/dev/*`。
- **运行阶段也读 `next.config.ts`**：
  - `next start` 会加载配置文件，而运行阶段的镜像里没有 `packages/stage`；
  - `repoRoot()` 找不到 `packages/stage/package.json` 会抛错（失败即停）。所以生产构建一律放宽根目录的话，镜像会起不来。
- **构建产物检查**：`tests/build-output.test.ts` 断言生产构建产物里没有 `data-stage-page`、`data-stage-demo`、`data-stage-part` 与合成样例标题。讲评课页面进入生产构建后，白板的属性名会出现在产物里。

## 二、可选做法

| | A. 根目录放到仓库根（只在构建与开发阶段） | B. npm 工作区 | C. `file:` 依赖并拷进 node_modules | D. 构建前复制进 apps/web |
|---|---|---|---|---|
| 怎么做 | `turbopack.root` 在构建与开发阶段一律放到仓库根；`next start` 不放 | 仓库根加 `package.json`（`workspaces`）与一份锁文件，`apps/web` 依赖 `@starbuddy/stage` | `apps/web/package.json` 加 `"@starbuddy/stage": "file:../../packages/stage"`，`install-links` 拷进 node_modules，`transpilePackages` 编译它 | 构建前脚本把 `packages/stage/src` 拷到 `apps/web` 下的生成目录，核对哈希 |
| 改动量 | 小：`next.config.ts`、两个测试、Dockerfile 注释 | 大：锁文件挪位置，`scripts/bootstrap-deps.sh`、CI、Dockerfile、许可证检查都要改；仓库里其他 Node 包各有锁文件，口径不一 | 中：锁文件多一项；Dockerfile 要在 `npm ci` 之前拷 `packages/stage` | 中：多一个脚本，多一份生成物 |
| 开发体验 | 改了 stage 立刻生效，与现在一样 | 立刻生效 | 改了 stage 要重新 `npm install`，否则用的是旧副本 | 要重跑复制 |
| 将来 stage 有了 npm 依赖（OpenMAIC、KaTeX） | 要另定：stage 自己装依赖并有锁文件，或那时改 B / C | 自然支持，依赖提升到根 | 自然支持，依赖记进 `apps/web` 的锁文件，许可证检查照常扫到 | 要另定 |
| 解析范围 | 放宽到整个仓库，靠现有静态测试只放行 stage 的两个接口文件 | 只解析声明的包 | 只解析声明的包 | 只解析 apps/web |

## 三、默认做法：选 A，并写明什么时候改
1. **`next.config.ts`**：
   - 改成按阶段返回配置的函数：构建（`phase-production-build`）与开发（`phase-development-server`）两个阶段，一律把 Turbopack 根目录放到仓库根；
   - `next start`（`phase-production-server`）不放宽，也不调用 `repoRoot()`，运行镜像里没有 `packages/stage` 照样能起；
   - `repoRoot()` 仍然失败即停：构建时算出的目录里没有 `packages/stage/package.json` 就报错。
2. **讲评课路由转成普通页面**：
   - `page.dev.tsx` 改为 `page.tsx`，去掉 `devRuntimeAllowed(host)` 那一道；
   - 开关仍是 `STARBUDDY_LESSON`，仍只在 dev / test 生效：staging、pilot、生产构建里有这个路由，但开关关，一律 404。BFF 与学习服务的讲评课开关在 staging 照旧拒绝启动。
   - 样张页 `/dev/stage` 仍是开发页，不进生产构建。
3. **测试**：
   - `tests/stage.test.ts`：引用 `packages/stage` 的链路改为样张页（开发页）与讲评课页面（普通页）；仓库外相对导入只放行两个接口文件，这条不变，并加一条：`apps/web/src` 不得有指向仓库其他目录（`services/`、`adapters/`、`db/`、`docs/` 等）的导入；
   - `tests/build-output.test.ts`：生产构建产物里没有 `/dev/stage`、样张页标记（`data-stage-page`、`data-stage-demo`）与合成样例标题；白板的属性名（`data-stage-part` 等）允许出现；讲评课页面在生产构建里存在；
   - `next.config.ts` 的单测：三个阶段的根目录取值；`next start` 阶段即使没有 `packages/stage` 也不抛错；
   - 端到端照旧：开关关时讲评课页面 404（`practice-off.spec.ts`），开关开时那一套照常。
4. **Dockerfile**：构建阶段已经拷了 `packages/stage`，不用改，只更新注释；运行阶段不拷 `packages/stage`，构建产物里已经含有编译后的代码。
5. **什么时候改 B 或 C**：`packages/stage` 第一次要引入 npm 依赖时（OpenMAIC 渲染层或 KaTeX，ADR 07 决定 1、3、6），在那次的依赖审批里一并定。默认倾向 C：依赖记进 `apps/web` 的锁文件，现有许可证检查直接覆盖。

## 四、不做
- 讲评课在 staging 打开：要改 BFF 与学习服务的开关口径、开关矩阵、内容发布闸门与额度，另起规格。
- npm 工作区（B）、`file:` 依赖（C）：见第三节第 5 条。
- 样张页转为普通页面。
- 换掉自研白板（ADR 07 选项 A 的 OpenMAIC 渲染层）。

## 五、待人确认（括号里是默认）
1. 选哪种做法（A；`packages/stage` 第一次引入 npm 依赖时，在那次依赖审批里重新定，倾向 C）。
2. 讲评课路由是否在本步转成普通页面（转；开关仍只 dev / test，staging 构建里有这个路由但一律 404）。也可以只改构建机制，等决定在 staging 打开时再转。
3. 生产构建产物里允许出现白板的属性名（允许；样张页标记与合成样例仍然不允许）。
4. 开发构建是否同样一律放宽根目录（是；以前只在含开发页时放宽，现在构建与开发两个阶段统一）。

## 六、验收
- 生产口径构建（不设 `STARBUDDY_DEV_PAGES`）成功，路由表有讲评课页面、没有 `/dev/*`；
- 运行阶段（`next start`）在没有 `packages/stage` 的目录里能起，`/readyz` 200；
- `make web-test`（含 build-output）、`make stage-test`、`make deploy-check` 通过；
- 端到端：讲评课开关关时页面 404，开时 `lesson.spec.ts` 照常通过；
- 有条件时（`DEPLOY_BUILD=1`）用 `deploy/docker/web.Dockerfile` 构建一次镜像并启动，确认 `/readyz` 200。
