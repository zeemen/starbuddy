// 测试辅助：模拟学习服务把适配器响应落库（冲刺 1-C 的学习服务写入接口尚未实现，spec“不做”第 3 条）。
// 只用来证明“契约字段 → 平台表”的映射正确；不是生产代码，不在适配器进程里运行。
// 以 starbuddy_learning 身份写入（ADR 01：只有学习服务能写证据与推断）。
// 输入是已经过契约校验的请求与响应，这里用宽松类型直接读字段
type Json = any;

const lit = (s: string) => `'${s.replace(/'/g, "''")}'`;
const litOrNull = (v: unknown) => (v === undefined || v === null ? "NULL" : typeof v === "number" ? String(v) : lit(String(v)));
const uuids = (ids: string[]) => `ARRAY[${ids.map(lit).join(",")}]::uuid[]`;
const texts = (xs: string[], type = "text") => `ARRAY[${xs.map(lit).join(",")}]::${type}[]`;
const local = (ref: string | undefined) => (ref ? ref.slice(ref.indexOf(":") + 1) : undefined);

function evidenceSql(req: Json, resp: Json, method: string): string {
  const ctx = req.context;
  return resp.evidence
    .map(
      (e: Json) => `INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, skill_local_id,
  admission, payload, source_method, request_id, request_idempotency_key)
SELECT ${lit(e.evidence_id)}, ${lit(e.occurred_at)}, ${lit(ctx.student_ref)}, 'aya', ${lit(e.kind)}, ${litOrNull(local(e.item_ref))},
  ${litOrNull(local(e.skill_node))}, ${lit(e.admission)}, ${lit(JSON.stringify(e.payload))}::jsonb, ${lit(method)},
  ${lit(ctx.request_id)}, ${lit(ctx.idempotency_key)}
WHERE NOT EXISTS (SELECT 1 FROM platform.evidence_event WHERE evidence_id = ${lit(e.evidence_id)});`,
    )
    .join("\n");
}

/** 一次作答：attempt 行指向作答证据（响应里 kind 为 response_* 的那条） */
function attemptSql(req: Json, attempt: Json, responseEvidenceId: string): string {
  return `INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id,
  hint_level_used, elapsed_sec, self_report, request_idempotency_key)
SELECT ${lit(attempt.attempt_id)}, ${lit(attempt.occurred_at)}, ${lit(req.context.student_ref)}, 'aya', ${lit(local(attempt.item_ref)!)},
  ${lit(responseEvidenceId)}, ${litOrNull(attempt.hint_level_used)}, ${litOrNull(attempt.elapsed_sec)}, ${litOrNull(attempt.self_report)},
  ${lit(req.context.idempotency_key)}
WHERE NOT EXISTS (SELECT 1 FROM platform.attempt WHERE attempt_id = ${lit(attempt.attempt_id)});`;
}

function attributionSql(req: Json, a: Json): string {
  return `INSERT INTO platform.error_attribution (student_id, subject, skill_local_id, subject_category, cross_subject_category,
  confidence, algorithm_version, evidence_ids, source_request_id)
VALUES (${lit(req.context.student_ref)}, 'aya', ${litOrNull(local(a.skill_node))}, ${lit(a.subject_category)},
  ${lit(a.cross_subject_category)}, ${a.confidence}, ${lit(a.algorithm_version)}, ${uuids(a.evidence_ids)}, ${lit(req.context.request_id)});`;
}

/** 重试场景：学习服务按 evidence_id 去重，已有的证据不再写入 */
export function persistEvidenceOnly(req: Json, resp: Json, method: string): string {
  return `BEGIN;\nSET LOCAL ROLE starbuddy_learning;\n${evidenceSql(req, resp, method)}\nCOMMIT;`;
}

export function persistGrade(req: Json, resp: Json): string {
  const r = resp.result;
  const responseEvidence = resp.evidence.find((e: Json) => e.kind !== "verifier_trace");
  const dims = r.dimensions
    .map(
      (d: Json) => `(${lit(d.dimension_id)}, ${litOrNull(d.score)}::numeric, ${d.max_score}, ${lit(d.judged_by)}, ${d.subjective},
    ${litOrNull(d.rule_ref)}, ${d.confidence}, ${uuids(d.evidence_ids)})`,
    )
    .join(",\n    ");
  return `BEGIN;
SET LOCAL ROLE starbuddy_learning;
${evidenceSql(req, resp, "grade")}
${attemptSql(req, req.attempt, responseEvidence.evidence_id)}
WITH g AS (
  INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
    max_total, calibrated, confidence, source_version, model_version, verifier_id, verifier_version, verifier_reason_codes,
    human_review_required, review_reasons, semantics, evidence_ids, source_request_id)
  VALUES (${lit(req.context.student_ref)}, 'aya', ${lit(req.attempt.attempt_id)}, ${lit(req.attempt.occurred_at)}, ${lit(r.verdict)},
    ${lit(r.rubric_version)}, ${litOrNull(r.score.total)}::numeric, ${r.score.max_total}, ${r.score.calibrated}, ${resp.confidence},
    ${lit(resp.source_version)}, ${lit(resp.model_version)}, ${lit(r.verifier.verifier_id)}, ${lit(r.verifier.version)},
    ${texts(r.verifier.reason_codes)}, ${resp.human_review_required}, ${texts(resp.review_reasons ?? [], "platform.review_reason")},
    ${lit(resp.semantics)}, ${uuids(resp.evidence_ids)}, ${lit(req.context.request_id)})
  RETURNING id)
INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, rule_ref, confidence, evidence_ids)
SELECT g.id, v.* FROM g, (VALUES
    ${dims}) AS v(dimension_id, score, max_score, judged_by, subjective, rule_ref, confidence, evidence_ids);
${(r.error_attributions ?? []).map((a: Json) => attributionSql(req, a)).join("\n")}
COMMIT;`;
}

export function persistDiagnose(req: Json, resp: Json): string {
  // 作答证据按顺序两两一组：[作答, 验证器轨迹]
  const attempts = req.attempts
    .map((a: Json, i: number) => attemptSql(req, a, resp.evidence[2 * i].evidence_id))
    .join("\n");
  const observations = resp.result.observations
    .map(
      (o: Json) => `INSERT INTO platform.skill_observation (student_id, subject, skill_local_id, outcome, local_level, confidence,
  algorithm_version, evidence_ids, source_request_id)
VALUES (${lit(req.context.student_ref)}, 'aya', ${lit(local(o.skill_node)!)}, ${lit(o.outcome)}, ${litOrNull(o.local_level)},
  ${o.confidence}, ${lit(o.algorithm_version)}, ${uuids(o.evidence_ids)}, ${lit(req.context.request_id)});`,
    )
    .join("\n");
  return `BEGIN;
SET LOCAL ROLE starbuddy_learning;
${evidenceSql(req, resp, "diagnose")}
${attempts}
${observations}
${resp.result.error_attributions.map((a: Json) => attributionSql(req, a)).join("\n")}
COMMIT;`;
}
