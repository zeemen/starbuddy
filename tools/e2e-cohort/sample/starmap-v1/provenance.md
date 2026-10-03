# 星图 v1 截图来历（未启用）

- 截图状态：captured（**未启用**：开关 starmap_v1 / starmap_dashed 默认关，生产启用待 ADR 11 签字、数学地图发布、判断生成接入调度）
- 截图对应的页面版本：星图 v1（H5），test 环境、开关打开、合成数据（db/seeds/dev/star-map-v1-synthetic.sql）
- 截取提交号：66d7e304ed87514b68e42e7c3708024c6afa5ec3（工作区有未提交改动）
- 生成方式：`make gate0-starmap-v1`（apps/web/e2e/visual-v1.spec.ts 的 @gate0 用例；视口 1024×768、DPR 1、浅色、整页、PNG）
- 与 v0 的关系：不替换 `../starmap/` 的 v0 截图（spec 待人确认 #7）；Gate 0 判定不因本截图改变
- 像素哈希：见 `starmap-v1.json` 的 `pixel_sha256`（RGBA 解码后求 SHA-256；同一容器与浏览器版本下应稳定）
