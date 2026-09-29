---
page_id: MATH.SYN.EQ.TRANSPOSE
page_type: kp
subject: aya
node_id: MATH.SYN.EQ.TRANSPOSE
map_version: 0.9.0
schema_version: 1
page_version: 1.0.0
risk_level: low
rebuild_kind: render
locked_sections: [definition, curriculum]
atom_refs:
  - {type: node, id: MATH.SYN.EQ.TRANSPOSE, hash: sha256:4530dc9f5adbf487}
  - {type: curriculum, id: 2022/SYN-EQ-02, hash: sha256:b9724491cf7b89ff}
  - {type: edge, id: MATH.SYN.EQ.TRANSPOSE>prerequisite>MATH.SYN.EQ.LINEAR, hash: sha256:f2487518bd50455e}
  - {type: projection, id: syn-tp-002, hash: sha256:3080e1eebcfd07c0}
---
# 移项

<!-- kge:section id=definition kind=locked -->
## 定义

**移项** [@node:MATH.SYN.EQ.TRANSPOSE]

包括：把等式一边的项改变符号后移到另一边。不包括：分式方程的去分母。 [@node:MATH.SYN.EQ.TRANSPOSE]
<!-- kge:end id=definition -->

<!-- kge:section id=curriculum kind=locked -->
## 课标要求

- 【运用】合成课标条目：能用移项解一元一次方程。 [@curriculum:2022/SYN-EQ-02]
<!-- kge:end id=curriculum -->

<!-- kge:section id=relations kind=render -->
## 前置与后续

前置：
- （暂无） [@node:MATH.SYN.EQ.TRANSPOSE]

后续：
- [[MATH.SYN.EQ.LINEAR]] 一元一次方程 [@edge:MATH.SYN.EQ.TRANSPOSE>prerequisite>MATH.SYN.EQ.LINEAR]
<!-- kge:end id=relations -->

<!-- kge:section id=textbook kind=render -->
## 教材位置

- syn-ed（2024）合成七上 合成第三章，第 86 页 {{quote:syn-tp-002}} [@projection:syn-tp-002]
<!-- kge:end id=textbook -->

<!-- kge:section id=region kind=render -->
## 地区权重

（暂无） [@node:MATH.SYN.EQ.TRANSPOSE]
<!-- kge:end id=region -->

<!-- kge:section id=key_points kind=narrative -->
## 讲解要点

移项的依据是等式的基本性质：等式两边同时加上或减去同一个数，结果仍相等。[@node:MATH.SYN.EQ.TRANSPOSE]

$$
x + 5 = 9 % check x=4
$$

移项后要检查每一项的符号是否已经改变。[@curriculum:2022/SYN-EQ-02]
<!-- kge:end id=key_points -->

<!-- kge:section id=pitfalls kind=narrative -->
## 易错

只移动了项却没有改变符号，是移项中出现最多的错误。[@node:MATH.SYN.EQ.TRANSPOSE]
<!-- kge:end id=pitfalls -->

<!-- kge:section id=frequent_questions kind=narrative -->
## 高频设问

- 说明某一步变形用到了等式的哪条性质。[@node:MATH.SYN.EQ.TRANSPOSE]
- 指出移项过程中符号出错的一步。[@node:MATH.SYN.EQ.TRANSPOSE]
<!-- kge:end id=frequent_questions -->
