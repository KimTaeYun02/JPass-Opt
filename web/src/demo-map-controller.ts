export type PlaceMark = { id: string; name: string; lat: number; lng: number; order: number | null; state: 'routed' | 'pending' | 'recommended' };
type MapPoint = { lat: number; lng: number };
export type MapRouteSegment = { mode: 'transit' | 'walking'; from: MapPoint; to: MapPoint; path?: MapPoint[] };
export type DemoMapView = { activeDayId: string; places: PlaceMark[]; selectedId?: string; showRoute: boolean; routePath?: MapPoint[]; routeSegments?: MapRouteSegment[] };
export type MapPhase = 'loading' | 'ready' | 'fallback';
export class DemoMapLoadError extends Error {}
export type MapSdk = { Map: typeof google.maps.Map; AdvancedMarkerElement: typeof google.maps.marker.AdvancedMarkerElement; Polyline: typeof google.maps.Polyline; LatLngBounds: typeof google.maps.LatLngBounds };
type Options = { host: HTMLElement; load: () => Promise<MapSdk>; onSelect: (id: string) => void; onStatus: (phase: MapPhase, message: string) => void; createElement?: (tag: string) => HTMLElement };

export function mapRoutePaths(view: DemoMapView): { mode: MapRouteSegment['mode']; path: MapPoint[] }[] {
  if (!view.showRoute) return [];
  if (view.routeSegments !== undefined) return view.routeSegments.map(({ mode, from, to, path }) => ({ mode, path: path ?? [from, to] })).filter(({ path }) => path.length > 1 && path.some((p) => p.lat !== path[0]!.lat || p.lng !== path[0]!.lng));
  const path = view.routePath ?? view.places.filter((place) => place.state === 'routed' && place.order !== null).sort((a, b) => a.order! - b.order!).map(({ lat, lng }) => ({ lat, lng }));
  return path.length > 1 ? [{ mode: 'transit', path }] : [];
}

function lineStyle(mode: MapRouteSegment['mode']): google.maps.PolylineOptions {
  return mode === 'walking' ? {
    strokeColor: '#258258', strokeOpacity: 0, strokeWeight: 4, zIndex: 2,
    icons: [{ icon: { path: 'M 0,-1 0,1', strokeColor: '#258258', strokeOpacity: 1, strokeWeight: 3, scale: 2 }, offset: '0', repeat: '14px' }],
  } : { strokeColor: '#3267d6', strokeOpacity: 0.9, strokeWeight: 4, zIndex: 1, icons: [] };
}

export function createDemoMapController(options: Options) {
  let latest: DemoMapView | undefined;
  let sdk: MapSdk | undefined;
  let map: google.maps.Map | undefined;
  const lines: { line: google.maps.Polyline; mode: MapRouteSegment['mode']; pathKey: string }[] = [];
  let started = false;
  let disposed = false;
  let failed = false;
  let phase: MapPhase = 'loading';
  let cameraKey = '';
  let selectedId: string | undefined;
  const create = options.createElement ?? ((tag: string) => document.createElement(tag));
  const markers = new Map<string, { marker: google.maps.marker.AdvancedMarkerElement; node: HTMLElement; badge: HTMLElement; name: HTMLElement; click: () => void }>();
  function clearMarkers() {
    markers.forEach(({ marker, click }) => { marker.removeEventListener('gmp-click', click); marker.map = null; });
    markers.clear();
  }
  function clearLines() {
    lines.forEach(({ line }) => line.setMap(null));
    lines.length = 0;
  }
  function fail(message: string) {
    if (disposed || failed) return;
    failed = true;
    phase = 'fallback';
    clearMarkers();
    clearLines();
    options.onStatus('fallback', message);
  }
  function paint() {
    if (!map || !sdk || !latest || disposed || failed) return;
    const visible = new Set(latest.places.map((place) => place.id));
    markers.forEach(({ marker, click }, id) => {
      if (!visible.has(id)) { marker.removeEventListener('gmp-click', click); marker.map = null; markers.delete(id); }
    });
    latest.places.forEach((place) => {
      let entry = markers.get(place.id);
      if (!entry) {
        const node = create('div');
        const badge = create('span'); badge.className = 'live-pin-badge';
        const name = create('span'); name.className = 'live-pin-name';
        node.replaceChildren(badge, name);
        const marker = new sdk!.AdvancedMarkerElement({ map, position: { lat: place.lat, lng: place.lng }, gmpClickable: true, content: node });
        const click = () => { options.onSelect(place.id); if (!disposed && !failed) markers.get(place.id)?.marker.focus(); };
        marker.addEventListener('gmp-click', click);
        entry = { marker, node, badge, name, click };
        markers.set(place.id, entry);
      }
      const status = place.state === 'pending' ? '추가됨 · 재계산 전' : place.state === 'recommended' ? '추천 후보' : place.order === null ? '방문 장소' : `${place.order}번째 방문 장소`;
      entry.marker.position = { lat: place.lat, lng: place.lng };
      entry.marker.title = `${place.name} · ${status}`;
      entry.marker.zIndex = latest!.selectedId === place.id ? 100 : place.state === 'pending' ? 30 : 10;
      entry.node.className = `live-pin ${place.state} ${latest!.selectedId === place.id ? 'selected' : ''}`;
      entry.badge.textContent = place.order === null ? '+' : String(place.order);
      entry.name.textContent = place.name;
    });
    const paths = mapRoutePaths(latest);
    paths.forEach(({ mode, path }, index) => {
      if (!lines[index]) lines.push({ line: new sdk!.Polyline({ map, clickable: false, ...lineStyle(mode) }), mode, pathKey: '' });
      const entry = lines[index]!;
      if (entry.mode !== mode) { entry.line.setOptions(lineStyle(mode)); entry.mode = mode; }
      const pathKey = JSON.stringify(path);
      if (entry.pathKey !== pathKey) { entry.line.setPath(path); entry.pathKey = pathKey; }
      entry.line.setVisible(path.length > 1);
    });
    lines.slice(paths.length).forEach((entry) => { entry.line.setPath([]); entry.pathKey = '[]'; entry.line.setVisible(false); });
    const path = paths.flatMap((entry) => entry.path);
    const nextCameraKey = `${latest.activeDayId}:${[...visible].sort().join(',')}:${JSON.stringify(path)}`;
    if (nextCameraKey !== cameraKey) {
      cameraKey = nextCameraKey;
      if (latest.places.length === 1 && path.length === 0) {
        map.setCenter({ lat: latest.places[0]!.lat, lng: latest.places[0]!.lng }); map.setZoom(14);
      } else if (latest.places.length > 1 || path.length > 1) {
        const bounds = new sdk.LatLngBounds();
        latest.places.forEach((place) => bounds.extend({ lat: place.lat, lng: place.lng }));
        path.forEach((point) => bounds.extend(point));
        map.fitBounds(bounds, { top: 110, bottom: 100, left: 50, right: 50 });
      }
    }
    if (latest.selectedId && latest.selectedId !== selectedId) {
      const place = latest.places.find((item) => item.id === latest!.selectedId);
      if (place) map.panTo({ lat: place.lat, lng: place.lng });
    }
    selectedId = latest.selectedId;
  }
  function update(view: DemoMapView) {
    if (disposed || failed) return;
    latest = structuredClone(view);
    if (map) { paint(); return; }
    if (started) return;
    started = true;
    options.onStatus('loading', 'Google 지도를 불러오는 중입니다.');
    void options.load().then((loaded) => {
      if (disposed || failed) return;
      sdk = loaded;
      map = new sdk.Map(options.host, { center: { lat: 35.006, lng: 135.755 }, zoom: 12, mapId: 'DEMO_MAP_ID', disableDefaultUI: true, zoomControl: true, keyboardShortcuts: true, clickableIcons: false, gestureHandling: 'greedy' });
      lines.push({ line: new sdk.Polyline({ map, clickable: false, ...lineStyle('transit') }), mode: 'transit', pathKey: '' });
      paint();
      phase = 'ready';
      options.onStatus('ready', 'Google 지도 · 방문 순서 연결선');
    }).catch((error: unknown) => fail(error instanceof DemoMapLoadError ? error.message : 'Google 지도 SDK 로딩에 실패했어요. 개략도로 표시합니다. 네트워크 연결과 키 권한을 확인해 주세요.'));
  }
  return { update, fail, status: () => phase, attach(parent: HTMLElement) { if (options.host.parentElement !== parent) parent.append(options.host); }, destroy() { disposed = true; clearMarkers(); clearLines(); } };
}
