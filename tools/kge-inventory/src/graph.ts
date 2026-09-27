// 知识图谱结构检查：环、传递边、孤点（spec 设计要点 3 通用项 2、3）。
// 边的方向统一为“前置 → 后续”。只对能解析为节点 ID 的边计算；自然语言前置与悬空边由调用方另行统计。

export type Edge = readonly [from: string, to: string];

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

function adjacency(nodes: readonly string[], edges: readonly Edge[]): Map<string, string[]> {
  const adj = new Map<string, string[]>(nodes.map((n) => [n, []]));
  for (const [a, b] of edges) {
    if (!adj.has(a) || !adj.has(b)) continue;
    adj.get(a)!.push(b);
  }
  for (const list of adj.values()) list.sort(cmp);
  return adj;
}

/** 环：大小 > 1 的强连通分量，以及自环。每个环内按 ID 排序，环之间按首个 ID 排序。 */
export function findCycles(nodes: readonly string[], edges: readonly Edge[]): string[][] {
  const adj = adjacency(nodes, edges);
  const index = new Map<string, number>();
  const low = new Map<string, number>();
  const onStack = new Set<string>();
  const stack: string[] = [];
  const out: string[][] = [];
  let counter = 0;

  // 迭代版 Tarjan，避免深递归
  for (const root of [...adj.keys()].sort(cmp)) {
    if (index.has(root)) continue;
    const work: { v: string; i: number }[] = [{ v: root, i: 0 }];
    index.set(root, counter);
    low.set(root, counter++);
    stack.push(root);
    onStack.add(root);
    while (work.length > 0) {
      const top = work[work.length - 1]!;
      const succ = adj.get(top.v)!;
      if (top.i < succ.length) {
        const w = succ[top.i++]!;
        if (!index.has(w)) {
          index.set(w, counter);
          low.set(w, counter++);
          stack.push(w);
          onStack.add(w);
          work.push({ v: w, i: 0 });
        } else if (onStack.has(w)) {
          low.set(top.v, Math.min(low.get(top.v)!, index.get(w)!));
        }
        continue;
      }
      work.pop();
      if (work.length > 0) {
        const parent = work[work.length - 1]!.v;
        low.set(parent, Math.min(low.get(parent)!, low.get(top.v)!));
      }
      if (low.get(top.v) === index.get(top.v)) {
        const comp: string[] = [];
        let w: string;
        do {
          w = stack.pop()!;
          onStack.delete(w);
          comp.push(w);
        } while (w !== top.v);
        const selfLoop = comp.length === 1 && adj.get(comp[0]!)!.includes(comp[0]!);
        if (comp.length > 1 || selfLoop) out.push(comp.sort(cmp));
      }
    }
  }
  return out.sort((a, b) => cmp(a[0]!, b[0]!));
}

/**
 * 传递边：a→c 存在，且从 a 出发不经过这条边、经由另一个直接后续也能到达 c。
 * KGE 要求“只连直接前置，传递关系由图计算”，所以这类边是多余的。
 */
export function findTransitiveEdges(nodes: readonly string[], edges: readonly Edge[]): Edge[] {
  const adj = adjacency(nodes, edges);
  const reachCache = new Map<string, Set<string>>();
  const reach = (start: string): Set<string> => {
    const hit = reachCache.get(start);
    if (hit) return hit;
    const seen = new Set<string>();
    const queue = [start];
    while (queue.length > 0) {
      const v = queue.shift()!;
      for (const w of adj.get(v) ?? []) {
        if (!seen.has(w)) {
          seen.add(w);
          queue.push(w);
        }
      }
    }
    reachCache.set(start, seen);
    return seen;
  };
  const out: Edge[] = [];
  const unique = new Set<string>();
  for (const [a, c] of edges) {
    if (!adj.has(a) || !adj.has(c) || a === c) continue;
    const k = `${a}\u0000${c}`;
    if (unique.has(k)) continue;
    unique.add(k);
    const others = adj.get(a)!.filter((b) => b !== c);
    if (others.some((b) => b === c || reach(b).has(c))) out.push([a, c]);
  }
  return out.sort((x, y) => cmp(x[0], y[0]) || cmp(x[1], y[1]));
}

/** 孤点：不在任何可解析边上，也没有被题目或 Rubric 引用。 */
export function findOrphans(nodes: readonly string[], edges: readonly Edge[], referenced: ReadonlySet<string>): string[] {
  const touched = new Set<string>();
  const known = new Set(nodes);
  for (const [a, b] of edges) {
    if (known.has(a) && known.has(b)) {
      touched.add(a);
      touched.add(b);
    }
  }
  return nodes.filter((n) => !touched.has(n) && !referenced.has(n)).sort(cmp);
}
