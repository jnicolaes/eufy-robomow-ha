import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.HTMLElement = class {};
globalThis.customElements = {get:() => true};
globalThis.window = {};
const {controls, mapHealth, escapeHTML} = await import('../../custom_components/eufy_robomow/frontend/eufy-mower-card.js');
const now = Date.parse('2026-09-06T12:00:00Z');
const mower = (state, options = {}) => ({state, attributes:{operating_mode:'control', supported_features:7, telemetry_updated_at:new Date(now).toISOString(), ...options}});
test('observe-only and stale status disable physical controls', () => {
  assert.equal(controls(mower('docked', {operating_mode:'observe_only'}), now).start, false);
  assert.equal(controls(mower('docked', {telemetry_updated_at:'2026-09-06T11:58:00Z'}), now).start, false);
  assert.equal(controls(mower('docked'), now).start, true);
  assert.equal(controls(mower('unavailable'), now).start, false);
});
test('pending start cannot repeat, safety pause stays available', () => {
  assert.equal(controls(mower('docked', {command:{state:'pending'}}), now).start, false);
  assert.equal(controls(mower('mowing', {command:{state:'pending'}}), now).pause, true);
  assert.equal(controls(mower('mowing', {supported_features:1}), now).pause, false);
});
test('old image is visibly cached even when image entity is available', () => {
  const image = {state:'2026-09-06T11:55:00Z', attributes:{acquisition_status:'healthy', acquisition_last_success:'2026-09-06T11:55:00Z'}};
  assert.equal(mapHealth(image, true, now).kind, 'warn');
  assert.equal(mapHealth(image, false, now).kind, 'good');
  image.attributes.acquisition_status = 'failed';
  assert.equal(mapHealth(image, false, now).kind, 'warn');
});
test('entity values cannot inject markup', () => {
  assert.equal(escapeHTML('<img onerror="x">'), '&lt;img onerror=&quot;x&quot;&gt;');
});
