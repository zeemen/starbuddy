// 审核台页面与 server action 取当前 staff：读开发 staff cookie 与请求的 Host（只在服务端调用）。PR 4 换成真实会话。
import { cookies, headers } from 'next/headers';
import { DEV_STAFF_COOKIE, devStaffFromCookie } from './dev-auth.ts';

export async function currentStaff(): Promise<string | null> {
  return devStaffFromCookie((await cookies()).get(DEV_STAFF_COOKIE)?.value, (await headers()).get('host'));
}
