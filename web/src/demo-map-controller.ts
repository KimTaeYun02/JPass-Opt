export type PlaceMark = { id: string; name: string; lat: number; lng: number; order: number | null; state: 'routed' | 'pending' | 'recommended' };
export type DemoMapView = { activeDayId: string; places: PlaceMark[]; selectedId?: string; showRoute: boolean; routePath?: { lat: number; lng: number }[] };
export type MapPhase = 'loading' | 'ready' | 'fallback';
export class DemoMapLoadError extends Error {}
export type MapSdk = { Map: typeof google.maps.Map; AdvancedMarkerElement: typeof google.maps.marker.AdvancedMarkerElement; Polyline: typeof google.maps.Polyline; LatLngBounds: typeof google.maps.LatLngBounds };
type Options = { host: HTMLElement; load: () => Promise<MapSdk>; onSelect: (id: string) => void; onStatus: (phase: MapPhase, message: string) => void; createElement?: (tag: string) => HTMLElement };

export function createDemoMapController(options: Options) {
  let latest: DemoMapView | undefined;
  let sdk: MapSdk | undefined;
  let map: google.maps.Map | undefined;
  let line: google.maps.Polyline | undefined;
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
  function fail(message: string) {
    if (disposed || failed) return;
    failed = true;
    phase = 'fallback';
    clearMarkers();
    line?.setMap(null);
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
    const route = latest.showRoute ? latest.places.filter((place) => place.state === 'routed' && place.order !== null).sort((a, b) => a.order! - b.order!) : [];
    const path = latest.showRoute ? latest.routePath ?? route.map(({ lat, lng }) => ({ lat, lng })) : [];
    line!.setPath(path);
    line!.setVisible(path.length > 1);
    const nextCameraKey = `${latest.activeDayId}:${[...visible].sort().join(',')}:${JSON.stringify(path)}`;
    if (nextCameraKey !== cameraKey) {
      cameraKey = nextCameraKey;
      if (latest.places.length === 1 && path.length === 0) {
        map.setCenter({ lat: latest.places[0]!.lat, lng: latest.places[0]!.lng }); map.setZoom(14);
      } else if (latest.places.length > 1) {
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
      line = new sdk.Polyline({ map, strokeColor: '#2458de', strokeOpacity: 0.8, strokeWeight: 4, clickable: false });
      paint();
      phase = 'ready';
      options.onStatus('ready', 'Google 지도 · 방문 순서 연결선');
    }).catch((error: unknown) => fail(error instanceof DemoMapLoadError ? error.message : 'Google 지도 SDK 로딩에 실패했어요. 개략도로 표시합니다. 네트워크 연결과 키 권한을 확인해 주세요.'));
  }
  return { update, fail, status: () => phase, attach(parent: HTMLElement) { if (options.host.parentElement !== parent) parent.append(options.host); }, destroy() { disposed = true; clearMarkers(); line?.setMap(null); } };
}
