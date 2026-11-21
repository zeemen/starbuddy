// PLT-21 写路径：合成内容包的登记 SQL。复用阿鸦的内容登记脚本（tools/content-register/aya.ts 的 registrationSql，
// 只增不改、登记时准入最高 pending_review），只把内容包换成压测栈生成的合成副本（命令行参数给文件路径）；
// 先经适配器自己的 indexPack 校验（与适配器启动时同一套检查），不合格即失败。只输出 SQL，不连接任何数据库。
//   node --no-warnings tools/loadtest/node/aya-register.mts <合成内容包.json>
import { readFileSync } from 'node:fs';
import { registrationSql } from '../../content-register/aya.ts';
import { indexPack } from '../../../adapters/aya/src/content.ts';

const path = process.argv[2];
if (!path) {
  console.error('aya-register: 缺少内容包路径');
  process.exit(2);
}
process.stdout.write(registrationSql(indexPack(JSON.parse(readFileSync(path, 'utf8')))));
