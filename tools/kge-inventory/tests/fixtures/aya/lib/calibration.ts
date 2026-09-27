// 合成夹具：规则式结论（没有模型调用）
export interface CalibrationState { evidence: string[]; confidence: number; abilityBand: string; note: string }
export function calibrate(): CalibrationState { return { evidence: [], confidence: 0, abilityBand: "—", note: "" }; }
