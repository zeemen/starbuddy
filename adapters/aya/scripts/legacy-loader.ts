// 只在生成内容包时使用：让 Node 能直接 import legacy 阿鸦的 TS 源码（只读）。
// 1. 把 "@/..." 别名解析到 legacy 根目录；2. 给省略了后缀的相对路径补上 .ts。
import { register } from "node:module";

register(new URL("./legacy-hooks.mjs", import.meta.url));
