// 端到端场景（spec 验收前两条）：合成作答 → 适配器 grade / diagnose → 模拟学习服务落库 → 查询。
// 输出一段 SQL 到标准输出，由 run.sh 在一次性的本地 Postgres 上执行。断言以 starbuddy_learning 身份查询。
// 学生、请求 ID 全部是合成值（CLAUDE.md 规则 8）。
import assert from "node:assert/strict";
import { context, makeAdapter, realPack, RUBRIC, STUDENT } from "../helpers.ts";
import { persistDiagnose, persistEvidenceOnly, persistGrade } from "./persist.ts";

type Json = any;
const out: string[] = [];
const sql = (s: string) => out.push(s);
const lit = (s: string) => `'${s.replace(/'/g, "''")}'`;
const uuids = (ids: string[]) => `ARRAY[${ids.map(lit).join(",")}]::uuid[]`;
const check = (cond: string, msg: string) => sql(`SELECT e2e.ok(${cond}, ${lit(msg)});`);
const TIME = "2026-10-08T19:30:00+08:00";

function grade(item: string, response: Json, attemptId: string) {
  const req = {
    context: context(),
    attempt: { attempt_id: attemptId, item_ref: `aya:${item}`, occurred_at: TIME, response, elapsed_sec: 95, hint_level_used: 0 },
    rubric_version: RUBRIC,
  };
  const r = makeAdapter().handle("POST", "grade", req);
  assert.equal(r.status, 200, JSON.stringify(r.body));
  return { req, resp: r.body as Json };
}

// ---------- A：填空答错且命中陷阱 ----------
const fillItem = realPack.itemById.get("Q-EQU-U03-D-001")!;
const A = grade(fillItem.id, { text: "9" }, "a7000000-0000-4000-8000-00000000000a");
assert.equal(A.resp.result.verdict, "incorrect");
assert.equal(A.resp.result.error_attributions.length, 1);
sql(persistGrade(A.req, A.resp));

// 同一个 idempotency_key 重试（新实例 = 进程重启）：同一批 evidence_id；学习服务按 evidence_id 去重
const replay = makeAdapter().handle("POST", "grade", A.req).body as Json;
assert.deepEqual(replay.evidence_ids, A.resp.evidence_ids, "重试返回同一批 evidence_id");
sql(persistEvidenceOnly(A.req, replay, "grade"));

const aResponse = A.resp.evidence.find((e: Json) => e.kind === "response_text").evidence_id;
check(`(SELECT count(*) FROM platform.evidence_event WHERE evidence_id = ANY(${uuids(A.resp.evidence_ids)})) = 2`,
  "A：evidence_event 查到响应里的两条证据；重试后没有重复");
check(`(SELECT bool_and(student_id = ${lit(STUDENT)} AND admission = 'source_unverified' AND subject = 'aya')
          FROM platform.evidence_event WHERE evidence_id = ANY(${uuids(A.resp.evidence_ids)}))`,
  "A：证据属于该学生，准入状态为 source_unverified");
check(`(SELECT bool_and(e.admission = i.admission) FROM platform.evidence_event e
          JOIN platform.item i ON i.subject = e.subject AND i.item_id = e.item_id
         WHERE e.evidence_id = ANY(${uuids(A.resp.evidence_ids)}))`,
  "A：证据的准入状态与平台题目登记一致");
check(`EXISTS (SELECT 1 FROM platform.attempt WHERE attempt_id = ${lit(A.req.attempt.attempt_id)}
          AND response_evidence_id = ${lit(aResponse)} AND item_id = ${lit(fillItem.id)})`,
  "A：attempt 指向作答证据");
check(`(SELECT evidence_ids = ${uuids(A.resp.evidence_ids)} AND verdict = 'incorrect' AND semantics = 'practice'
          AND human_review_required AND 'content_not_admitted' = ANY (review_reasons)
          AND model_version = 'algo:aya-symbolic-ts@0.1.0' AND verifier_id = 'aya-symbolic-ts'
          FROM platform.grade_result WHERE attempt_id = ${lit(A.req.attempt.attempt_id)})`,
  "A：grade_result 的 evidence_ids 与响应一致，practice 且待复核");
check(`(SELECT count(*) FROM platform.grade_result WHERE attempt_id = ${lit(A.req.attempt.attempt_id)}) = 1`,
  "A：重试没有产生第二条判分");
check(`EXISTS (SELECT 1 FROM platform.grade_dimension d JOIN platform.grade_result g ON g.id = d.grade_result_id
          WHERE g.attempt_id = ${lit(A.req.attempt.attempt_id)} AND d.dimension_id = 'answer' AND d.judged_by = 'verifier'
            AND d.rule_ref = 'aya-symbolic-ts@0.1.0' AND d.score = 0)`,
  "A：grade_dimension 查到 answer 维度");
const attr = A.resp.result.error_attributions[0];
check(`EXISTS (SELECT 1 FROM platform.error_attribution a
          WHERE a.student_id = ${lit(STUDENT)} AND a.subject_category = ${lit(attr.subject_category)}
            AND a.cross_subject_category = ${lit(attr.cross_subject_category)} AND a.evidence_ids = ${uuids([aResponse])}
            AND a.algorithm_version = 'aya-trap-attr@0.1.0' AND a.confidence = 0.8)`,
  `A：error_attribution 查到 ${attr.subject_category} → ${attr.cross_subject_category}，证据 ID 与响应一致`);

// ---------- B：选择题答对 ----------
const choiceItem = realPack.pack.items.find((i) => i.format === "single")!;
const B = grade(choiceItem.id, { choice: choiceItem.answer }, "a7000000-0000-4000-8000-00000000000b");
sql(persistGrade(B.req, B.resp));
check(`EXISTS (SELECT 1 FROM platform.evidence_event WHERE evidence_id = ${lit(B.resp.evidence[0].evidence_id)}
          AND kind = 'response_choice' AND payload = ${lit(JSON.stringify(B.resp.evidence[0].payload))}::jsonb)`,
  "B：选择题作答证据落库为 response_choice");
check(`(SELECT verdict = 'correct' AND score_total = 1 FROM platform.grade_result WHERE attempt_id = ${lit(B.req.attempt.attempt_id)})`,
  "B：选择题判对");

// ---------- C：验证器边界情况（needs_review，维度分为空） ----------
const C = grade(fillItem.id, { text: "2x+" }, "a7000000-0000-4000-8000-00000000000c");
sql(persistGrade(C.req, C.resp));
assert.equal(C.resp.result.verdict, "needs_review");
check(`(SELECT verdict = 'needs_review' AND score_total IS NULL
          AND verifier_reason_codes = ARRAY[${C.resp.result.verifier.reason_codes.map(lit).join(",")}]::text[]
          AND 'verifier_boundary_case' = ANY (review_reasons) AND confidence <= 0.5
          FROM platform.grade_result WHERE attempt_id = ${lit(C.req.attempt.attempt_id)})`,
  "C：边界情况落库为 needs_review，通过数据库的复核约束");

// ---------- D：diagnose（intake） ----------
const dReq = {
  context: context(),
  mode: "intake",
  attempts: [
    { attempt_id: "a7000000-0000-4000-8000-0000000000d1", item_ref: `aya:${fillItem.id}`, occurred_at: TIME, response: { text: "9,-5" }, self_report: "guessed" },
    { attempt_id: "a7000000-0000-4000-8000-0000000000d2", item_ref: `aya:${fillItem.id}`, occurred_at: TIME, response: { text: "7,-7" }, hint_level_used: 5 },
    { attempt_id: "a7000000-0000-4000-8000-0000000000d3", item_ref: `aya:${choiceItem.id}`, occurred_at: TIME, response: { choice: choiceItem.answer } },
  ],
};
const dr = makeAdapter().handle("POST", "diagnose", dReq);
assert.equal(dr.status, 200, JSON.stringify(dr.body));
const D = dr.body as Json;
sql(persistDiagnose(dReq, D));
check(`(SELECT count(*) FROM platform.skill_observation WHERE source_request_id = ${lit(dReq.context.request_id)}) = ${D.result.observations.length}`,
  `D：skill_observation 查到 ${D.result.observations.length} 条观测`);
for (const o of D.result.observations as Json[]) {
  check(`EXISTS (SELECT 1 FROM platform.skill_observation s JOIN platform.skill_node n ON n.subject = s.subject AND n.local_id = s.skill_local_id
            WHERE s.source_request_id = ${lit(dReq.context.request_id)} AND 'aya:' || s.skill_local_id = ${lit(o.skill_node)}
              AND s.outcome = ${lit(o.outcome)} AND s.confidence = ${o.confidence} AND s.evidence_ids = ${uuids(o.evidence_ids)})`,
    `D：观测 ${o.skill_node} ${o.outcome}（置信度 ${o.confidence}）已落库，能力点已登记`);
}
check(`EXISTS (SELECT 1 FROM platform.error_attribution WHERE source_request_id = ${lit(dReq.context.request_id)})`,
  "D：diagnose 的错因已落库");

// ---------- 全局：所有推断引用的证据 ID 都能在 evidence_event 里查到（溯源闭环） ----------
check(`NOT EXISTS (
  SELECT 1 FROM (
    SELECT unnest(evidence_ids) AS id FROM platform.grade_result
    UNION ALL SELECT unnest(evidence_ids) FROM platform.grade_dimension
    UNION ALL SELECT unnest(evidence_ids) FROM platform.error_attribution
    UNION ALL SELECT unnest(evidence_ids) FROM platform.skill_observation) r
  WHERE NOT EXISTS (SELECT 1 FROM platform.evidence_event e WHERE e.evidence_id = r.id))`,
  "所有推断引用的 evidence_id 都能在 evidence_event 查到");
check(`(SELECT count(*) FROM platform.mastery_estimate) = 0`, "适配器链路不写掌握度（规则 2）");

process.stdout.write(`SET ROLE starbuddy_learning;\n${out.join("\n")}\n`);
