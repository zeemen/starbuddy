# 讲评课在 staging 打开 · 实现记录

## PR 0：修阿鸦镜像
### 做了什么
- `deploy/docker/adapter.Dockerfile`：运行阶段拷 `packages/stage/package.json` 与 `packages/stage/src`，保持仓库相对结构；dockerignore 白名单放行这两项。
  - `package.json` 必须拷：它声明 `"type": "module"`，Node 按它决定 `.ts` 文件按 ES 模块加载；
  - `schema` 运行时不读，不拷；
  - 三科共用一份镜像定义，知行、文砺不导入它，多拷无害。
- `deploy/tests/test_static.py` 新增两条，管反方向：镜像里的源码导入的东西，都在同一阶段拷进来了。
  - `test_image_relative_imports_are_copied`：
    - 逐个 Dockerfile、逐个阶段（适配器按三科展开 `${ADAPTER}`），把 COPY 换算成“仓库里的来源 → 镜像里的位置”；
    - 扫描拷进来的 `.ts` / `.tsx` / `.mjs` / `.js`（跳过 node_modules 与测试），把相对导入按镜像里的位置解析；
    - 目标必须落在这个阶段拷进来的东西里，并且仓库里真有其文件；`COPY --from` 来的只算“在镜像里”。
  - `test_image_relative_imports_check_catches_missing_package`：反例。去掉阿鸦镜像的 `packages/stage`、只拷 `package.json` 不拷 `src`、去掉 web 构建阶段的 `packages/stage/src`，都要报出来。

### 验证
- 新测试在修复前的 Dockerfile 上报出阿鸦两处导入（`src/lessons.ts`、`src/methods/teach.ts` → `packages/stage/src/index.ts`），修复后通过；`test_static.py` 33 条全过。
- 按镜像目录结构实测启动阿鸦（运行依赖照 deps 阶段 `npm ci --omit=dev --ignore-scripts` 安装）：
  - 修复前：`ERR_MODULE_NOT_FOUND`；
  - 修复后：越过模块加载，给合成服务令牌后端口正常监听。
- `make deploy-check`（严格模式）通过，`deploy/tests` 335 条全过。镜像构建（`DEPLOY_BUILD=1`）本机没有 docker 守护进程，没有执行。

### 经验
- 镜像构建不在 CI 里时，“源码导入了镜像里没有的目录”只有真起镜像才会暴露。静态测试把它提前到每次 PR。
- 这次的根因是 ③a 给阿鸦加了仓库外导入，却没有同时改镜像定义。新测试会直接拦住这类遗漏。

### 待人确认
- 无。
