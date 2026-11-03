-- 合成夹具
create type domain_t as enum ('D1','D2');
create type level_t as enum ('L1','L2');
create type error_t as enum ('知识性','审题性');
create type student_seg_t as enum ('基础薄弱','高分冲刺');
create table if not exists profiles (
  id uuid primary key,
  display_name text,
  segment student_seg_t
);
create table if not exists ability_state (
  student_id uuid not null,
  domain domain_t not null,
  mastery numeric,
  primary key (student_id, domain)
);
create table if not exists textbook_catalog (
  id uuid primary key,
  must_recite boolean not null default false
);
alter table ability_state add column if not exists data_confidence numeric;
