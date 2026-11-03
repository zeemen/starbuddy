// present_item（契约 1.1.0 候选版，PLT-12：specs/present-item-contract/spec.md）：从内容包取一道题的题面。
// 只读、确定性：不判分、不产生证据、不读时钟、不调模型。输出前按白名单投影——逐字段从内容包里挑契约列出的那几样
// （题型、选项键、题干与选项内容的受限富文本、作答格式、版权状态、内容版本），答案、陷阱、提示阶梯、审核等级、来源编号一律不碰。
// 作答格式不从答案推出：多选的 max_choices 取选项数（不取正确选项数），填空一律 expression（不按标准答案像不像数字给）。
// 题干缺失（真实内容包至今不收录题干）或版权受限 → content_state = withheld：只给题型、选项键与作答格式。
import type { LoadedPack, PackItem } from "../content.ts";
import { projectParagraphs, projectRuns, type RichInline, type RichParagraph } from "../rich-text.ts";

export const PRESENT_ALGORITHM = "aya-present@0.1.0";
/** 填空作答的长度上限（与学习服务诊断作答的上限一致） */
export const FILL_MAX_LENGTH = 200;

export type ItemType = "single_choice" | "multi_choice" | "fill_blank";
export type LicenseState = "unverified" | "licensed" | "original" | "public" | "restricted";

export interface PresentedItem {
  item_ref: string;
  item_version: string;
  item_type: ItemType;
  content_state: "complete" | "withheld";
  stem?: RichParagraph[];
  options?: { key: string; content?: RichInline[] }[];
  answer_format: { kind: "choice"; min_choices: number; max_choices: number } | { kind: "number" | "expression"; max_length: number };
  license_state: LicenseState;
}

const ITEM_TYPE: Record<PackItem["format"], ItemType> = { single: "single_choice", multi: "multi_choice", fill: "fill_blank" };

/** 逐题授权状态 → 契约 LicenseState（与知识服务 ItemTagView 同一枚举）。自编且授权已核 → original */
export function licenseState(item: PackItem): LicenseState {
  const s = item.source;
  if (s.license_status === "denied") return "restricted";
  if (s.license_status === "cleared") return s.source_type === "self_authored" ? "original" : "licensed";
  return "unverified";
}

export function presentItem(content: LoadedPack, item: PackItem): PresentedItem {
  const license = licenseState(item);
  const complete = item.stem !== undefined && license !== "restricted";
  const type = ITEM_TYPE[item.format];
  const out: PresentedItem = {
    item_ref: `aya:${item.id}`,
    item_version: `${content.pack.pack_name}@${content.versionTag}`,
    item_type: type,
    content_state: complete ? "complete" : "withheld",
    answer_format: item.format === "fill"
      ? { kind: "expression", max_length: FILL_MAX_LENGTH }
      : { kind: "choice", min_choices: 1, max_choices: item.format === "single" ? 1 : (item.options ?? []).length },
    license_state: license,
  };
  if (complete) out.stem = projectParagraphs(item.stem!);
  if (item.format !== "fill") {
    out.options = (item.options ?? []).map((o) => {
      const runs = complete ? item.option_content?.[o.key] : undefined;
      return runs ? { key: o.key, content: projectRuns(runs) } : { key: o.key };
    });
  }
  return out;
}
