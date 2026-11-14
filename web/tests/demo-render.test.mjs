import assert from 'node:assert/strict';
import test from 'node:test';
import { renderDemoMarkup } from '../src/demo-render.ts';

test('repeated UI renders never disconnect the SDK slot or any map ancestor', () => {
  const mutations = [];
  class Node {
    constructor(selector, children = []) { this.selector = selector; this.id = selector.startsWith('#') ? selector.slice(1) : ''; this.children = []; children.forEach((child) => this.append(child)); }
    querySelector(selector) { for (const child of this.children) { if (child.selector === selector) return child; const found = child.querySelector(selector); if (found) return found; } return null; }
    append(child) { if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1); child.parent = this; this.children.push(child); }
    remove() { mutations.push(this); this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
    replaceWith(next) { mutations.push(this); const parent = this.parent; const index = parent.children.indexOf(this); if (next.parent) next.parent.children.splice(next.parent.children.indexOf(next), 1); parent.children[index] = next; next.parent = parent; this.parent = null; }
  }
  const host = new Node('#live-map', [new Node('#sdk-marker')]);
  const slot = new Node('#demo-map-slot', [host]);
  const oldOverlay = new Node('.map-place-card');
  const area = new Node('.map-area', [slot, oldOverlay]);
  const workspace = new Node('.workspace', [area, new Node('.planner')]);
  const app = new Node('#app', [new Node('.site-header'), workspace]);
  app.ownerDocument = { createElement: () => ({ content: new Node('fragment', [new Node('.site-header'), new Node('.map-area', [new Node('#demo-map-slot'), new Node('.map-place-card')]), new Node('.planner')]) }) };
  Object.defineProperty(app, 'innerHTML', { set() { assert.fail('must not replace the app after mounting'); } });
  for (let i = 0; i < 4; i++) {
    renderDemoMarkup(app, '<parsed by template mock>');
    assert.equal(app.querySelector('.workspace'), workspace);
    assert.equal(app.querySelector('.map-area'), area);
    assert.equal(app.querySelector('#demo-map-slot'), slot);
    assert.equal(slot.children[0], host);
    assert.equal(host.children.length, 1);
    assert.equal(area.children.length, 2, 'overlays are replaced without accumulating');
    assert.equal(area.querySelector('#demo-map-slot'), slot);
  }
  assert.equal(mutations.some((node) => [app, workspace, area, slot, host].includes(node)), false);
  assert.equal(oldOverlay.parent, null);
});

test('initial render mounts markup before a map slot exists', () => {
  const app = { querySelector: () => null, innerHTML: '' };
  renderDemoMarkup(app, '<main>initial</main>');
  assert.equal(app.innerHTML, '<main>initial</main>');
});
