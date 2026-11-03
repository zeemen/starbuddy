---
page_id: MATH.SYN.EQ.LINEAR
page_type: kp
subject: aya
node_id: MATH.SYN.EQ.LINEAR
map_version: 0.9.0
schema_version: 1
page_version: 1.0.0
risk_level: low
rebuild_kind: render
locked_sections: [definition, curriculum]
atom_refs:
  - {type: node, id: MATH.SYN.EQ.LINEAR, hash: sha256:4f6f299232d42ba1}
  - {type: curriculum, id: 2022/SYN-EQ-01, hash: sha256:3578bb411f124cfc}
  - {type: edge, id: MATH.SYN.EQ.TRANSPOSE>prerequisite>MATH.SYN.EQ.LINEAR, hash: sha256:f2487518bd50455e}
  - {type: edge, id: MATH.SYN.EQ.LINEAR>prerequisite>MATH.SYN.EQ.APPLY, hash: sha256:55cf39e05857a562}
  - {type: projection, id: syn-tp-001, hash: sha256:2a1163e685f3864e}
  - {type: region, id: syn-rp-002, hash: sha256:def57f59b2e15704}
---
# 一元一次方程

<!-- kge:section id=definition kind=locked -->
## 定义

**一元一次方程** [@node:MATH.SYN.EQ.LINEAR]

包括：只含一个未知数且未知数的次数是一的整式方程。不包括：二元一次方程组。 [@node:MATH.SYN.EQ.LINEAR]
<!-- kge:end id=definition -->

<!-- kge:section id=curriculum kind=locked -->
## 课标要求

- 【理解】合成课标条目：理解一元一次方程的概念。 [@curriculum:2022/SYN-EQ-01]
<!-- kge:end id=curriculum -->

<!-- kge:section id=relations kind=render -->
## 前置与后续

前置：
- [[MATH.SYN.EQ.TRANSPOSE]] 移项 [@edge:MATH.SYN.EQ.TRANSPOSE>prerequisite>MATH.SYN.EQ.LINEAR]

后续：
- [[MATH.SYN.EQ.APPLY]] 一元一次方程的应用 [@edge:MATH.SYN.EQ.LINEAR>prerequisite>MATH.SYN.EQ.APPLY]
<!-- kge:end id=relations -->

<!-- kge:section id=textbook kind=render -->
## 教材位置

- syn-ed（2024）合成七上 合成第三章，第 80–82 页 {{quote:syn-tp-001}} [@projection:syn-tp-001]
<!-- kge:end id=textbook -->

<!-- kge:section id=region kind=render -->
## 地区权重

- syn-region 2025：权重 0.030；常见题位：填空题 [@region:syn-rp-002]
<!-- kge:end id=region -->

<!-- kge:section id=key_points kind=narrative -->
## 讲解要点

解一元一次方程的一般步骤是去分母、去括号、移项、合并同类项、系数化为一。[@node:MATH.SYN.EQ.LINEAR]

$$
2x + 3 = 7 % check x=2
$$

把求得的解代入原方程，左右两边相等才说明解正确。[@curriculum:2022/SYN-EQ-01]
<!-- kge:end id=key_points -->

<!-- kge:section id=pitfalls kind=narrative -->
## 易错

去括号时括号前是负号，括号里的各项都要改变符号。[@node:MATH.SYN.EQ.LINEAR]

移项时漏改符号会直接得到错误的解。[@edge:MATH.SYN.EQ.TRANSPOSE>prerequisite>MATH.SYN.EQ.LINEAR]
<!-- kge:end id=pitfalls -->

<!-- kge:section id=frequent_questions kind=narrative -->
## 高频设问

- 判断一个等式是不是一元一次方程。[@node:MATH.SYN.EQ.LINEAR]
- 已知方程的解，求方程中字母系数的值。[@node:MATH.SYN.EQ.LINEAR]
<!-- kge:end id=frequent_questions -->
