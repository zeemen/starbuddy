// 开发身份桩可选的合成 staff（审核台）：与 services/learning/review_console/devauth.py 的 DEV_STAFF 一致，全部合成（规则 8）。
// 只用于 /dev/login；审核台 PR 4 的 staff 登录上线后删除。
export interface SyntheticStaff {
  account_id: string;
  label: string;
}

export const SYNTHETIC_STAFF: readonly SyntheticStaff[] = [
  { account_id: '5f0e0000-0000-4000-8000-0000000000e1', label: '合成教研 T1' },
  { account_id: '5f0e0000-0000-4000-8000-0000000000e2', label: '合成教研 T2' },
];
