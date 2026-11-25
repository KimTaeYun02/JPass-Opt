# 오프라인 경로 알고리즘 실험

`web` 폴더에서 실행합니다. PC 데모의 API 모드는 백엔드 알고리즘을 사용합니다. 아래 TypeScript CLI는 별도의 오프라인 검증 도구이며 현재 백엔드 구현을 대신하지 않습니다.

자동 저장 보고서는 `web/.local-run/reports/`에 생성되며 Git에서 제외됩니다. `demo:route`와 `demo:notion`은 기본적으로 표준 출력만 사용하고 `--output <path>`를 명시하면 해당 경로에 저장합니다. 부모 폴더는 먼저 만들어 주세요.

## Five-stop route prototype

Run `npm run demo:route` to optimize five Tokyo station names using synthetic,
directed travel data in `fixtures/route-five-stops.json`. This independent CLI
prototype makes no external requests and does not require a Maps key.

`src/route-optimizer.ts` consumes each crawler result unchanged as
`{time, fee, route: [{station, line}]}`, wrapped with its `from` and `to` station
names. Held-Karp subset DP minimizes time, breaks ties by fare, then by JS
code-unit ID order. It also returns up to three ranked paths through the shared
`optimizeVisitOrders` core; `rankCandidates` retains its array return shape.
The first and last stations are free; there is no return trip. Times are fixed
snapshots and fares assume independent payments without a pass. Missing
directions and malformed results are rejected. The current input bound remains
2–8 stations. Larger bounds and multi-day allocation are separate extensions.

The cost is the supplied `result.time` unchanged. Do not add initial departure
waiting, attraction access walking, or attraction stay time. Transfer/waiting
already included in the crawler's `time` is retained. Optimization performs no
departure-time propagation or external calls. Changing stays does not change
the optimized order when the quote matrix stays fixed.

Results include `algorithm: 'held-karp'`, `dpStates`, `dpTransitions`, and the
legacy `evaluatedOrders: 0`. For five stops there are 80 populated states and
160 structural state transitions. Transitions count predecessor/end connections
once, irrespective of how many top-k labels traverse each connection. Paths use
backpointers; only returned paths are reconstructed. Independent maximum-sum
bounds reject unsafe time/fee totals even in paths discarded by top-k pruning.

Run `node scripts/demo-browser-route.mjs` for the cached actual Ekispert data,
or `npm run verify:route` to cross-check production DP against complete
permutation evaluation and an independently implemented verification DP.
The raw-time winner is Odaiba → Nezu Shrine → Tokyo Dome → Meiji Shrine →
Enoshima, 143 minutes / JPY 2,374. The previous time-dependent observations are
retained as historical data for a different order, not validation of this winner.
The lazy-routing replay remains a historical experiment using wait-inclusive
costs, not the current optimization cost definition.

The sample timings, fares and test lines are fictitious, including the transfer
test station. Replace the fixture's 20 directional quotes with actual crawler
results to compare real snapshots. Do not treat a result as a departure-time
validated itinerary. An optional `--output <path>` saves the demo report.

## Notion visit datasets

Run `npm run demo:notion`.
`fixtures/notion-visits.json` transcribes the two groups under the linked Notion
page's reference section. Place IDs are distinct from stations: Skytree and
Sumida Aquarium retain separate visits despite sharing a station. The original
`{time, fee, route}` results and station names are retained.

The page only contains four of twenty directed place pairs for the first group,
and three of twelve for the second. Their query timestamps and station access
walks are unknown. The default report therefore includes the original reference
orders, missing pairs and `needs-travel-data`, not a fabricated optimized route.
It makes zero external requests. Start time 2026-10-17 09:00 JST and 120-minute
stays are demonstration assumptions, not actual travel dates or opening hours.

To rank up to three candidates, supply explicit estimates using
`--estimates <json-path>`. The JSON is keyed by dataset IDs (`five-visits`,
`four-visits`), each containing `[{"from":"place-id","to":"place-id",
"time":30,"fee":200}]`. Fill every missing pair; reverse costs and same-station
walking are never inferred automatically. Values are illustrative caller inputs,
not measured travel information. Known snapshots take precedence. Top-k DP
ranks paths locally; estimated legs are marked individually. Even an
all-snapshot candidate remains `unverified`. Every new candidate quote needs an
explicit `snapshot` or `estimate` source; legacy station-only optimizer inputs
remain supported separately. Fees assume independent leg payments.

`route-provider.ts` supplies a station-query adapter contract. A local `.mjs`
adapter can export a default `{id, reuseAllowed, async quote(request)}` and be
loaded with `--provider <path> --max-calls <integer>`. Each invocation must perform
at most one external route request; retries must pass through the session again.
The request contains place IDs, station endpoints, exact timezone-normalized
`departureAt` and options. Return a crawler `RouteResult` or
`{status:'unknown',reason:'...'}`. Native TypeScript support requires Node 22.20+.
No live provider adapter is enabled in these frontend CLIs by default. The
repository's Python crawler is a separate component and is not invoked here.

Only when an explicit provider is supplied is the first selected candidate
per dataset refreshed sequentially, adding
each place's stay before requesting the next leg. Identical station/time/options
requests share in-flight work; successful results are temporarily cached only
with explicit `reuseAllowed:true` (default TTL five minutes). No persistent cache
is written. Unknown/error results are never cached, and every provider invocation
consumes the same session budget, including across both datasets. Changed stay
times propagate to later query times; unchanged conditions reuse valid quotes.
Candidate ranking never performs provider calls. Schedule times use UTC ISO
strings and remain station-only references: opening hours, access walks, pass
fares and timetable capability are unverified. `--start-at` accepts an explicit
timezone-qualified starting datetime.
Refreshed totals are reported separately and do not imply the original candidate
is still optimal; the order is not ranked again during the refresh pass.
