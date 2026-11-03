// 单测共用：合成样例与小工具（全部合成数据）。
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { DEFAULT_CONFIG, type DedupeConfig } from "../src/config.ts";
import type { Item } from "../src/input.ts";

export const TOOL = fileURLToPath(new URL("../", import.meta.url));
export const FIXTURE_PATH = `${TOOL}fixtures/synthetic.json`;
export const FIXTURE_RAW = readFileSync(FIXTURE_PATH, "utf8");

interface RawItem { id: string; text: string; main_nodes: string[]; paper_id?: string; seq?: number }
export const RAW_ITEMS = (JSON.parse(FIXTURE_RAW) as { items: RawItem[] }).items;
export const textOf = (id: string): string => RAW_ITEMS.find((x) => x.id === id)!.text;

/** 已知卷（12 题，按序） */
export const BASE_PAPER = RAW_ITEMS.filter((x) => x.paper_id === "SYN-PAPER-K").sort((a, b) => a.seq! - b.seq!).map((x) => x.text);
/** 与已知卷都不相干的合成题 */
export const OTHER = RAW_ITEMS.filter((x) => x.paper_id === "SYN-PAPER-H" && x.seq! > 6).map((x) => x.text);

export const item = (id: string, text: string, mainNodes: string[] = [], paperId?: string, seq?: number): Item =>
  paperId === undefined ? { id, text, mainNodes } : { id, text, mainNodes, paperId, seq };

/** 确定性的合成乱码题（“（合成）”开头 + len 个常用汉字区的字），彼此不相干 */
export function synthText(seed: number, len: number): string {
  let s = (seed * 2654435761 + 1013904223) >>> 0;
  const out: string[] = [];
  for (let i = 0; i < len; i++) {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    out.push(String.fromCodePoint(0x4e00 + (s >>> 8) % 6000));
  }
  return `（合成）${out.join("")}`;
}

export function withConfig(patch: { near?: Partial<DedupeConfig["near"]>; paper?: Partial<DedupeConfig["paper"]> }): DedupeConfig {
  return { ...DEFAULT_CONFIG, near: { ...DEFAULT_CONFIG.near, ...patch.near }, paper: { ...DEFAULT_CONFIG.paper, ...patch.paper } };
}
