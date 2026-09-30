// 按准入决定重建内容包（不需要 legacy）：读现有 content/pack.json，把全部题目的准入与来源重置为默认，
// 再套用平台导出的准入决定文件，重做 C9 词表检查，写回。题目、答案、提示阶梯、节点等其余内容原样保留。
//
// 用法（在 adapters/aya 下）：
//   npm run rebuild:pack -- --decisions /path/to/admission-decisions.json [--c9 strict|flag] [--report c9.json] [--out ...]
// 决定文件由平台导出、人执行本脚本（spec 待人确认 #9 默认）；文件只含编号与枚举，可以与内容包一起提交到
// content/admission-decisions.json（缺省路径）。同样的输入两次重建逐字节相同。
import { readFileSync } from "node:fs";
import { PACK_FORMAT, type ContentPack } from "../src/content.ts";
import { finishPack, packCliOptions, runCli } from "./pack-cli.ts";

runCli(() => {
  const opts = packCliOptions();
  const current = JSON.parse(readFileSync(opts.pack, "utf8")) as ContentPack;
  if (current.format !== PACK_FORMAT) throw new Error(`${opts.pack} 不是 ${PACK_FORMAT}，请先用 npm run build:pack 从 legacy 生成`);
  finishPack(current, opts, "rebuild-content-pack");
}, "rebuild-content-pack");
