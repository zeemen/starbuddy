// 页面主题（角色视觉家族规范第三章的宣白、鸦青、石灰）。星光金不在这里（见 src/star/tokens.ts）。
// 描边色经 CSS 变量随主题切换：
//   浅色：亮 / 半亮描边鸦青；未亮描边原为石灰 #8C9099，对宣白只有 2.88:1，不到 3:1（WCAG 1.4.11），
//         视觉负责人给色值之前用“鸦青 55% 叠在宣白上”的纯色 #7F8490 占位（3.37:1，待人确认 #6）。
//         spec 写的占位是鸦青 50%，实测只有 2.95:1，所以取 55%。
//   深色：底色鸦青；亮 / 半亮描边宣白 60%（#9FA3AA，5.63:1）；未亮描边石灰（4.46:1）。
export const XUANBAI = '#F4F3EE';
export const YAQING = '#1F2A44';
export const SHIHUI = '#8C9099';

const LIGHT = `
  --bg: ${XUANBAI};
  --fg: ${YAQING};
  --muted: #4A5163;
  --rule: #C9CBD1;
  --star-stroke: ${YAQING};
  --star-stroke-unlit: #7F8490;`;

const DARK = `
  --bg: ${YAQING};
  --fg: ${XUANBAI};
  --muted: #C4C7CE;
  --rule: #3A4560;
  --star-stroke: #9FA3AA;
  --star-stroke-unlit: ${SHIHUI};`;

// 页面跟随系统主题；任意元素也可以用 data-theme="light" / "dark" 固定主题（符号样张页并排展示两种主题）
export const THEME_CSS = `
:root { color-scheme: light dark; }
:root, [data-theme="light"] {${LIGHT}
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {${DARK}
  }
}
[data-theme="dark"] {${DARK}
}
`;
