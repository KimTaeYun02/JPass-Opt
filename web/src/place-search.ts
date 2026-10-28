import { importLibrary } from '@googlemaps/js-api-loader';
import type { Place } from './trip-model';
import { DEFAULT_VISIT_MINUTES } from './trip-model';

// Project-wide daily quotas also protect calls after a page reload.
let autocompleteCalls = 0;
let detailsCalls = 0;

export function initPlaceSearch(input: HTMLInputElement, results: HTMLElement, status: HTMLElement, onSelect: (place: Place) => void, onInput: () => void): { reset: () => void } {
  let version = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let session: google.maps.places.AutocompleteSessionToken | undefined;
  let predictions: google.maps.places.PlacePrediction[] = [];
  let busy = false;
  const announce = (message: string): void => { status.textContent = message; };
  const clear = (): void => { results.replaceChildren(); predictions = []; };

  async function search(query: string, current: number): Promise<void> {
    if (!import.meta.env.VITE_GOOGLE_MAPS_API_KEY) { announce('검색 API 키가 없어 장소 검색을 사용할 수 없어요.'); return; }
    if (autocompleteCalls >= 20) { announce('테스트용 검색 횟수에 도달했어요. 잠시 후 다시 이용해 주세요.'); return; }
    try {
      const { AutocompleteSuggestion, AutocompleteSessionToken } = await importLibrary('places');
      if (current !== version) return;
      session ??= new AutocompleteSessionToken();
      autocompleteCalls += 1;
      const { suggestions } = await AutocompleteSuggestion.fetchAutocompleteSuggestions({
        input: query, includedRegionCodes: ['jp'], language: 'ko', region: 'jp', sessionToken: session,
      });
      if (current !== version) return;
      predictions = suggestions.flatMap((suggestion) => suggestion.placePrediction ? [suggestion.placePrediction] : []);
      results.replaceChildren();
      predictions.forEach((prediction, index) => {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.prediction = String(index);
        const title = document.createElement('strong');
        title.textContent = prediction.mainText?.toString() || prediction.text.toString();
        const address = document.createElement('span');
        address.textContent = prediction.secondaryText?.toString() || '주소를 확인하려면 선택해 주세요.';
        button.append(title, address);
        item.append(button);
        results.append(item);
      });
      announce(predictions.length ? '주소를 확인하고 검색 결과를 선택해 주세요. · Google Maps 제공' : '일치하는 장소가 없어요. 일본어 또는 다른 이름으로 검색해 보세요.');
    } catch (error) {
      if (current !== version) return;
      console.warn('Place search failed:', String(error).replace(/AIza[\w-]+/g, '[redacted]'));
      clear();
      announce('장소 검색을 사용할 수 없어요. 네트워크, API 권한 또는 일일 테스트 한도를 확인해 주세요.');
    }
  }

  function queue(): void {
    if (busy) return;
    version += 1;
    clearTimeout(timer);
    clear();
    onInput();
    const query = input.value.trim();
    if (query.length < 2) { announce('장소명을 두 글자 이상 입력해 주세요.'); return; }
    const current = version;
    announce('검색 중…');
    timer = setTimeout(() => void search(query, current), 450);
  }
  input.addEventListener('input', (event) => {
    if (!(event as InputEvent).isComposing) queue();
  });
  input.addEventListener('compositionend', queue);
  input.addEventListener('compositionstart', () => { version += 1; clearTimeout(timer); clear(); onInput(); });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') event.preventDefault();
    if (event.key === 'Escape') { version += 1; clearTimeout(timer); clear(); }
  });
  results.addEventListener('click', async (event) => {
    const button = (event.target as HTMLElement).closest<HTMLElement>('button[data-prediction]');
    const prediction = button ? predictions[Number(button.dataset.prediction)] : undefined;
    if (!prediction || busy) return;
    if (detailsCalls >= 10) { announce('테스트용 장소 확인 한도에 도달했어요.'); return; }
    busy = true;
    input.disabled = true;
    const current = ++version;
    clearTimeout(timer);
    clear();
    announce('장소의 주소와 위치를 확인하는 중…');
    try {
      const selected = prediction.toPlace();
      detailsCalls += 1;
      await selected.fetchFields({ fields: ['id', 'formattedAddress', 'location'] });
      if (current !== version) return;
      if (!selected.location || !selected.id) throw new Error('Missing location');
      const name = prediction.mainText?.toString() || prediction.text.toString();
      input.value = name;
      onSelect({
        id: crypto.randomUUID(), placeId: selected.id, name,
        address: selected.formattedAddress || prediction.secondaryText?.toString() || '주소 정보 없음',
        location: selected.location.toJSON(), source: 'google', required: true, duration: DEFAULT_VISIT_MINUTES,
      });
      announce('Google Maps 검색 결과를 선택했어요. 아래에서 배치를 정해 주세요.');
    } catch (error) {
      console.warn('Place selection failed:', String(error).replace(/AIza[\w-]+/g, '[redacted]'));
      if (current === version) announce('위치 확인에 실패했어요. 다시 검색하거나 일일 테스트 한도를 확인해 주세요.');
    } finally {
      busy = false;
      input.disabled = false;
      session = undefined;
    }
  });
  return { reset: () => {
    version += 1;
    clearTimeout(timer);
    session = undefined;
    clear();
    input.value = '';
    announce('장소명을 입력하고 검색 결과를 선택해 주세요.');
  } };
}
