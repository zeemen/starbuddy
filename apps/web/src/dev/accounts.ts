// 开发身份桩可选的合成账号：与 db/seeds/dev/star-map-synthetic.sql 一一对应，全部合成（规则 8）。
// 只用于 /dev/login；4-H 删除。
export interface SyntheticAccount {
  account_id: string;
  label: string;
  /** 登录后默认打开谁的星图 */
  student_id: string;
}

export const SYNTHETIC_ACCOUNTS: readonly SyntheticAccount[] = [
  { account_id: '5a000000-0000-4000-8000-0000000000b1', label: '合成学生 S1（数学、道法）', student_id: '5a000000-0000-4000-8000-0000000000c1' },
  { account_id: '5a000000-0000-4000-8000-0000000000b2', label: '合成学生 S2（S1 的妹妹）', student_id: '5a000000-0000-4000-8000-0000000000c2' },
  { account_id: '5a000000-0000-4000-8000-0000000000a1', label: '合成家长 gS（S1、S2 的家长）', student_id: '5a000000-0000-4000-8000-0000000000c1' },
  { account_id: '5a000000-0000-4000-8000-0000000000a2', label: '合成家长 gO（无关家庭）', student_id: '5a000000-0000-4000-8000-0000000000c3' },
];

export const STUDENT = {
  S1: '5a000000-0000-4000-8000-0000000000c1',
  S2: '5a000000-0000-4000-8000-0000000000c2',
  S3: '5a000000-0000-4000-8000-0000000000c3',
} as const;
