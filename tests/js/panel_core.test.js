// node --test tests/js/panel_core.test.js
const test = require('node:test');
const assert = require('node:assert');
const C = require('../../picarpro/web/static/panel-core.js');

test('mergeStatus: полный заменяет, дельта дополняет', () => {
  const full = C.mergeStatus(null, {a: 1, b: 2}, false);
  assert.deepStrictEqual(full, {a: 1, b: 2});
  const merged = C.mergeStatus(full, {b: 3, c: 4}, true);
  assert.deepStrictEqual(merged, {a: 1, b: 3, c: 4});
  const replaced = C.mergeStatus(merged, {z: 9}, false);
  assert.deepStrictEqual(replaced, {z: 9});
});

test('reconnectDelay: растёт и ограничена 10 с (с дрожью ±30%)', () => {
  assert.strictEqual(C.reconnectDelay(0, 0.5), 500);
  assert.strictEqual(C.reconnectDelay(1, 0.5), 1000);
  assert.strictEqual(C.reconnectDelay(2, 0.5), 2000);
  assert.ok(C.reconnectDelay(20, 1) <= 13000);
  assert.ok(C.reconnectDelay(20, 0) >= 7000);
});

test('vectorToDir: 8 направлений и мёртвая зона', () => {
  assert.strictEqual(C.vectorToDir(0, -50), 'forward');
  assert.strictEqual(C.vectorToDir(0, 50), 'backward');
  assert.strictEqual(C.vectorToDir(-50, 0), 'left');
  assert.strictEqual(C.vectorToDir(50, 0), 'right');
  assert.strictEqual(C.vectorToDir(40, -40), 'forward_right');
  assert.strictEqual(C.vectorToDir(-40, -40), 'forward_left');
  assert.strictEqual(C.vectorToDir(-40, 40), 'backward_left');
  assert.strictEqual(C.vectorToDir(40, 40), 'backward_right');
  assert.strictEqual(C.vectorToDir(3, 3), 'stop');
});

test('combineKeys: диагонали, отмена, отпускание одной из двух', () => {
  assert.strictEqual(C.combineKeys({}), 'stop');
  assert.strictEqual(C.combineKeys({up: true}), 'forward');
  assert.strictEqual(C.combineKeys({up: true, right: true}), 'forward_right');
  assert.strictEqual(C.combineKeys({down: true, left: true}), 'backward_left');
  assert.strictEqual(C.combineKeys({up: true, down: true}), 'stop');
  assert.strictEqual(C.combineKeys({left: true, right: true}), 'stop');
  assert.strictEqual(C.combineKeys({spin_left: true}), 'spin_left');
  assert.strictEqual(C.combineKeys({spin_left: true, spin_right: true}), 'stop');
  // W+D зажаты, D отпустили -> едем вперёд, а не стоп
  const held = {up: true, right: true};
  delete held.right;
  assert.strictEqual(C.combineKeys(held), 'forward');
  // вперёд важнее разворота
  assert.strictEqual(C.combineKeys({up: true, spin_left: true}), 'forward');
});

test('keyAction: регистр и стрелки', () => {
  assert.strictEqual(C.keyAction('W'), 'up');
  assert.strictEqual(C.keyAction('ArrowLeft'), 'left');
  assert.strictEqual(C.keyAction('Q'), 'spin_left');
  assert.strictEqual(C.keyAction('x'), null);
});

test('driverState', () => {
  assert.strictEqual(C.driverState(null, 'me'), 'free');
  assert.strictEqual(C.driverState('me', 'me'), 'you');
  assert.strictEqual(C.driverState('other', 'me'), 'other');
});

test('makeClientId уникален и безопасен для заголовка', () => {
  const a = C.makeClientId(), b = C.makeClientId();
  assert.notStrictEqual(a, b);
  assert.match(a, /^[a-z0-9]{3,30}$/);
});
