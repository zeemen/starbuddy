// 合成夹具：引用内置模块，加载钩子应拒绝并记“无法加载”
import { readFileSync } from "node:fs";
export const UNITS = [{ grade: 7, semester: "上", order: 1, code: "U1", title: "单元一", probe: typeof readFileSync }];
