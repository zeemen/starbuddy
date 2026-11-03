// teach v0（spec 设计要点第 4 节、验收标准 teach 各条；待人确认 #1 默认）：确定性提示阶梯 + 知识页面直出。
// 全部是合成数据（规则 8）：对话、页面正文是编造的；提示原文来自 legacy 内容包（学科内容，不含学生数据）。
import assert from "node:assert/strict";
import { test } from "node:test";
import type { LogRecord } from "../src/adapter.ts";
import { createAdapter } from "../src/adapter.ts";
import {
  DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT,
  HINT_ALGORITHM,
  hintLevelFor,
  hintRevealsAnswer,
  PAGE_LESSON_ALGORITHM,
  splitPage,
  teachHint,
} from "../src/methods/teach.ts";
import { contextV1, contractV1, keysOf, makeAdapterV1, realPack, syntheticPack, taxonomy, type Json } from "./helpers.ts";

const CAP = DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT;

const adapter = makeAdapterV1();
const PERSONA = { persona_id: "aya", prompt_version: "1.1.0" };
const CONVERSATION_SECRET = "（合成对话）CONVO-SECRET-4d7e";
const EVIDENCE_SECRET = "（合成作答）EVID-SECRET-81aa";

function hintBody(itemId: string, allowed: number, extra: Record<string, unknown> = {}) {
  const it = realPack.itemById.get(itemId);
  return {
    context: contextV1(),
    mode: "hint",
    skill_node: `aya:${it ? it.mainNodes[0] : "K-SYN-0001"}`,
    item_ref: `aya:${itemId}`,
    allowed_hint_level: allowed,
    persona: PERSONA,
    ...extra,
  };
}
function lessonBody(bodyMd: string | undefined, extra: Record<string, unknown> = {}) {
  return {
    context: contextV1(),
    mode: "lesson",
    skill_node: "aya:P-MUL-0812",
    allowed_hint_level: 3,
    persona: PERSONA,
    ...(bodyMd !== undefined ? { knowledge_page: { page_id: "ma.alg.synthetic_0001", page_version: "1.0.0", body_md: bodyMd, atom_refs: ["node:MA.ALG.SYN_0001"] } } : {}),
    ...extra,
  };
}
function ok(body: unknown, a = adapter) {
  const r = a.handle("POST", "teach", body);
  assert.equal(r.status, 200, JSON.stringify(r.body).slice(0, 300));
  assert.deepEqual(contractV1.validate("TeachResponse", r.body, "1.0.0"), { ok: true });
  return r.body as Json;
}

// ---------- hint：正常 ----------
test("hint：对每个 allowed_hint_level（0–6），hint_level_used ≤ min(allowed, 3)，输出就是该级提示原文，不含更高等级的内容", () => {
  assert.equal(CAP, 3);
  const items = realPack.pack.items.filter((_, i) => i % 5 === 0);
  for (const it of items) {
    for (let allowed = 0; allowed <= 6; allowed++) {
      const b = ok(hintBody(it.id, allowed));
      const used = b.result.hint_level_used;
      assert.ok(used <= Math.min(allowed, CAP), `${it.id} allowed=${allowed} used=${used}`);
      assert.equal(b.result.segments.length, 1);
      const seg = b.result.segments[0];
      if (seg.type === "item_ref") {
        assert.equal(seg.item_ref, `aya:${it.id}`); // 该级含答案：只指回题目
        assert.equal(used, 0);
        continue;
      }
      assert.equal(used, Math.min(allowed, CAP, it.hintPath.length - 1));
      assert.equal(seg.text, it.hintPath[used]);
      for (let higher = used + 1; higher < it.hintPath.length; higher++) {
        if (it.hintPath[higher] !== it.hintPath[used]) assert.ok(!seg.text.includes(it.hintPath[higher]!), `${it.id} 带出了 ${higher} 级`);
      }
    }
  }
});

test("封顶：作答提交前（v1.0 没有“已提交”的信号，所以一律如此）全部 721 道题都不返回 4 级及以上的提示原文", () => {
  for (const it of realPack.pack.items) {
    const low = new Set(it.hintPath.slice(0, CAP + 1));
    for (const allowed of [4, 5, 6]) {
      const b = ok(hintBody(it.id, allowed));
      assert.ok(b.result.hint_level_used <= CAP, `${it.id} allowed=${allowed}`);
      const seg = b.result.segments[0];
      if (seg.type !== "text") continue;
      for (const l of [4, 5, 6]) {
        const h = it.hintPath[l];
        if (h !== undefined && !low.has(h)) assert.notEqual(seg.text, h, `${it.id} allowed=${allowed} 返回了 ${l} 级`);
      }
    }
  }
});

test("封顶配置 max_hint_level_before_submit：可以往下调（1 → 最多 1 级），不能超过 3（构造时拒绝）", () => {
  const low = createAdapter({ content: realPack, taxonomy, contract: contractV1, allowCandidate: true, teach: { max_hint_level_before_submit: 1 } });
  const it = realPack.pack.items.find((i) => i.format === "single")!;
  for (const allowed of [1, 3, 6]) assert.equal(ok(hintBody(it.id, allowed), low).result.hint_level_used, 1);
  for (const bad of [4, 5, 6, -1, 2.5]) {
    assert.throws(() => createAdapter({ content: realPack, taxonomy, contract: contractV1, teach: { max_hint_level_before_submit: bad } }), /max_hint_level_before_submit/, String(bad));
  }
  // 纯函数的缺省值同样是 3
  assert.equal(teachHint(realPack, { skill_node: `aya:${it.mainNodes[0]}`, item_ref: `aya:${it.id}`, allowed_hint_level: 6 }).hintLevelUsed, 3);
});

test("hint 的信封：不调模型、不写话术（不披露、不转介、没有 verified_statements / verified_claims），不产生证据", () => {
  const it = realPack.pack.items.find((i) => i.format === "single")!;
  const b = ok(hintBody(it.id, 2));
  assert.equal(b.result.hint_level_used, 2);
  assert.equal(b.result.ai_disclosure_shown, false);
  assert.equal(b.result.referral_triggered, false);
  assert.ok(!keysOf(b).includes("verified_statements"));
  assert.ok(!keysOf(b).includes("verified_claims"));
  assert.deepEqual(b.evidence_ids, []);
  assert.ok(!("evidence" in b));
  assert.equal(b.model_version, `algo:${HINT_ALGORITHM}`);
  assert.equal(b.source_version, `algo:${HINT_ALGORITHM};${realPack.itemsSourceVersion}`);
  assert.doesNotMatch(b.source_version, /prompt:/);
  assert.equal(b.human_review_required, false);
});

test("对内容包里每一道填空题逐题断言：任何 allowed 下输出里都没有标准答案原文；含答案的那一级不返回原文，只指回题目", () => {
  const fills = realPack.pack.items.filter((i) => i.format === "fill");
  assert.equal(fills.length, 338);
  const withheldItems = new Set<string>();
  const withheldAtZero = new Set<string>();
  for (const it of fills) {
    for (let allowed = 0; allowed <= 6; allowed++) {
      const b = ok(hintBody(it.id, allowed));
      const seg = b.result.segments[0];
      const level = Math.min(allowed, CAP, it.hintPath.length - 1);
      if (hintRevealsAnswer(it.hintPath[level]!, it.answer)) {
        // 含答案的那一级：不降级到别的等级，整条不给
        assert.deepEqual(b.result.segments, [{ type: "item_ref", item_ref: `aya:${it.id}` }], `${it.id} allowed=${allowed}`);
        withheldItems.add(it.id);
        if (level === 0) withheldAtZero.add(it.id);
        continue;
      }
      assert.equal(seg.type, "text");
      assert.equal(seg.text, it.hintPath[level]);
      assert.ok(!hintRevealsAnswer(seg.text, it.answer), `${it.id} allowed=${allowed}`);
      // 独立的朴素核对：两个字符以上的答案原文不是输出的子串
      for (const a of it.answer) if (a.trim().length >= 2) assert.ok(!seg.text.includes(a), `${it.id} allowed=${allowed} 含答案`);
    }
  }
  // lessons.md 里交教研改题的清单：74 道，其中 3 道 0 级就含答案
  assert.equal(withheldItems.size, 74);
  assert.deepEqual([...withheldAtZero].sort(), ["Q-PRC-U02-D-003", "Q-PRC-U02-R-001", "Q-PRF-U01-T-001"]);
});

test("答案检查：两个字符以上按子串（忽略空白与 $），单个字符按独立记号（x^2 里的 2 不算）", () => {
  assert.equal(hintRevealsAnswer("补全为 $n+1$。", ["n+1"]), true);
  assert.equal(hintRevealsAnswer("写成 $n + 1$", ["n+1"]), true);
  assert.equal(hintRevealsAnswer("先算 $x^2$ 的系数", ["2"]), false);
  assert.equal(hintRevealsAnswer("所以 $x=2$", ["2"]), true);
  assert.equal(hintRevealsAnswer("相差 12", ["2"]), false);
  assert.equal(hintRevealsAnswer("再读一遍题目", ["5"]), false);
});

test("含答案的那一级：不返回原文、不降级，只返回题目引用段（hint_level_used = 0）；不含答案的级照常返回", () => {
  const pack = syntheticPack([{ answer: ["蓝球"], hintPath: ["（合成）数一数蓝球。", "（合成）1 级", "（合成）2 级", "（合成）3 级：是蓝球", "（合成）4 级"] }]);
  const a = makeAdapterV1(pack);
  const node = `aya:${pack.pack.items[0]!.mainNodes[0]}`;
  const withheld = [{ type: "item_ref", item_ref: "aya:SYN-0" }];
  for (const allowed of [0, 3, 4, 6]) {
    const b = ok({ ...hintBody("SYN-0", allowed), skill_node: node }, a);
    assert.deepEqual(b.result.segments, withheld, `allowed=${allowed}`);
    assert.equal(b.result.hint_level_used, 0);
    assert.ok(!JSON.stringify(b).includes("蓝球"));
  }
  for (const allowed of [1, 2]) {
    const b = ok({ ...hintBody("SYN-0", allowed), skill_node: node }, a);
    assert.deepEqual(b.result.segments, [{ type: "text", text: `（合成）${allowed} 级` }]);
    assert.equal(b.result.hint_level_used, allowed);
  }
  assert.equal(hintLevelFor(pack.pack.items[0]!, 0), -1);
  assert.equal(hintLevelFor(pack.pack.items[0]!, 3), -1);
  assert.equal(hintLevelFor(pack.pack.items[0]!, 2), 2);
  // 原因编码：契约里没有位置，只在内部结果里（lessons 待人确认）
  const out = teachHint(pack, { skill_node: node, item_ref: "aya:SYN-0", allowed_hint_level: 3 });
  assert.equal(out.withheld, "hint_reveals_answer");
  assert.equal(teachHint(pack, { skill_node: node, item_ref: "aya:SYN-0", allowed_hint_level: 2 }).withheld, undefined);
});

// ---------- lesson：正常 ----------
test("lesson：带 knowledge_page 时按空行切段，各段拼接等于 body_md 去掉段间空行，不含页面以外的文字", () => {
  const md = "（合成）第一段第一行\n第一段第二行\n\n\n（合成）第二段 $x^2$\n  \n（合成）第三段";
  const b = ok(lessonBody(md));
  const texts = b.result.segments.map((s: Json) => s.text);
  assert.deepEqual(texts, ["（合成）第一段第一行\n第一段第二行", "（合成）第二段 $x^2$", "（合成）第三段"]);
  assert.equal(texts.join(""), md.replace(/\n[ \t]*\n(?:[ \t]*\n)*/g, ""));
  for (const s of b.result.segments) assert.equal(s.type, "text");
  assert.equal(b.result.hint_level_used, 0);
  assert.ok(!keysOf(b).includes("whiteboard_dsl") && !JSON.stringify(b).includes("atom_refs") && !JSON.stringify(b).includes("node:MA"));
  assert.equal(b.semantics, "formal");
  assert.equal(b.confidence, 1);
  assert.equal(b.model_version, `algo:${PAGE_LESSON_ALGORITHM}`);
  assert.equal(b.source_version, `algo:${PAGE_LESSON_ALGORITHM};${realPack.itemsSourceVersion}`);
  // 超长段按 4000 字切（不拆代理对），拼接仍等于原文
  const long = "𝑥".repeat(4100);
  const parts = splitPage(long);
  assert.deepEqual(parts.map((p) => Array.from(p).length), [4000, 100]);
  assert.equal(parts.join(""), long);
  ok(lessonBody(long));
});

// ---------- 低置信度 ----------
test("hint 低置信度：题目未准入 → practice、confidence = 0.5；已准入 → formal、1.0；都不置 human_review_required", () => {
  const it = realPack.pack.items[0]!;
  const b = ok(hintBody(it.id, 1));
  assert.equal(b.semantics, "practice");
  assert.equal(b.confidence, 0.5);
  assert.equal(b.human_review_required, false);
  const pack = syntheticPack([{}], { sourceVerified: true });
  const admitted = ok({ ...hintBody("SYN-0", 1), skill_node: `aya:${pack.pack.items[0]!.mainNodes[0]}` }, makeAdapterV1(pack));
  assert.equal(admitted.semantics, "formal");
  assert.equal(admitted.confidence, 1);
});

// ---------- 上游失败 ----------
test("teach 坏请求：hint 缺 item_ref 或 skill_node 与题目不符 → 422；未知题目 → 404；lesson 不带页面 → 422 MODE_NOT_SUPPORTED；缺 usage_id → 400；0.9.0 → 501", () => {
  const it = realPack.pack.items[0]!;
  const noItem: Json = hintBody(it.id, 2);
  delete noItem.item_ref;
  const other = realPack.pack.nodes.find((n) => !it.mainNodes.includes(n.id))!.id;
  const noUsage: Json = hintBody(it.id, 2);
  delete noUsage.context.usage_id;
  const cases: [unknown, number, string][] = [
    [noItem, 422, "INVALID_REQUEST"],
    [hintBody(it.id, 2, { skill_node: `aya:${other}` }), 422, "INVALID_REQUEST"],
    [hintBody("NOT-IN-PACK", 2), 404, "ITEM_NOT_FOUND"],
    [lessonBody(undefined), 422, "MODE_NOT_SUPPORTED"],
    [lessonBody("  \n\n  "), 422, "INVALID_REQUEST"],
    [lessonBody("（合成）", { skill_node: "aya:K-NOT-A-NODE-0000" }), 422, "INVALID_REQUEST"],
    [noUsage, 400, "INVALID_REQUEST"],
    [hintBody(it.id, 7), 400, "INVALID_REQUEST"],
    [hintBody(it.id, 2, { item_ref: "wenli:Q-1" }), 400, "INVALID_REQUEST"],
    [{ ...hintBody(it.id, 2), context: contextV1({ contract_version: "0.9.0", idempotency_key: "synthetic-teach-key" }) }, 501, "METHOD_NOT_IMPLEMENTED"],
  ];
  for (const [body, status, code] of cases) {
    const r = adapter.handle("POST", "teach", body);
    assert.equal(r.status, status, JSON.stringify(body).slice(0, 200));
    assert.equal((r.body as Json).code, code);
    assert.deepEqual(contractV1.validate("Problem", r.body, "1.0.0"), { ok: true });
  }
});

test("conversation 与 recent_evidence 的内容不出现在响应和日志里（只参与请求体指纹）", () => {
  const logs: LogRecord[] = [];
  const a = makeAdapterV1(realPack, logs);
  const it = realPack.pack.items[3]!;
  const extra = {
    conversation: [{ turn_id: "00000000-0000-4000-8000-0000000000c1", role: "student", content: CONVERSATION_SECRET }],
    recent_evidence: [{
      evidence_id: "00000000-0000-4000-8000-0000000000e1",
      attempt_id: "00000000-0000-4000-8000-0000000000a1",
      kind: "response_text",
      occurred_at: "2026-10-08T19:30:00+08:00",
      item_ref: `aya:${it.id}`,
      skill_node: `aya:${it.mainNodes[0]}`,
      admission: "source_unverified",
      payload: { text: EVIDENCE_SECRET },
    }],
  };
  const withCtx = ok(hintBody(it.id, 2, extra), a);
  const without = ok(hintBody(it.id, 2), a);
  assert.deepEqual(withCtx.result, without.result);
  const lesson = ok(lessonBody("（合成）页面", extra), a);
  a.handle("POST", "teach", hintBody("NOT-IN-PACK", 2, extra));
  for (const s of [JSON.stringify(withCtx), JSON.stringify(lesson), JSON.stringify(logs)]) {
    assert.ok(!s.includes(CONVERSATION_SECRET));
    assert.ok(!s.includes(EVIDENCE_SECRET));
  }
});
