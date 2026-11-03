// 白板文字的安全检查与宽度估算（确定性，不依赖字体或浏览器排版）。
// 渲染时文字一律作为文本节点输出（React 转义），这里的检查是内容层的白名单：DSL 里不该出现标记、链接与脚本的写法。

/**
 * 不可见与易混字符，用 Unicode 类别写：Cc（C0/C1 控制符，含换行与制表：白板一条文字就是一行）、Cf（格式字符：零宽、方向标记、
 * 双向文本控制符、BOM、软连字符等）、Zl / Zp（行、段分隔符）、Cs（落单的代理项）、Co（私用区）、Cn（未分配码位）。
 * 覆盖 apps/web 富文本白名单拒绝的同一批字符，并且更严。
 */
const INVISIBLE_RE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}\p{Cs}\p{Co}\p{Cn}]/u;

/** HTML 的写法：标签起始（<b、</、<!、<?）与字符实体（&lt; &#60;）。单独的 < >（如“x < 3”）不算 */
const HTML_RE = /<[A-Za-z!/?]|&#?[A-Za-z0-9]+;/u;

/**
 * 链接、外部资源与脚本的写法：任何 scheme://、协议相对的 //、www.、常见伪协议（javascript: data: vbscript: file: mailto: blob:）、
 * HTML 事件属性（onxxx=）。白板文字只是教学内容，不需要这些；宁可误拒，不放过。
 */
const LINK_OR_SCRIPT_RE =
  /(?:[a-z][a-z0-9+.-]*:\/\/|\/\/|\bwww\.|\b(?:javascript|data|vbscript|file|mailto|blob)\s*:|\bon[a-z]+\s*=)/iu;

export type TextProblem = 'invisible_char' | 'html' | 'link_or_script';

/** 一段白板文字的问题（没有问题返回 null；不检查长度） */
export function textProblem(s: string): TextProblem | null {
  if (INVISIBLE_RE.test(s)) return 'invisible_char';
  if (HTML_RE.test(s)) return 'html';
  if (LINK_OR_SCRIPT_RE.test(s)) return 'link_or_script';
  return null;
}

/** 按码点计的长度 */
export const codePoints = (s: string): number => [...s].length;

/**
 * 估算文字宽度（以字号 em 计）：可打印 ASCII 记 0.6 em，其余（汉字、全角标点、数学符号、上下标）一律记 1 em。
 * 偏宽的估计：校验器用它判断文字会不会超出画布右边，渲染器用它算高亮带的宽度。
 */
export function textWidthEm(s: string): number {
  let w = 0;
  for (const ch of s) w += /^[\x20-\x7E]$/u.test(ch) ? 0.6 : 1;
  return w;
}
