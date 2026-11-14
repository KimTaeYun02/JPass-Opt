type Cost = { time: number; fee: number };
type Label = Cost & { end: number; key: string; previous?: Label };
type State = { labels: Label[]; maxTime: bigint; maxFee: bigint };
export type DpPath = Cost & { indices: number[] };
export type DpResult = {
  paths: DpPath[]; algorithm: 'held-karp';
  dpStates: number; dpTransitions: number; evaluatedOrders: 0;
};

const compare = (a: Label, b: Label): number => {
  if (a.time !== b.time) return a.time < b.time ? -1 : 1;
  if (a.fee !== b.fee) return a.fee < b.fee ? -1 : 1;
  return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
};
const keep = (labels: Label[], candidate: Label, limit: number): void => {
  const index = labels.findIndex(label => compare(candidate, label) < 0);
  if (index >= 0) labels.splice(index, 0, candidate);
  else if (labels.length < limit) labels.push(candidate);
  if (labels.length > limit) labels.pop();
};

// Caller supplies a validated complete matrix indexed by sorted place IDs.
// Prefixes sharing (mask, end) admit identical suffixes, so retaining top-k is exact.
export function kBestHeldKarp(matrix: readonly (readonly (Cost | null)[])[], limit: number): DpResult {
  const n = matrix.length;
  const size = 1 << n;
  const table: (State | undefined)[] = Array(size * n);
  const bounds = matrix.map(row => row.map(edge => edge && {
    time: BigInt(edge.time), fee: BigInt(edge.fee),
  }));
  const safeLimit = BigInt(Number.MAX_SAFE_INTEGER);
  let dpStates = 0;
  let dpTransitions = 0;
  for (let mask = 1; mask < size; mask++) {
    for (let end = 0; end < n; end++) {
      if (!(mask & (1 << end))) continue;
      const previousMask = mask ^ (1 << end);
      const state: State = { labels: [], maxTime: 0n, maxFee: 0n };
      // Monotone rank characters make equal-length keys follow sorted ID order.
      const keyPart = String.fromCharCode(65 + end);
      if (!previousMask) state.labels.push({ time: 0, fee: 0, end, key: keyPart });
      else for (let previous = 0; previous < n; previous++) {
        if (!(previousMask & (1 << previous))) continue;
        const prefix = table[previousMask * n + previous]!;
        const edge = matrix[previous][end]!;
        const bound = bounds[previous][end]!;
        const maxTime = prefix.maxTime + bound.time;
        const maxFee = prefix.maxFee + bound.fee;
        // Expensive discarded prefixes must not hide an unsafe possible sum.
        // Nonnegative costs and completeness let every partial path be extended.
        if (maxTime > safeLimit || maxFee > safeLimit) {
          throw new Error('합계가 안전한 정수 범위를 초과했습니다.');
        }
        if (maxTime > state.maxTime) state.maxTime = maxTime;
        if (maxFee > state.maxFee) state.maxFee = maxFee;
        dpTransitions++;
        for (const label of prefix.labels) keep(state.labels, {
          time: label.time + edge.time, fee: label.fee + edge.fee,
          end, key: label.key + keyPart, previous: label,
        }, limit);
      }
      table[mask * n + end] = state;
      dpStates++;
    }
  }
  const winners: Label[] = [];
  for (let end = 0; end < n; end++) {
    for (const label of table[(size - 1) * n + end]!.labels) keep(winners, label, limit);
  }
  const paths = winners.map(label => {
    const indices: number[] = [];
    for (let cursor: Label | undefined = label; cursor; cursor = cursor.previous) indices.push(cursor.end);
    return { time: label.time, fee: label.fee, indices: indices.reverse() };
  });
  return { paths, algorithm: 'held-karp', dpStates, dpTransitions, evaluatedOrders: 0 };
}
