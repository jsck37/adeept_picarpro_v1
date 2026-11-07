/* PiCar Pro — чистая логика веб-панели (без DOM), чтобы её можно было
 * проверять в node (tests/js/panel_core.test.js). Подключается до app.js. */
(function (root) {
  'use strict';

  // Объединение дельты статуса с накопленным состоянием.
  // isDelta=false — пришёл полный статус, он заменяет всё.
  function mergeStatus(base, data, isDelta) {
    if (!isDelta || !base) return Object.assign({}, data);
    return Object.assign(base, data);
  }

  // Задержка переподключения: экспонента 0.5 с .. 10 с с «дрожью» ±30 %,
  // чтобы телефоны не стучались в Pi одновременно после обрыва Wi-Fi.
  function reconnectDelay(attempt, rnd) {
    var base = Math.min(10000, 500 * Math.pow(2, Math.max(0, attempt)));
    var r = (typeof rnd === 'number') ? rnd : Math.random();
    return Math.round(base * (0.7 + 0.6 * r));
  }

  // Направление по вектору смещения джойстика (dx,dy — пиксели, y вниз).
  // Крайние лево/право — тоже steer_* (руль без движения), как A/D.
  function vectorToDir(dx, dy, deadzone) {
    var dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < (deadzone === undefined ? 10 : deadzone)) return 'stop';
    var a = Math.atan2(-dy, dx) * 180 / Math.PI;
    if (a > -22.5 && a <= 22.5) return 'steer_right';
    if (a > 22.5 && a <= 67.5) return 'forward_right';
    if (a > 67.5 && a <= 112.5) return 'forward';
    if (a > 112.5 && a <= 157.5) return 'forward_left';
    if (a > 157.5 || a <= -157.5) return 'steer_left';
    if (a > -157.5 && a <= -112.5) return 'backward_left';
    if (a > -112.5 && a <= -67.5) return 'backward';
    return 'backward_right';
  }

  // Клавиши -> логические кнопки движения (v1.6.0).
  // WASD — колёса (модель «как у автомобиля»: A/D сами по себе только
  // поворачивают руль, машина едет только от W/S). Стрелки отданы камере
  // (см. app.js camKeys) — раньше и WASD, и стрелки вели колёса.
  var KEY_ACTIONS = {
    'w': 'up', 's': 'down', 'a': 'left', 'd': 'right',
    'q': 'spin_left', 'e': 'spin_right'
  };

  function keyAction(key) {
    if (key && key.length === 1) key = key.toLowerCase();
    return KEY_ACTIONS[key] || null;
  }

  // Камера с клавиатуры (v1.6.1): стрелки и I/J/K/L, шаг 5°.
  // Возвращает [dPan, dTilt] — прибавки к текущим углам; null — не камера.
  // Спецификация владельца робота (инверсия убрана):
  //   вверх  = tilt +5   (камера поднимается)
  //   вниз   = tilt -5   (камера опускается)
  //   вправо = pan  -5   (панорама вправо: на этом серво «вправо» = меньше)
  //   влево  = pan  +5
  // (v1.6.0: стрелки отданы камере, но tilt у них был перевёрнут —
  // «вверх» опускал камеру; I/K повторяли ту же ошибку.)
  var CAM_KEYS = {
    'i': [0, 5], 'k': [0, -5], 'j': [5, 0], 'l': [-5, 0],
    'ArrowUp': [0, 5], 'ArrowDown': [0, -5],
    'ArrowLeft': [5, 0], 'ArrowRight': [-5, 0]
  };

  function camKeyDelta(key) {
    return CAM_KEYS[key] || null;
  }

  // Камера с джойстика (v1.6.1): драг в ту же сторону, что стрелки.
  // dx,dy — пиксели от центра (y вниз), r — радиус. Раньше драг вправо
  // увеличивал pan (камера уходила влево), а драг вверх — уменьшал tilt
  // (камера опускалась): оба направления были инвертированы.
  function stickToCam(dx, dy, r) {
    if (!r) return { pan: 90, tilt: 90 };
    return {
      pan: 90 - Math.round(dx / r * 30),
      tilt: 90 - Math.round(dy / r * 30)
    };
  }

  // Набор зажатых логических кнопок -> команда движения.
  // Раньше отпускание ЛЮБОЙ клавиши посылало stop, даже если другая ещё
  // зажата, а W+D не давало диагональ.
  // v1.6.0: A/D без W/S -> steer_left/steer_right (руль без движения),
  // а не дуга вперёд.
  function combineKeys(held) {
    var up = !!held.up, down = !!held.down;
    if (up && down) { up = false; down = false; }
    var left = !!held.left, right = !!held.right;
    if (left && right) { left = false; right = false; }
    if (up || down) {
      var v = up ? 'forward' : 'backward';
      if (left) return v + '_left';
      if (right) return v + '_right';
      return v;
    }
    if (left) return 'steer_left';
    if (right) return 'steer_right';
    if (held.spin_left && !held.spin_right) return 'spin_left';
    if (held.spin_right && !held.spin_left) return 'spin_right';
    return 'stop';
  }

  // Идентификатор устройства (стабилен в пределах вкладки).
  function makeClientId(rnd) {
    var r = rnd || Math.random;
    return 'c' + Math.floor(r() * 0xffffff).toString(36) +
           Math.floor(r() * 0xffffff).toString(36);
  }

  // Состояние плашки «кто управляет».
  function driverState(driver, me) {
    if (!driver) return 'free';
    return driver === me ? 'you' : 'other';
  }

  var api = {
    mergeStatus: mergeStatus, reconnectDelay: reconnectDelay,
    vectorToDir: vectorToDir, keyAction: keyAction, combineKeys: combineKeys,
    camKeyDelta: camKeyDelta, stickToCam: stickToCam,
    makeClientId: makeClientId, driverState: driverState
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PanelCore = api;
})(typeof window !== 'undefined' ? window : this);
