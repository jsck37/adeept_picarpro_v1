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
  // v1.6.0: крайние лево/право — руль без движения
  assert.strictEqual(C.vectorToDir(-50, 0), 'steer_left');
  assert.strictEqual(C.vectorToDir(50, 0), 'steer_right');
  assert.strictEqual(C.vectorToDir(40, -40), 'forward_right');
  assert.strictEqual(C.vectorToDir(-40, -40), 'forward_left');
  assert.strictEqual(C.vectorToDir(-40, 40), 'backward_left');
  assert.strictEqual(C.vectorToDir(40, 40), 'backward_right');
  assert.strictEqual(C.vectorToDir(3, 3), 'stop');
});

test('combineKeys: A/D без W/S — руль без движения (v1.6.0)', () => {
  assert.strictEqual(C.combineKeys({left: true}), 'steer_left');
  assert.strictEqual(C.combineKeys({right: true}), 'steer_right');
  // с W/S — обычные диагонали
  assert.strictEqual(C.combineKeys({up: true, left: true}), 'forward_left');
  assert.strictEqual(C.combineKeys({down: true, right: true}), 'backward_right');
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

test('keyAction: регистр; стрелки отданы камере (v1.6.0)', () => {
  assert.strictEqual(C.keyAction('W'), 'up');
  assert.strictEqual(C.keyAction('a'), 'left');
  assert.strictEqual(C.keyAction('Q'), 'spin_left');
  assert.strictEqual(C.keyAction('x'), null);
  // стрелки больше НЕ колёса — ими управляет камера (app.js camKeys)
  assert.strictEqual(C.keyAction('ArrowLeft'), null);
  assert.strictEqual(C.keyAction('ArrowUp'), null);
  assert.strictEqual(C.keyAction('ArrowDown'), null);
  assert.strictEqual(C.keyAction('ArrowRight'), null);
});

test('camKeyDelta: инверсия убрана, спека владельца (v1.6.1)', () => {
  // вверх = tilt +5 (камера поднимается), вниз = tilt -5
  assert.deepStrictEqual(C.camKeyDelta('ArrowUp'), [0, 5]);
  assert.deepStrictEqual(C.camKeyDelta('ArrowDown'), [0, -5]);
  assert.deepStrictEqual(C.camKeyDelta('i'), [0, 5]);
  assert.deepStrictEqual(C.camKeyDelta('k'), [0, -5]);
  // вправо = pan -5 (панорама вправо), влево = pan +5
  assert.deepStrictEqual(C.camKeyDelta('ArrowRight'), [-5, 0]);
  assert.deepStrictEqual(C.camKeyDelta('ArrowLeft'), [5, 0]);
  assert.deepStrictEqual(C.camKeyDelta('l'), [-5, 0]);
  assert.deepStrictEqual(C.camKeyDelta('j'), [5, 0]);
  // не камера
  assert.strictEqual(C.camKeyDelta('w'), null);
  assert.strictEqual(C.camKeyDelta('x'), null);
});

test('stickToCam: драг совпадает со стрелками (v1.6.1)', () => {
  // драг вверх (dy<0) — камера поднимается (tilt > 90)
  const up = C.stickToCam(0, -30, 60);
  assert.strictEqual(up.pan, 90);
  assert.ok(up.tilt > 90, `up.tilt=${up.tilt} должен быть > 90`);
  // драг вниз — опускается
  const down = C.stickToCam(0, 30, 60);
  assert.ok(down.tilt < 90, `down.tilt=${down.tilt} должен быть < 90`);
  // драг вправо — панорама вправо (pan < 90)
  const right = C.stickToCam(30, 0, 60);
  assert.ok(right.pan < 90, `right.pan=${right.pan} должен быть < 90`);
  // драг влево — влево (pan > 90)
  const left = C.stickToCam(-30, 0, 60);
  assert.ok(left.pan > 90, `left.pan=${left.pan} должен быть > 90`);
  // центр — нейтраль
  assert.deepStrictEqual(C.stickToCam(0, 0, 60), { pan: 90, tilt: 90 });
  assert.deepStrictEqual(C.stickToCam(10, -10, 0), { pan: 90, tilt: 90 });
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
