// 生成报告：asset-inventory.md / asset-inventory.json / conclusion-fields.md（spec 设计要点 6）。
// 输出排序固定、不含运行时间，同一输入两次运行逐字节相同。
import { SUBJECT_NAME, V2_QUESTIONS, type Inventory, type Subject } from "./model.ts";

const ORDER: Subject[] = ["math", "chinese", "moral"];
const esc = (s: string) => s.replace(/\|/g, "\\|").replace(/\n/g, " ");
const show = (v: unknown) => (v === null ? "—" : typeof v === "boolean" ? (v ? "是" : "否") : String(v));

export function renderJson(inv: Inventory): string {
  return JSON.stringify(inv, null, 1) + "\n";
}

export function renderInventoryMd(inv: Inventory): string {
  const out: string[] = [];
  out.push("# KGE-00 知识资产盘点报告", "");
  out.push(
    "> 由 `tools/kge-inventory` 生成，请勿手改；重新生成：`make kge-inventory`。",
    "> 依据：开发计划 v2.0 冲刺 0 · W1 盘点项 6；KGE V1.1 第七章。口径与限制见 `specs/kge-00-asset-inventory/spec.md`。",
    "> 本报告只含资产 ID、名称、计数与文件路径，不含题干、范文、原文，不含任何学生数据。",
    "",
  );
  out.push("## 生成信息", "", "| 项 | 值 |", "|---|---|");
  out.push(`| 工具 | ${inv.meta.tool} ${inv.meta.toolVersion} |`);
  for (const l of inv.meta.legacy) {
    out.push(
      `| legacy ${l.name} | \`${l.root}\` · 提交 ${l.commit ?? "非 git 目录"}${l.commits !== null ? ` · 提交数 ${l.commits}` : ""}${l.clean === false ? " · **工作区有未提交改动**" : ""} |`,
    );
  }
  out.push(`| 库模式输出 | ${inv.db.provided ? `已并入 ${new Set(inv.db.rows.map((r) => r.database)).size} 个库` : "未提供（相关项写“待库模式”）"} |`, "");

  out.push("## 一、盘点项 6 的六个问题", "");
  for (const q of V2_QUESTIONS) {
    const rows = inv.answers.filter((a) => a.question === q.id);
    out.push(`### ${q.text}`, "");
    if (rows.length === 0) {
      out.push("（只涉及道法，见下方道法一行）", "");
      continue;
    }
    out.push("| 学科 | 回答 |", "|---|---|");
    for (const s of ORDER) for (const a of rows.filter((r) => r.subject === s)) out.push(`| ${SUBJECT_NAME[s]} | ${esc(a.answer)} |`);
    out.push("");
  }

  out.push("## 二、KGE 第七章阻断问题的实物核验", "", "| 学科 | 问题 | 核验结论 | 依据（本报告的键） |", "|---|---|---|---|");
  for (const s of ORDER)
    for (const b of inv.blockers.filter((x) => x.subject === s))
      out.push(`| ${SUBJECT_NAME[s]} | ${esc(b.problem)} | ${esc(b.verdict)} | ${b.evidence.map((e) => `\`${e}\``).join(" ")} |`);
  out.push("");

  const titles: Record<Subject, string> = { math: "三", chinese: "四", moral: "五" };
  for (const s of ORDER) {
    const r = inv.subjects.find((x) => x.subject === s);
    if (!r) continue;
    out.push(`## ${titles[s]}、${SUBJECT_NAME[s]}`, "", `读过的源文件：${r.sources.map((f) => `\`${f}\``).join("、")}`, "");
    if (r.loadErrors.length > 0) {
      out.push("**加载问题**：", "");
      for (const e of r.loadErrors) out.push(`- ${esc(e)}`);
      out.push("");
    }
    for (const g of r.groups) {
      out.push(`### ${g.title}`, "", "| 键 | 项 | 值 | 样例 / 说明 |", "|---|---|---|---|");
      for (const f of g.findings) {
        const extra = [f.samples && f.samples.length > 0 ? `样例：${f.samples.join("；")}` : "", f.note ?? ""].filter(Boolean).join("　");
        out.push(`| \`${f.key}\` | ${esc(f.label)} | ${esc(show(f.value))} | ${esc(extra)} |`);
      }
      out.push("");
    }
  }

  out.push("## 六、库模式", "");
  if (!inv.db.provided) {
    out.push(
      "没有提供库模式输出。以下各项在报告里写“待库模式”：语文 85 篇必背（`textbook_catalog.must_recite`）、库表 `knowledge_point` 与 `item_bank`、道法 AI 生成题（`daofa_item.rubric`）、阿鸦库 `knowledge_nodes` / `questions` 是否有数据、库里是否残留 20 格编码。",
      "",
      "运行方式见 `tools/kge-inventory/db/readonly-inventory.sql` 文件头；输出用 `--db-output <库名>=<文件>` 并入。",
      "",
    );
  } else {
    if (inv.db.rejected > 0) out.push(`**有 ${inv.db.rejected} 行库模式输出因键不在白名单或值不合规被拒绝并入**，请核对运行的 SQL 是否被改过。`, "");
    out.push("| 库 | 键 | 值 |", "|---|---|---|");
    for (const r of inv.db.rows) out.push(`| ${esc(r.database)} | \`${esc(r.key)}\` | ${esc(r.value)} |`);
    out.push("");
  }
  return out.join("\n");
}

export function renderConclusionsMd(inv: Inventory): string {
  const out: string[] = [];
  out.push("# 结论字段清单（SIE 迁移输入）", "");
  out.push(
    "> 由 `tools/kge-inventory` 生成，请勿手改。依据：开发计划 v2.0 盘点项 7；完整方案 v1.1 5.5。",
    "> **SIE 迁移处理：以下全部不迁移，只存档。** 新判断由新证据重算生成，旧报告只作存档。",
    "> 算法一栏是按代码结构的判断：所在文件或使用方有模型调用记“含模型调用”，否则记“规则（纯函数）”；库表与枚举不判断。",
    "> 证据引用、置信度按字段名识别（如 evidence、*_ids、confidence），只说明字段是否存在，不说明内容是否可靠。",
    "> 触及不推断清单的字段也按字段名与取值识别，可能漏报或误报，需人工核对。",
    "",
  );
  const forbidden = inv.conclusions.flatMap((c) => c.fields.filter((f) => f.forbidden));
  out.push("## 汇总", "", "| 学科 | 结论结构数 | 字段数 | 带证据引用的结构 | 带置信度的结构 | 触及不推断清单或同伴比较的字段 |", "|---|---|---|---|---|---|");
  for (const s of ORDER) {
    const cs = inv.conclusions.filter((c) => c.subject === s);
    out.push(
      `| ${SUBJECT_NAME[s]} | ${cs.length} | ${cs.reduce((a, c) => a + c.fields.length, 0)} | ${cs.filter((c) => c.hasEvidenceRef).length} | ${cs.filter((c) => c.hasConfidence).length} | ${forbidden.filter((f) => f.subject === s).length} |`,
    );
  }
  out.push("");
  if (forbidden.length > 0) {
    out.push("## 触及不推断清单或同伴比较的字段", "", "| 学科 | 来源 | 结构 | 字段 / 取值 | 原因 |", "|---|---|---|---|---|");
    for (const f of forbidden) out.push(`| ${SUBJECT_NAME[f.subject]} | \`${f.source}\` | ${f.symbol} | ${esc(f.field)} | ${esc(f.forbidden!)} |`);
    out.push("");
  }
  out.push("## 明细", "");
  for (const s of ORDER) {
    const cs = inv.conclusions.filter((c) => c.subject === s);
    if (cs.length === 0) continue;
    out.push(`### ${SUBJECT_NAME[s]}`, "", "| 来源 | 结构 | 类型 | 算法 | 证据引用 | 置信度 | 字段 |", "|---|---|---|---|---|---|---|");
    for (const c of cs) {
      out.push(
        `| \`${c.source}\` | ${c.symbol} | ${c.kind} | ${esc(c.algorithm)}（${esc(c.algorithmBasis)}） | ${c.hasEvidenceRef ? "有" : "无"} | ${c.hasConfidence ? "有" : "无"} | ${esc(c.fields.map((f) => f.field).join("、"))} |`,
      );
    }
    out.push("");
  }
  return out.join("\n");
}

/** 输出安全检查：不得有 40 个以上连续汉字（原文片段的信号），见 spec 验收。返回违规片段。 */
export function longCjkRuns(text: string, limit = 40): string[] {
  return [...text.matchAll(new RegExp(`[\\u4e00-\\u9fff]{${limit},}`, "g"))].map((m) => m[0].slice(0, 20));
}
