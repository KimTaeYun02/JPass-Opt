import { importLibrary, setOptions } from '@googlemaps/js-api-loader';
import { createDemoMapController, DemoMapLoadError } from './demo-map-controller';
import type { MapSdk, MapPhase } from './demo-map-controller';

const apiKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined;
if (apiKey) setOptions({ key: apiKey, v: 'weekly', language: 'ko', region: 'JP' });

export function createGoogleDemoMap(host: HTMLElement, onSelect: (id: string) => void, onStatus: (phase: MapPhase, message: string) => void) {
  const load = async (): Promise<MapSdk> => {
    if (!apiKey) throw new DemoMapLoadError('Google 지도 키가 설정되지 않았어요. VITE_GOOGLE_MAPS_API_KEY를 설정해 주세요. 개략도로 표시합니다.');
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const libraries = Promise.all([importLibrary('maps'), importLibrary('marker'), importLibrary('core')]);
      const [maps, markers, core] = await Promise.race([libraries, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new DemoMapLoadError('Google 지도 로딩 시간이 초과됐어요. 네트워크 연결을 확인해 주세요. 개략도로 표시합니다.')), 20000); })]);
      return { Map: maps.Map, Polyline: maps.Polyline, AdvancedMarkerElement: markers.AdvancedMarkerElement, LatLngBounds: core.LatLngBounds };
    } finally { clearTimeout(timer); }
  };
  const controller = createDemoMapController({ host, load, onSelect, onStatus });
  const authWindow = window as unknown as { gm_authFailure?: () => void };
  const previous = authWindow.gm_authFailure;
  const authFailure = () => controller.fail('Google 지도 인증에 실패했어요. 키의 허용 주소와 Maps JavaScript API 설정을 확인해 주세요.');
  authWindow.gm_authFailure = authFailure;
  import.meta.hot?.dispose(() => { controller.destroy(); if (authWindow.gm_authFailure === authFailure) authWindow.gm_authFailure = previous; });
  return controller;
}
