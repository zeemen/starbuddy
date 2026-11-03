// 合成夹具：页码为近似值。l2 绑定了版次（正例），其余没有（反例）。
export const DAOFA_KNOWLEDGE_MAP = [
  { key: "law", title: "法律", nodes: [
    { id: "l1", name: "宪法", book: "八下", pages: "2-15", keywords: [], phrases: ["规范表述"] },
    { id: "l2", name: "权利", book: "八下", pages: "约20页", keywords: [], phrases: ["规范表述"], edition: "2024" },
  ] },
  { key: "xi_reader", title: "读本", nodes: [
    { id: "xr1", name: "读本节点", book: "读本", pages: "待核", keywords: [], phrases: [], source: "读本", needs_review: true },
  ] },
];
