// 合成夹具：能力代码与陷阱码（E-FIX-002 的根因不在十二类里）
export const ABILITY_CODES = [
  { code: "C-OPR-01", name: "运算", dim: "运算能力", observable: "—" },
  { code: "C-DAT-01", name: "数据", dim: "数据分析", observable: "—" },
];
export const ERROR_TRAPS = [
  { code: "E-FIX-001", desc: "—", rootCause: "运算错误" },
  { code: "E-FIX-002", desc: "—", rootCause: "不存在的根因" },
];
