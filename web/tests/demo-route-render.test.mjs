import assert from 'node:assert/strict';
import test from 'node:test';
import { renderRouteDay } from '../src/demo-route-render.ts';

const station = (name, lat = 35) => ({ name, location: { lat, lng: 139 } });
const places = {
  a: { id: 'a', name: '첫 관광지', station: '경로 계산 후 확인' },
  b: { id: 'b', name: '다음 관광지', station: '경로 계산 후 확인' },
};
const options = { live: true, dayIndex: 0, date: '2026-11-10', place: (id) => places[id] };
function day(overrides = {}) {
  return { order: ['a', 'b'], minutes: 23, fare: 300, startTime: '23:50',
    arrivals: { a: '2026-11-10T23:50:00+09:00', b: '2026-11-11T00:13:00+09:00' },
    stations: { a: station('出発駅'), b: station('到着駅', 36) },
    legs: [{ from: 'a', to: 'b', minutes: 23, fare: 300, line: 'ignored summary', route: [
      { fromStation: station('出発駅'), toStation: station('乗換A'), line: '地下鉄の名前' },
      { fromStation: station('乗換A'), toStation: station('乗換B'), line: '도보' },
      { fromStation: station('乗換B'), toStation: station('到着駅'), line: 'バスの名前' },
    ] }], ...overrides };
}

test('ordered station steps are visible, walking is explicit, modes and transfers are not guessed', () => {
  const output = renderRouteDay(day(), options);
  assert.ok(output.indexOf('地下鉄の名前') < output.indexOf('도보</strong>'));
  assert.ok(output.indexOf('도보</strong>') < output.indexOf('バスの名前'));
  assert.equal((output.match(/class="route-step"/g) ?? []).length, 3);
  assert.equal((output.match(/class="route-mode walking"/g) ?? []).length, 1);
  assert.equal((output.match(/class="route-mode"/g) ?? []).length, 2);
  assert.doesNotMatch(output, /<details|환승 \d|지하철<|버스</);
  assert.match(output, /관광지↔역 이동과 관광지 체류 시간은 포함되지 않아요/);
  assert.equal((output.match(/장소↔역 이동 정보 미제공/g) ?? []).length, 2);
  assert.match(output, /이동 기준 시각 \+1일 00:13/);
  assert.match(output, /누적 역 간 이동 <\/span>\+23분/);
  assert.match(output, /23분 <span>· ¥300/);
  const accessRows = [...output.matchAll(/<p class="route-access">(.*?)<\/p>/g)].map((match) => match[1]);
  for (const row of accessRows) assert.doesNotMatch(row, /도보|\d+분|¥/);
});

test('empty routes need station identity before claiming a shared station and never claim no movement', () => {
  const base = day();
  base.legs = [{ from: 'a', to: 'b', minutes: 0, fare: 0, line: '동일 역', route: [] }];
  base.minutes = 0; base.fare = 0;
  let output = renderRouteDay(base, options);
  assert.match(output, /상세 이동 경로가 제공되지 않았어요/);
  assert.doesNotMatch(output, /같은 이용 역|이동 없음|동일 역/);
  base.stations = { a: station('같은 이름'), b: station('같은 이름', 36) };
  assert.doesNotMatch(renderRouteDay(base, options), /같은 이용 역/);
  base.stations.b = station('같은 이름');
  output = renderRouteDay(base, options);
  assert.match(output, /같은 이용 역/);
  assert.match(output, /장소 사이 실제 이동은 별도로 확인/);
  assert.equal((output.match(/장소↔역 이동 정보 미제공/g) ?? []).length, 2);
  assert.doesNotMatch(output, /이동 없음/);
});

test('missing station, steps and individual arrival entries degrade without made-up clocks', () => {
  const base = day({ stations: undefined, arrivals: { a: day().arrivals.a } });
  base.legs[0].route = undefined;
  const output = renderRouteDay(base, options);
  assert.match(output, /역 정보 미제공/);
  assert.match(output, /상세 이동 경로가 제공되지 않았어요/);
  assert.doesNotMatch(output, /경로 계산 후 확인|00:13|undefined|ignored summary/);
});

test('shared-station nonzero provider values remain visible with a verification notice', () => {
  const base = day({ stations: { a: station('같은 역'), b: station('같은 역') } });
  base.legs[0].route = [];
  const output = renderRouteDay(base, options);
  assert.match(output, /역 간 이동 · 확인 필요/);
  assert.match(output, /같은 역으로 조회됐지만 이동 시간·요금이 있어 경로 확인이 필요/);
  assert.match(output, /23분 <span>· ¥300/);
});

test('several legs accumulate travel offsets and retain both station access gaps per journey', () => {
  const thirdId = 'c"<unsafe>';
  const base = day({ order: ['a', 'b', thirdId], minutes: 30, fare: 500 });
  base.legs.push({ from: 'b', to: thirdId, minutes: 7, fare: 200, line: 'second line', route: [
    { fromStation: station('到着駅', 36), toStation: station('最後の駅', 37), line: 'second line' },
  ] });
  const output = renderRouteDay(base, { ...options, place: (id) => places[id] ?? { id, name: '세 번째 관광지', station: '最後の駅' } });
  assert.match(output, /누적 역 간 이동 <\/span>\+30분/);
  assert.equal((output.match(/class="route-journey"/g) ?? []).length, 2);
  assert.equal((output.match(/장소↔역 이동 정보 미제공/g) ?? []).length, 4);
  assert.ok(output.indexOf('バスの名前') < output.indexOf('second line'));
  assert.match(output, /data-id="c&quot;&lt;unsafe&gt;"/);
  assert.doesNotMatch(output, /data-id="c"<unsafe>/);
});

test('single-place and example results stay explicit about missing journeys and provenance', () => {
  const single = renderRouteDay(day({ order: ['a'], legs: [], minutes: 0, fare: 0 }), options);
  assert.match(single, /1곳이라 관광지 사이 이동 구간이 없어요/);
  assert.doesNotMatch(single, /class="route-journey"|class="route-access"/);
  const base = day(); base.legs[0].route = undefined; base.legs[0].line = '교통편 예시';
  const example = renderRouteDay(base, { ...options, live: false });
  assert.match(example, /교통비 예시/);
  assert.match(example, /역 간 이동 · 예시/);
  assert.match(example, /교통편 예시/);
  assert.doesNotMatch(example, /23:50|00:13|이동 기준 시각/);
});

test('all supplied labels and focus identifiers are escaped and step lists stay visible for long routes', () => {
  const unsafe = '<img src=x onerror=alert(1)> & "';
  const base = day();
  base.stations.a.name = unsafe;
  base.legs[0].route = Array.from({ length: 5 }, () => ({ fromStation: station(unsafe), toStation: station(unsafe), line: unsafe }));
  const output = renderRouteDay(base, { ...options, selected: 'a', place: (id) => ({ ...places[id], name: unsafe }) });
  assert.doesNotMatch(output, /<img|<details/);
  assert.match(output, /&lt;img/);
  assert.match(output, /&amp; &quot;/);
  assert.match(output, /class="route-stop selected" data-action="focus" data-id="a"/);
  assert.equal((output.match(/class="route-step"/g) ?? []).length, 5);
});
