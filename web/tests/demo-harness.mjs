import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import * as model from '../src/demo-model.ts';
import { createTravelApi, formatJapanTime } from '../src/demo-api.ts';

// In-memory unit harness; does not open or connect to a browser or web page.
const require = createRequire(new URL('../package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../src/demo.ts', import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '');
const script = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
export function harness({ mode = 'example', client = createTravelApi() } = {}) {
  const callbacks = [];
  const listeners = new Map();
  const dialogListeners = new Map();
  const panel = { scrollTop: 0, scrollTo() {} };
  const app = { innerHTML: '', querySelector: (selector) => selector === '.panel-content' ? panel : null, querySelectorAll: () => [], addEventListener: (name, callback) => listeners.set(name, callback) };
  const dialog = { open: false, querySelector: () => null, addEventListener: (name, callback) => dialogListeners.set(name, callback), showModal() { this.open = true; }, close() { this.open = false; dialogListeners.get('close')?.(); } };
  const search = { value: '', addEventListener(name, callback) { if (name === 'input') this.onInput = callback; else if (name === 'keydown') this.onKey = callback; }, focus() {} };
  const searchResults = { innerHTML: '' }, searchSelection = { innerHTML: '' }, searchConfirm = { disabled: true, textContent: '' };
  const toast = { textContent: '', classList: { add() {}, remove() {} } };
  const elements = { '#app': app, '#place-dialog': dialog, '#demo-search': search, '#search-results': searchResults, '#search-selection': searchSelection, '#search-confirm': searchConfirm, '#toast': toast };
  const context = vm.createContext({ ...model, AbortController, createTravelApi: () => client, formatJapanTime, URLSearchParams, renderDemoMarkup: (element, markup) => {
    // Validate the real render() output against the persistent renderer's shape contract.
    for (const pattern of [/class="site-header"/g, /class="planner"/g, /class="map-area"/g, /id="demo-map-slot"/g]) assert.equal([...markup.matchAll(pattern)].length, 1);
    element.innerHTML = markup;
  }, structuredClone, document: { activeElement: null, documentElement: { style: { setProperty() {} } }, querySelector: (selector) => elements[selector], getElementById: () => null }, window: { location: { search: mode === 'example' ? '?mode=example' : '' }, innerWidth: 1366, addEventListener() {} }, setTimeout: (callback, delay) => { if (delay === 650) callbacks.push(callback); return 1; }, clearTimeout() {} });
  vm.runInContext(script, context);
  return {
    markup: () => app.innerHTML,
    state: () => JSON.parse(vm.runInContext('JSON.stringify({draft,view,result,operationError,loading,messages,map:mapView(),initialPool,nearbyPool})', context)),
    input(id, value) { listeners.get('input')({ target: { id, value } }); },
    click(action, extra = {}) { listeners.get('click')({ target: { closest: () => ({ dataset: { action, ...extra }, hasAttribute: () => false }) } }); },
    change(id, value) { listeners.get('change')({ target: { id, value } }); },
    async settle() { for (const callback of callbacks.splice(0)) callback(); await new Promise(setImmediate); },
    pending: () => callbacks.length,
    message: () => toast.textContent,
    dialogClick(dataset) { dialogListeners.get('click')({ target: { closest: () => ({ dataset, disabled: false }) } }); },
    filter(query) { search.value = query; search.onInput(); },
    searchKey(key) { search.onKey({ key, preventDefault() {} }); },
    closeDialog() { dialog.close(); },
    dialogOpen: () => dialog.open,
    searchMarkup: () => searchResults.innerHTML,
    selectionMarkup: () => searchSelection.innerHTML,
    confirm: searchConfirm,
  };
}
