// 作答通道与证据等级（纯函数，spec 设计要点 3；ADR 15；待人确认 #6）。
//
// 契约 v0.9 / v1.0 的 DiagnoseRequest 与 AttemptInput 都没有“输入通道”或“证据等级”字段（v1.0 只在 GradeRequest 上有
// evidence_level）。不改契约的前提下，本适配器从平台随请求带来的既有证据（prior_evidence，EvidenceRecord）里读通道标记：
//   - kind = interaction_signal、payload.signal_code ∈ evidence_level_a / evidence_level_b / evidence_level_c：平台输入通道服务给出的证据等级；
//   - kind = ocr_transcription：这道作答来自拍照识别（平台已把识别结果归一成 response.text / choice 放进 attempts）。
// 标记与作答的对应：两边都有 attempt_id 时按 attempt_id；否则按 item_ref 与 occurred_at（同一时刻）。
// 判定（同一道作答有多个标记时取最严）：
//   - 没有任何标记 → A（原生测评，键盘 / 点选）；
//   - 有拍照识别证据、没有等级标记 → C（未确认的拍照作答）；有拍照识别证据时最高只认 B；
//   - A → 权重 1.0；B → 0.7（legacy SOURCE_WEIGHT：原生 1.0、拍照确认 0.7，未校准）；C → 不判分，列入 ocr_confirmation_required。
// 这是本适配器对“通道信息从哪里来”的约定，平台侧需要按它传标记（README 待人确认）。
import { SOURCE_WEIGHT } from "./coverage.ts";

export type EvidenceLevel = "A" | "B" | "C";

export const LEVEL_SIGNAL_CODES: Readonly<Record<string, EvidenceLevel>> = {
  evidence_level_a: "A",
  evidence_level_b: "B",
  evidence_level_c: "C",
};

/** 等级 → 观测置信度权重；C 不判分（null） */
export const LEVEL_WEIGHT: Readonly<Record<EvidenceLevel, number | null>> = {
  A: SOURCE_WEIGHT.native_test,
  B: SOURCE_WEIGHT.ocr_confirmed,
  C: null,
};

const RANK: Record<EvidenceLevel, number> = { A: 0, B: 1, C: 2 };
export const stricter = (a: EvidenceLevel, b: EvidenceLevel): EvidenceLevel => (RANK[a] >= RANK[b] ? a : b);

export interface AttemptKey {
  attempt_id?: string;
  item_ref: string;
  occurred_at: string;
}

export interface PriorRecord {
  attempt_id?: string;
  kind: string;
  item_ref?: string;
  occurred_at: string;
  payload: Record<string, unknown>;
}

const sameInstant = (a: string, b: string) => {
  const x = Date.parse(a);
  const y = Date.parse(b);
  return Number.isFinite(x) && x === y;
};

/** 这条既有证据是否属于这道作答 */
export function belongsTo(e: PriorRecord, a: AttemptKey): boolean {
  if (a.attempt_id && e.attempt_id) return e.attempt_id === a.attempt_id;
  return e.item_ref === a.item_ref && sameInstant(e.occurred_at, a.occurred_at);
}

/** 一道作答的证据等级 */
export function evidenceLevel(a: AttemptKey, prior: readonly PriorRecord[]): EvidenceLevel {
  let explicit: EvidenceLevel | null = null;
  let photo = false;
  for (const e of prior) {
    if (!belongsTo(e, a)) continue;
    if (e.kind === "ocr_transcription") photo = true;
    if (e.kind === "interaction_signal") {
      const lvl = LEVEL_SIGNAL_CODES[String(e.payload.signal_code)];
      if (lvl) explicit = explicit === null ? lvl : stricter(explicit, lvl);
    }
  }
  if (photo) return explicit === null ? "C" : stricter(explicit, "B");
  return explicit ?? "A";
}
