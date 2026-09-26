// 占位：任何代码试图使用 sharp 都直接失败，而不是悄悄走 LGPL 的 libvips（specs/star-map-sprint3/spec.md 待人确认 #10）
throw new Error('sharp is intentionally not installed in apps/web (LGPL libvips, ADR 07); images.unoptimized is on');
