import './style.css';
import { importLibrary, setOptions } from '@googlemaps/js-api-loader';
import { MAP_STYLE } from './mapStyle';
import { initTripInput } from './trip-input';
import type { Place } from './trip-model';

// The prototype uses a Kansai trip. This map does not represent a computed route.
const INITIAL_CENTER: google.maps.LatLngLiteral = { lat: 34.88, lng: 135.6 };
const INITIAL_ZOOM = 9;

const mapElement = document.getElementById('map');
const statusElement = document.getElementById('map-status');
const apiKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined;
if (apiKey) setOptions({ key: apiKey, v: 'weekly', language: 'ko', region: 'JP' });

function showStatus(message: string): void {
  if (!statusElement) return;
  statusElement.textContent = message;
  statusElement.hidden = false;
}

function hideStatus(): void {
  if (statusElement) statusElement.hidden = true;
}

// Google calls this global hook when the key is invalid or the referrer is not allowed.
(window as unknown as { gm_authFailure: () => void }).gm_authFailure = () => {
  showStatus('지도 API 인증에 실패했습니다. 키 제한(허용 주소, 허용 API)을 확인해 주세요.');
};

async function initMap(): Promise<void> {
  if (!apiKey) {
    showStatus('VITE_GOOGLE_MAPS_API_KEY가 없습니다. web/.env.local을 확인해 주세요.');
    return;
  }
  if (!mapElement) return;


  try {
    const { Map, Circle, InfoWindow } = await importLibrary('maps');
    const map = new Map(mapElement, {
      center: INITIAL_CENTER,
      zoom: INITIAL_ZOOM,
      styles: MAP_STYLE,
      disableDefaultUI: true,
      zoomControl: true,
      zoomControlOptions: { position: google.maps.ControlPosition.RIGHT_BOTTOM },
      clickableIcons: false,
      gestureHandling: 'greedy',
    });
    map.addListener('tilesloaded', hideStatus);
    const point = new Circle({ map, radius: 35, fillColor: '#315d48', fillOpacity: 0.8, strokeColor: '#fff', strokeWeight: 2, clickable: false });
    const info = new InfoWindow();
    window.addEventListener('trip-place-selected', (event) => {
      const place = (event as CustomEvent<Place>).detail;
      if (!place.location) return;
      map.setCenter(place.location);
      map.setZoom(15);
      point.setCenter(place.location);
      const label = document.createElement('div');
      const name = document.createElement('strong'); name.textContent = place.name;
      const address = document.createElement('p'); address.textContent = place.address;
      label.append(name, address);
      info.setContent(label); info.setPosition(place.location); info.open({ map, shouldFocus: false });
    });
  } catch (error) {
    console.error(error);
    showStatus('지도를 불러오지 못했습니다. 네트워크 상태를 확인해 주세요.');
  }
}

const tripPanel = document.querySelector<HTMLElement>('.trip-panel');
if (tripPanel) initTripInput(tripPanel);
void initMap();
