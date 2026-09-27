-- 合成夹具
create table if not exists knowledge_nodes (
  id text primary key,
  kind text not null check (kind in ('K','S','P','M'))
);
