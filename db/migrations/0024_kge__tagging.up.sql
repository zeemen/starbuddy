-- 0024 kge · 打标来源与低置信候选队列（specs/kge-02-atoms-tagging/spec.md 设计要点 3，KGE-02）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限、知识口径）。依赖 0011、0019、0023（review_current_staff）。
--   - kge.item_tag 记下打标的来源、置信度、打标器版本与模型版本（规则 6：推断记算法版本与置信度）；
--   - kge.tag_candidate 是公共层的低置信候选队列：没有学生键（规则 13），只有知识服务能写候选；
--   - 教研（active staff，经 starbuddy_review）只经 kge.decide_tag_candidate 接受或驳回；草稿地图版本上接受主考点候选时写回 item_tag，
--     已发布版本上的打标仍走 0019 的审核记录，这里只记结论。
-- 注意：item_tag 加列会改变 kge.map_version_digest 与 0019 逐条打标签字摘要（row_digest(to_jsonb(item_tag))）的输入，
--       已有的版本签字与打标签字在演练库上都会对不上，需要重新签字（本迁移之前没有已发布的阿鸦版本；写进演练清单）。

ALTER TABLE kge.item_tag
  ADD COLUMN tag_confidence     numeric(4, 3) CHECK (tag_confidence BETWEEN 0 AND 1),
  ADD COLUMN tag_source         text CHECK (tag_source IN ('rule', 'vector', 'model', 'human')),
  ADD COLUMN tagger_version     platform.versioned_name,
  ADD COLUMN tag_model_version  platform.model_version,
  -- A 级结论所依据的每一层与版本（规则层 + 与之一致的另一层；只有一层时也写一个元素），规则 6：推断记算法版本（契约审查建议 3）
  ADD COLUMN tag_basis          jsonb NOT NULL DEFAULT '[]'
    CHECK (jsonb_typeof(tag_basis) = 'array' AND jsonb_array_length(tag_basis) <= 3 AND pg_column_size(tag_basis) <= 1024
           AND NOT kge.has_personal_key(tag_basis));
-- 来源类型加 legacy：旧产品题库导入的题，来源待核实，不能标成“原创”（审查建议：版权溯源）
ALTER TABLE kge.item_tag DROP CONSTRAINT item_tag_source_kind_check;
ALTER TABLE kge.item_tag ADD CONSTRAINT item_tag_source_kind_check
  CHECK (source_kind IN ('textbook', 'exam_paper', 'original', 'adapted', 'third_party', 'legacy'));
-- 来源、打标器版本、置信度同有同无（0024 之前的行三者都空）；模型层打的标必须带模型版本，其他层不能带
ALTER TABLE kge.item_tag ADD CONSTRAINT item_tag_source_complete
  CHECK ((tag_source IS NULL) = (tagger_version IS NULL) AND (tag_source IS NULL) = (tag_confidence IS NULL));
ALTER TABLE kge.item_tag ADD CONSTRAINT item_tag_model_version
  CHECK ((tag_source = 'model') = (tag_model_version IS NOT NULL) OR (tag_source IS NULL AND tag_model_version IS NULL));

CREATE TABLE kge.tag_candidate (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  map_version_id  uuid NOT NULL REFERENCES kge.map_version (id),
  subject         platform.subject NOT NULL,
  item_id         platform.skill_local_id NOT NULL,
  node_id         kge.node_id NOT NULL,
  role            text NOT NULL CHECK (role IN ('main', 'secondary')),
  layer           text NOT NULL CHECK (layer IN ('rule', 'vector', 'model')),
  confidence      numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  tagger_version  platform.versioned_name NOT NULL,
  model_version   platform.model_version,
  reason_code     text NOT NULL CHECK (reason_code ~ '^[a-z0-9_]{1,48}$'),
  grade           text NOT NULL CHECK (grade IN ('A', 'B')),
  -- 题目来源类型（与 item_tag.source_kind 同一套取值）：接受主考点候选写回 item_tag 时沿用
  item_source_kind text NOT NULL CHECK (item_source_kind IN ('textbook', 'exam_paper', 'original', 'adapted', 'third_party', 'legacy')),
  status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'rejected')),
  -- 处理人不存在公共层（不能挂账号 ID，80 攻击面断言），记在 platform.tag_candidate_signoff
  decided_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tag_candidate_model_version CHECK ((layer = 'model') = (model_version IS NOT NULL)),
  CONSTRAINT tag_candidate_decision CHECK ((status = 'open') = (decided_at IS NULL)),
  -- 题目身份只在 platform.item；节点必须属于同一地图版本
  FOREIGN KEY (subject, item_id) REFERENCES platform.item (subject, item_id),
  FOREIGN KEY (map_version_id, node_id) REFERENCES kge.knowledge_node (map_version_id, id),
  UNIQUE (map_version_id, subject, item_id, node_id, role, layer, tagger_version)
);
CREATE INDEX tag_candidate_open_idx ON kge.tag_candidate (map_version_id, subject, created_at) WHERE status = 'open';
COMMENT ON TABLE kge.tag_candidate IS
  '打标候选（KGE-02）：规则、向量、模型三层产出的候选与置信度；B 级进这里等教研处理。公共层，没有学生数据（规则 13）；只追加，结论只经 kge.decide_tag_candidate';
-- 学科与题目、节点的学科一致（沿用 0011 的 guard_subject）
CREATE TRIGGER guard_subject BEFORE INSERT OR UPDATE ON kge.tag_candidate FOR EACH ROW EXECUTE FUNCTION kge.guard_subject();

-- 知识服务：读、写候选（只能以“待处理”写入）；不能改结论（没有 UPDATE 授权）
GRANT SELECT, INSERT ON kge.tag_candidate TO starbuddy_kge;
-- 决定函数的属主：读候选、改结论、写回草稿版本的 item_tag
GRANT SELECT ON kge.tag_candidate TO starbuddy_authz;
GRANT UPDATE (status, decided_at) ON kge.tag_candidate TO starbuddy_authz;
GRANT INSERT, UPDATE (node_ids, tag_source, tag_confidence, tag_model_version, tagger_version, tag_basis) ON kge.item_tag TO starbuddy_authz;
ALTER TABLE kge.tag_candidate ENABLE ROW LEVEL SECURITY;
ALTER TABLE kge.tag_candidate FORCE ROW LEVEL SECURITY;
CREATE POLICY tag_candidate_kge_read ON kge.tag_candidate FOR SELECT TO starbuddy_kge USING (true);
CREATE POLICY tag_candidate_kge_submit ON kge.tag_candidate FOR INSERT TO starbuddy_kge
  WITH CHECK (status = 'open' AND decided_at IS NULL);

-- 候选结论的处理人（沿用 0018 签字表的做法）：只由 kge.decide_tag_candidate 写入，只追加；没有任何策略，只有属主 authz 能读写
CREATE TABLE platform.tag_candidate_signoff (
  tag_candidate_id  uuid PRIMARY KEY REFERENCES kge.tag_candidate (id),
  account_id        uuid NOT NULL REFERENCES platform.account (id),
  decision          text NOT NULL CHECK (decision IN ('accepted', 'rejected')),
  signed_at         timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE platform.tag_candidate_signoff IS '打标候选（kge.tag_candidate）的处理人：只由 kge.decide_tag_candidate 写入（0024）';
CREATE TRIGGER reject_signoff_change BEFORE UPDATE OR DELETE ON platform.tag_candidate_signoff
  FOR EACH ROW EXECUTE FUNCTION platform.reject_signoff_change();
CREATE TRIGGER reject_signoff_truncate BEFORE TRUNCATE ON platform.tag_candidate_signoff
  FOR EACH STATEMENT EXECUTE FUNCTION platform.reject_signoff_change();
GRANT SELECT, INSERT ON platform.tag_candidate_signoff TO starbuddy_authz;
ALTER TABLE platform.tag_candidate_signoff ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.tag_candidate_signoff FORCE ROW LEVEL SECURITY;

-- ---------- “人工”打标只能来自教研处理（安全、契约审查建议 1） ----------
-- 知识服务不能自己写出 tag_source = 'human'，也不能改人工确认过的打标内容；只有决定函数的属主 authz 能写。
-- 其他列（例如 0019 的审核状态）照常可改。
CREATE FUNCTION kge.guard_human_tag() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, pg_temp
  AS $$
BEGIN
  IF current_user = 'starbuddy_authz' THEN
    RETURN NEW;
  END IF;
  IF NEW.tag_source = 'human' AND (TG_OP = 'INSERT' OR OLD.tag_source IS DISTINCT FROM 'human') THEN
    RAISE EXCEPTION '“人工”打标只能由教研经 kge.decide_tag_candidate 写入' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.tag_source = 'human'
     AND (NEW.main_node_id, NEW.node_ids, NEW.tag_source, NEW.tag_confidence, NEW.tagger_version, NEW.tag_basis)
         IS DISTINCT FROM (OLD.main_node_id, OLD.node_ids, OLD.tag_source, OLD.tag_confidence, OLD.tagger_version, OLD.tag_basis) THEN
    RAISE EXCEPTION '人工确认过的打标不能由知识服务改' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION kge.guard_human_tag() FROM PUBLIC;
CREATE TRIGGER guard_human_tag BEFORE INSERT OR UPDATE ON kge.item_tag FOR EACH ROW EXECUTE FUNCTION kge.guard_human_tag();

-- ---------- 教研处理候选（只授 starbuddy_review） ----------
-- 草稿版本上的接受必须能写回，写不回的接受直接报错（不留“已接受却没生效”的候选，审查建议 4）：
--   主考点：该题还没有打标、候选节点是知识节点层（不是主题或领域）→ 写回 item_tag（来源 human、置信度 1），
--           同题其他待处理的主考点候选一并驳回（记签字）；
--   次要节点：该题已有打标、节点不重复、不到 3 个（小题 1–3 个节点，KGE V1.1 :745）→ 追加，整行来源改为 human。
-- 已发布或已撤下的版本只记结论，打标变更走 0019 的审核记录。驳回只记结论。
-- 返回 'written'（写回 item_tag）或 'recorded'（只记结论）。
CREATE FUNCTION kge.decide_tag_candidate(p_candidate uuid, p_decision text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
DECLARE v_staff uuid := platform.review_current_staff(); c kge.tag_candidate; v_status text; t kge.item_tag;
        v_found boolean; v_level text; v_basis jsonb;
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('accepted', 'rejected') THEN
    RAISE EXCEPTION 'decision must be accepted or rejected' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT * INTO c FROM kge.tag_candidate WHERE id = p_candidate FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION '打标候选不存在' USING ERRCODE = 'no_data_found';
  ELSIF c.status <> 'open' THEN
    RAISE EXCEPTION '打标候选已处理' USING ERRCODE = 'check_violation';
  END IF;
  SELECT v.status INTO v_status FROM kge.map_version v WHERE v.id = c.map_version_id FOR SHARE;
  IF p_decision = 'rejected' OR v_status <> 'draft' THEN
    UPDATE kge.tag_candidate SET status = p_decision, decided_at = now() WHERE id = p_candidate;
    INSERT INTO platform.tag_candidate_signoff (tag_candidate_id, account_id, decision) VALUES (p_candidate, v_staff, p_decision);
    RETURN 'recorded';
  END IF;
  v_basis := jsonb_build_array(jsonb_build_object('layer', c.layer, 'version', coalesce(c.model_version::text, c.tagger_version::text)));
  SELECT * INTO t FROM kge.item_tag WHERE map_version_id = c.map_version_id AND subject = c.subject AND item_id = c.item_id FOR UPDATE;
  v_found := FOUND;
  IF c.role = 'main' THEN
    IF v_found THEN
      RAISE EXCEPTION '该题已有主考点，不能再接受主考点候选' USING ERRCODE = 'check_violation';
    END IF;
    SELECT n.level INTO v_level FROM kge.knowledge_node n WHERE n.map_version_id = c.map_version_id AND n.id = c.node_id;
    IF v_level IS DISTINCT FROM 'node' THEN
      RAISE EXCEPTION '主考点必须是知识节点层，不能是主题或领域' USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, source_kind, license_state,
                              tag_confidence, tag_source, tagger_version, tag_basis)
    VALUES (c.map_version_id, c.subject, c.item_id, c.node_id, ARRAY[c.node_id]::kge.node_id[], c.item_source_kind, 'unverified',
            1, 'human', c.tagger_version, v_basis);
    WITH r AS (
      UPDATE kge.tag_candidate SET status = 'rejected', decided_at = now()
       WHERE map_version_id = c.map_version_id AND subject = c.subject AND item_id = c.item_id AND role = 'main'
         AND status = 'open' AND id <> p_candidate
      RETURNING id)
    INSERT INTO platform.tag_candidate_signoff (tag_candidate_id, account_id, decision) SELECT id, v_staff, 'rejected' FROM r;
  ELSE
    IF NOT v_found OR c.node_id = ANY (t.node_ids) OR c.node_id = t.main_node_id OR cardinality(t.node_ids) >= 3 THEN
      RAISE EXCEPTION '次要节点写不回：该题还没有主考点、节点已在打标里，或已满 3 个' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE kge.item_tag
       SET node_ids = t.node_ids || c.node_id, tag_source = 'human', tag_confidence = 1, tag_model_version = NULL,
           tagger_version = coalesce(t.tagger_version, c.tagger_version),
           tag_basis = CASE WHEN jsonb_array_length(t.tag_basis) < 3 THEN t.tag_basis || v_basis ELSE t.tag_basis END
     WHERE map_version_id = c.map_version_id AND subject = c.subject AND item_id = c.item_id;
  END IF;
  UPDATE kge.tag_candidate SET status = 'accepted', decided_at = now() WHERE id = p_candidate;
  INSERT INTO platform.tag_candidate_signoff (tag_candidate_id, account_id, decision) VALUES (p_candidate, v_staff, 'accepted');
  RETURN 'written';
END $$;
ALTER FUNCTION kge.decide_tag_candidate(uuid, text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.decide_tag_candidate(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.decide_tag_candidate(uuid, text) TO starbuddy_review;
