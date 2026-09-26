-- 策略快照（3-B spec 第 7 节第 4 条）：表、策略名、命令、角色、宽松 / 限制、USING、WITH CHECK，存进 psql 变量 snap_policies。
SELECT coalesce(string_agg(line, E'\n' ORDER BY line), '') AS snap_policies FROM (
  SELECT format('%s.%s|%s|%s|%s|%s|using=%s|check=%s', schemaname, tablename, policyname, cmd,
                array_to_string(roles, ','), permissive, coalesce(qual, '-'), coalesce(with_check, '-')) AS line
    FROM pg_policies WHERE schemaname NOT IN ('t')
) q \gset
