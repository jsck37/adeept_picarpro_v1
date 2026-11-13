// getElementById вызывается сотни раз за обновление статуса — кэшируем узлы
(function () {
  var cache = {}, orig = document.getElementById.bind(document);
  document.getElementById = function (id) {
    var el = cache[id];
    if (el && el.isConnected) return el;
    el = orig(id);
    if (el) cache[id] = el;
    return el;
  };
})();

// Идентификатор этого устройства (вкладки) для арбитража управления
var CLIENT_ID = (function () {
  var k = 'picarpro_cid';
  try {
    var v = sessionStorage.getItem(k);
    if (!v) { v = PanelCore.makeClientId(); sessionStorage.setItem(k, v); }
    return v;
  } catch (e) { return PanelCore.makeClientId(); }
})();
var currentDriver = null;
var wsAttempt = 0;
var statusPending = null;   // статус, накопленный пока вкладка скрыта

var servoDefs = [
  { id: 0, name: 'Steering', min: 30, max: 150, init: 90 },
  { id: 1, name: 'Cam Pan',  min: 0,  max: 180, init: 90 },
  { id: 2, name: 'Cam Tilt', min: 80, max: 170, init: 90 },
  { id: 3, name: 'Servo 3',  min: 0,  max: 180, init: 90 },
  // v1.6.2 (подтверждено с железа): наклон клешни — канал 5, захват —
  // канал 6, канал 4 (локоть) — резерв, не подключён.
  { id: 4, name: 'Servo 4',    min: 0,   max: 180, init: 90 },
  { id: 5, name: 'Crane Tilt', min: 0,   max: 180, init: 180 },
  { id: 6, name: 'Crane Grab', min: 100, max: 180, init: 100 },
];
// Лимиты синхронизируются с сервера (config.py SERVO_LIMITS) — только чтение.

var hlMainOn = false;
var hlLeftSignal = false;
var hlRightSignal = false;
var currentLedMode = 'off';
var lastSentDir = 'stop';
var moveThrottle = 0;
var craneArmClosed = false;
var craneGripPosition = 'mid';   // v1.6.4: high убран
var logCounts = { info: 0, warn: 0, error: 0, debug: 0 };
var logFilters = { info: true, warn: true, error: true, debug: true };
var logSortMode = 'time';
var consoleAutoScroll = true;
var hw = {
  motors: false, servos: false, leds: false, buzzer: false,
  switches: false, ultrasonic: false, mpu6050: false,
  oled: false, camera: false, autonomous: false, crane: false,
  ds4: false, voice: false,
};
var ws = null;
var wsReconnectTimer = null;
var usePolling = false;
var pollTimer = null;

// ---- токен-авторизация (если задана на сервере) ----
function getToken() {
  return localStorage.getItem('picarpro_token') || '';
}
function setToken(t) {
  if (t) localStorage.setItem('picarpro_token', t);
  else localStorage.removeItem('picarpro_token');
}
function authHeaders() {
  var h = {'Content-Type': 'application/json', 'X-Client-Id': CLIENT_ID};
  var t = getToken();
  if (t) h['X-Auth-Token'] = t;
  return h;
}
function handleUnauthorized() {
  var t = prompt('PiCar Pro: access token required');
  if (t !== null) { setToken(t.trim()); location.reload(); }
}

function toast(msg, type) {
  type = type || 'info';
  var container = document.getElementById('toast-container');
  var el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(function() {
    el.style.animation = 'toast-out .3s ease forwards';
    setTimeout(function() { el.remove(); }, 300);
  }, 3000);
}

function hexToRgb(hex) {
  return [parseInt(hex.slice(1,3),16), parseInt(hex.slice(3,5),16), parseInt(hex.slice(5,7),16)];
}

var lastWebActive = 0;
function sendCommand(cmd, params) {
  params = params || {};
  if (cmd === 'move' || cmd === 'speed') {
    // web_active — не чаще раза в 10 с (раньше улетал с КАЖДЫМ move:
    // двойной спам по WS при драге джойстика)
    var nowWa = Date.now();
    if (nowWa - lastWebActive > 10000) {
      lastWebActive = nowWa;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({cmd: 'web_active', params: {active: true}}));
      }
    }
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({cmd: cmd, params: params}));
  } else {
    var urlMap = {
      'move': '/cmd/move', 'speed': '/cmd/speed', 'servo': '/cmd/servo',
      'led': '/cmd/led', 'buzzer': '/cmd/buzzer',
      'buzzer_stop': '/cmd/buzzer_stop',
      'cv_mode': '/cmd/cv_mode', 'auto': '/cmd/auto', 'crane': '/cmd/crane',
      'voice': '/cmd/voice', 'headlight': '/cmd/headlight', 'blinker': '/cmd/blinker',
      'side_lights': '/cmd/side_lights',
      'hand_color': '/cmd/hand_color', 'servo_get_limits': '/cmd/servo_get_limits',
      'i2c_scan': '/cmd/i2c_scan',
      'web_active': '/cmd/web_active', 'clear_log': '/cmd/clear_log',
      'steady': '/cmd/steady', 'battery': '/cmd/battery',
      'push': '/cmd/push', 'yolo_status': '/cmd/yolo_status',
      'mission': '/cmd/mission', 'estop': '/cmd/estop', 'take_control': '/cmd/take_control',
      'release_control': '/cmd/release_control',
    };
    var url = urlMap[cmd];
    if (url) {
      fetch(url, { method: 'POST', headers: authHeaders(), body: JSON.stringify(params) })
        .then(function(r) {
          if (r.status === 401) { handleUnauthorized(); return null; }
          return r.json();
        })
        .then(function(m) { handleResponse(m); })
        .catch(function() {});
    }
  }
}

document.querySelectorAll('.collapsible-header').forEach(function(header) {
  header.addEventListener('click', function() {
    var targetId = header.dataset.target;
    if (!targetId) return;
    var body = document.getElementById(targetId);
    if (!body) return;
    header.classList.toggle('open');
    body.classList.toggle('open');
    saveCollapsed();
  });
});

// Состояние свёрнутых блоков помним (на телефоне иначе каждый раз листать
// видео, режимы и геймпад до нужного).
var COLLAPSE_KEY = 'picarpro_collapsed';
function saveCollapsed() {
  try {
    var closed = [];
    document.querySelectorAll('.collapsible-header[data-target]').forEach(function(h) {
      if (!h.classList.contains('open')) closed.push(h.dataset.target);
    });
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify(closed));
  } catch (e) {}
}
function restoreCollapsed() {
  var closed = null;
  try { closed = JSON.parse(localStorage.getItem(COLLAPSE_KEY) || 'null'); } catch (e) {}
  if (closed === null && window.innerWidth <= 700) {
    // первый запуск на телефоне: оставляем видео, режимы и движение,
    // остальное свёрнуто
    closed = ['ds4-body', 'voice-body', 'servo-body', 'mpu-body', 'hl-body',
              'led-body', 'buzzer-body', 'cv-body'];
  }
  (closed || []).forEach(function(id) {
    var body = document.getElementById(id);
    var head = document.querySelector('.collapsible-header[data-target="' + id + '"]');
    if (body && head) { body.classList.remove('open'); head.classList.remove('open'); }
  });
}

document.querySelectorAll('.tab-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
    document.getElementById('content-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'console') {
      // очистка + перезагрузка истории: DOM консоли не растёт бесконечно
      var out = document.getElementById('console-output');
      if (out) out.innerHTML = '';
      fetchConsoleHistory();
    }
    updateVideoFeed();
  });
});

// ---- видео: пауза MJPEG-потока, когда он не виден (главный тормоз
// телефонов — декодирование 15 fps JPEG в фоне) --------------------
var FEED_URL = '/video_feed';
var FEED_OFF = "data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22640%22 height=%22480%22><rect fill=%22%231a1a1a%22 width=%22640%22 height=%22480%22/><text x=%2250%25%22 y=%2250%25%22 fill=%22%235f6368%22 font-size=%2220%22 text-anchor=%22middle%22 dy=%22.3em%22>Video paused</text></svg>";
function updateVideoFeed() {
  var img = document.getElementById('camera-feed');
  if (!img) return;
  var controlTab = document.getElementById('content-control');
  var visible = controlTab && controlTab.classList.contains('active') && !document.hidden;
  var want = visible ? FEED_URL : FEED_OFF;
  if (img.dataset.feedState !== want) {
    img.dataset.feedState = want;
    img.src = want;
  }
}
document.addEventListener('visibilitychange', updateVideoFeed);

function toggleHwSection(cardId, tagId, available) {
  var tagEl = document.getElementById(tagId);
  if (!tagEl) return;
  if (available) {
    tagEl.style.display = 'none';
    if (cardId) {
      var card = document.getElementById(cardId);
      if (card) card.classList.remove('hw-missing');
    }
    if (!cardId) {
      var parentCard = tagEl.closest('.card');
      if (parentCard) parentCard.classList.remove('hw-missing');
    }
  } else {
    tagEl.style.display = '';
    if (cardId) {
      var card = document.getElementById(cardId);
      if (card) card.classList.add('hw-missing');
    }
    if (!cardId) {
      var parentCard = tagEl.closest('.card');
      if (parentCard) parentCard.classList.add('hw-missing');
    }
  }
}

function updateHardwareUI(hardwareStatus) {
  if (!hardwareStatus) return;
  hw = hardwareStatus;
  toggleHwSection('card-autonomous', 'auto-missing-tag', hw.autonomous);
  toggleHwSection(null, 'servo-missing-tag', hw.servos);
  toggleHwSection('card-headlights', 'hl-missing-tag', hw.switches);
  toggleHwSection('card-led', 'led-missing-tag', hw.leds);
  toggleHwSection('card-buzzer', 'buzzer-missing-tag', hw.buzzer);
  toggleHwSection(null, 'mpu-missing-tag', hw.mpu6050);
  toggleHwSection(null, 'claw-missing-tag', hw.crane);
  toggleHwSection('card-voice', 'voice-missing-tag', hw.voice);
}

var lastNotDriverToast = 0;
function setDriver(driver) {
  currentDriver = driver || null;
  var st = PanelCore.driverState(currentDriver, CLIENT_ID);
  var chip = document.getElementById('driver-chip');
  if (chip) {
    chip.dataset.state = st;
    chip.textContent = st === 'you' ? 'You drive'
      : (st === 'other' ? 'Another device drives' : 'Control free');
  }
  document.body.classList.toggle('not-driver', st === 'other');
}

function handleResponse(m) {
  if (!m) return;
  if (m.driver !== undefined) setDriver(m.driver);
  if (m.missions) renderMissionList(m.missions);
  if (m.cmd === 'mission' && m.ok !== false) {
    renderMissionStatus(m);
    if (m.action === 'save' || m.action === 'delete' || m.saved) refreshMissions();
  }
  if (m.code === 'not_driver') {
    var now = Date.now();
    if (now - lastNotDriverToast > 3000) {   // не спамим при драге джойстика
      lastNotDriverToast = now;
      toast('Controlled by another device \u2014 press \u201cTake control\u201d', 'error');
    }
    return;
  }
  if (m.error) toast(m.error, 'error');
  if (m.interrupted_auto) toast('Auto mode stopped by manual control', 'info');
  if (m.cmd === 'servo_get_limits' && m.ok) applyServoLimits(m.limits);
}

// Статус: пока вкладка скрыта — копим, применяем одним разом при возврате
function onStatus(data, isDelta) {
  if (document.hidden) {
    statusPending = PanelCore.mergeStatus(statusPending, data, isDelta && !!statusPending);
    return;
  }
  updateStatus(data);
}
function flushPendingStatus() {
  if (statusPending && !document.hidden) {
    var d = statusPending; statusPending = null;
    updateStatus(d);
  }
}

function isPanelVisible() {
  var c = document.getElementById('content-control');
  return !document.hidden && !!c && c.classList.contains('active');
}
var lastReportedVisible = null;
function reportView() {
  var v = isPanelVisible();
  if (v === lastReportedVisible) return;
  lastReportedVisible = v;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({cmd: 'view', params: {visible: v}}));
  }
}

function scheduleReconnect() {
  if (wsReconnectTimer) return;
  var delay = PanelCore.reconnectDelay(wsAttempt++);
  wsReconnectTimer = setTimeout(function() { wsReconnectTimer = null; wsConnect(); }, delay);
}
function reconnectNow() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  wsConnect();
}
window.addEventListener('online', reconnectNow);

function wsConnect() {
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  var wsHost = location.hostname;
  var wsPort = 8888;
  var token = getToken();
  var url = 'ws://' + wsHost + ':' + wsPort + (token ? ('/?token=' + encodeURIComponent(token)) : '');
  try {
    ws = new WebSocket(url);
    ws.onopen = function() {
      wsAttempt = 0;
      document.getElementById('connection-dot').classList.remove('offline');
      usePolling = false;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      lastReportedVisible = null;
      // представляемся: id устройства для арбитража + видима ли панель
      ws.send(JSON.stringify({cmd: 'hello', params: {id: CLIENT_ID, visible: isPanelVisible()}}));
      lastReportedVisible = isPanelVisible();
      refreshMissions();
    };
    ws.onmessage = function(e) {
      try {
        var data = JSON.parse(e.data);
        var msgType = data.type || '';
        var msgData = data.data || {};
        if (msgType === 'auth_required') {
          handleUnauthorized();
          ws.close();
        }
        else if (msgType === 'status') onStatus(msgData, !!data.delta);
        else if (msgType === 'log') appendConsoleLine(data.text, data.ts);
        else if (msgType === 'log_history') {
          if (data.lines && data.lines.length) {
            data.lines.forEach(function(item) { appendConsoleLine(item[1], item[0]); });
          }
        }
        else if (msgType === 'response') handleResponse(msgData);
      } catch(err) {}
    };
    ws.onclose = function() {
      document.getElementById('connection-dot').classList.add('offline');
      // экспоненциальная пауза вместо фиксированных 3 с
      scheduleReconnect();
      if (!usePolling) { usePolling = true; startPolling(); }
    };
    ws.onerror = function() { ws.close(); };
  } catch(e) { usePolling = true; startPolling(); scheduleReconnect(); }
}

function startPolling() {
  if (pollTimer) return;
  function poll() {
    fetch('/api/status', { headers: (function(){ var h = {}; var t = getToken(); if (t) h['X-Auth-Token'] = t; return h; })() })
      .then(function(r) {
        if (r.status === 401) { handleUnauthorized(); throw new Error('unauthorized'); }
        return r.json();
      })
      .then(function(d) { updateStatus(d); })
      .catch(function() {});
  }
  poll();
  pollTimer = setInterval(poll, 1500);
}

function fetchConsoleHistory() {
  fetch('/api/logs', { headers: (function(){ var h = {}; var t = getToken(); if (t) h['X-Auth-Token'] = t; return h; })() })
    .then(function(r) { return r.json(); }).then(function(d) {
    if (d.lines) {
      // только последние 250 строк — на телефоне 800 DOM-узлов тормозят
      d.lines.slice(-CONSOLE_HISTORY_LINES)
        .forEach(function(item) { appendConsoleLine(item[1], item[0]); });
    }
  }).catch(function() {});
}

var firstStatus = true;
// v1.6.2: радар как в оригинальном приложении Adeept — сектор скана с
// точками препятствий; точки приходят в статусе (radar_data).
// v1.6.5: «нет эха» сервер шлёт как RADAR_MAX_CM (свободно) — рисуем такие
// точки тускло-зелёными, препятствия — красными; при недоступном HC-SR04
// выводим сообщение вместо пустого экрана.
var RADAR_MAX_CM = 200;   // ULTRASONIC_MAX_DISTANCE (м) * 100
var RADAR_SECTOR_DEG = 60;   // сектор скана: ±60° (см. _radar_scan)
var currentAutoMode = 'none';
function drawRadar(points, offline) {
  var cv = document.getElementById('radar-canvas');
  if (!cv || !cv.getContext) return;
  var ctx = cv.getContext('2d');
  var W = cv.width, H = cv.height;
  var cx = W / 2, cy = H - 20, R = H - 40;
  var sector = RADAR_SECTOR_DEG * Math.PI / 180;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0b1a10';
  ctx.fillRect(0, 0, W, H);
  // дистанционные дуги 50/100/150/200 см
  ctx.strokeStyle = 'rgba(80,200,120,0.35)';
  ctx.lineWidth = 1;
  [50, 100, 150, 200].forEach(function(cm) {
    ctx.beginPath();
    ctx.arc(cx, cy, R * cm / RADAR_MAX_CM, -Math.PI / 2 - sector, -Math.PI / 2 + sector);
    ctx.stroke();
  });
  // границы сектора и центральный луч
  [-1, 0, 1].forEach(function(k) {
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.sin(k * sector) * R, cy - Math.cos(k * sector) * R);
    ctx.strokeStyle = k === 0 ? 'rgba(80,200,120,0.5)' : 'rgba(80,200,120,0.35)';
    ctx.stroke();
  });
  // точки, увиденные ультразвуком: препятствия — красные, «свободно» —
  // тускло-зелёные у внешней дуги
  (points || []).forEach(function(p) {
    if (p.distance === null || p.distance === undefined || p.distance <= 0) return;
    var d = Math.min(p.distance, RADAR_MAX_CM);
    var a = (p.angle || 0) * Math.PI / 180;
    var x = cx + Math.sin(a) * R * d / RADAR_MAX_CM;
    var y = cy - Math.cos(a) * R * d / RADAR_MAX_CM;
    var free = p.distance >= RADAR_MAX_CM - 2;
    ctx.fillStyle = free ? 'rgba(80,200,120,0.45)' : '#ff5252';
    ctx.beginPath();
    ctx.arc(x, y, free ? 2 : 3.5, 0, Math.PI * 2);
    ctx.fill();
  });
  // корпус робота
  ctx.fillStyle = '#4caf50';
  ctx.fillRect(cx - 5, cy - 3, 10, 6);
  if (offline) {
    ctx.fillStyle = '#ffb74d';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('HC-SR04 offline', cx, 16);
  }
}
function updateStatus(d) {
  if (!d) return;
  if ('driver' in d) setDriver(d.driver);
  if (d.mission) renderMissionStatus(d.mission);
  if (d.clients !== undefined) document.getElementById('sb-clients').textContent = d.clients;
  if (d.cpu_temp !== undefined) document.getElementById('sb-cpu-temp').textContent = d.cpu_temp + '\u00B0C';
  if (d.cpu_usage !== undefined) document.getElementById('sb-cpu-usage').textContent = d.cpu_usage + '%';
  if (d.ram_percent !== undefined || d.ram) {
    var ramText;
    if (d.ram && d.ram.used_mb !== undefined) {
      ramText = d.ram.used_mb + '/' + d.ram.total_mb + 'M ' + d.ram.percent + '%';
    } else {
      ramText = d.ram_percent + '%';
    }
    document.getElementById('sb-ram').textContent = ramText;
  }
  if (d.distance !== undefined) document.getElementById('sb-distance').textContent = d.distance + 'cm';
  // v1.6.5: скорость в панели — фактическая: удержание L2/R2 (турбо) = 100 %,
  // геймпад без турбо = его текущая скорость хода, иначе ползунок веб-панели
  var spdEl = document.getElementById('sb-speed');
  var pad = d.ds4;
  if (pad && pad.connected && pad.turbo) {
    spdEl.textContent = '100%';
    spdEl.classList.add('turbo');
  } else if (pad && pad.connected && pad.drive_speed > 0) {
    spdEl.textContent = pad.drive_speed + '%';
    spdEl.classList.remove('turbo');
  } else if (d.speed !== undefined) {
    spdEl.textContent = d.speed + '%';
    spdEl.classList.remove('turbo');
  }
  var autoModeLabels = {
    'none': 'Ready', 'radarScan': 'Radar', 'automatic': 'Auto Drive',
    'trackLine': 'IR Line', 'trackLineCV': 'CV Line', 'trackHand': 'Hand Track',
    'keepDistance': 'Distance', 'findColor': 'Color Track', 'watchDog': 'Watchdog',
    'obstacleGrab': 'Obstacle Grab', 'mazeU': 'Maze U'
  };
  if (d.auto_mode !== undefined) currentAutoMode = d.auto_mode;
  var radarBox = document.getElementById('radar-box');
  if (radarBox) radarBox.style.display = currentAutoMode === 'radarScan' ? 'block' : 'none';
  if (d.radar_data) drawRadar(d.radar_data, d.ultra_ok === false);
  else if (currentAutoMode === 'radarScan') drawRadar([], d.ultra_ok === false);
  var sbModule = document.getElementById('sb-module');
  if (sbModule) sbModule.textContent = autoModeLabels[d.auto_mode || 'none'] || d.auto_mode || 'Ready';
  if (d.hw) {
    updateHardwareUI(d.hw);
    if (firstStatus) { firstStatus = false; }
  }
  if (d.servo_limits) {
    applyServoLimits(d.servo_limits);
  }
  // синхронизация углов с сервера (когда поле не в фокусе)
  if (d.servo_angles && Array.isArray(d.servo_angles)) {
    d.servo_angles.forEach(function(ang, sid) {
      var inp = document.querySelector('input.servo-angle-input[data-servo="' + sid + '"]');
      if (inp && document.activeElement !== inp) {
        var def = servoDefById(sid);
        if (def && Math.abs((def.cur || 0) - ang) >= 1) {
          def.cur = ang;
          inp.value = ang;
          var val = document.querySelector('label .val[data-servo-val="' + sid + '"]');
          if (val) val.textContent = ang + '\u00B0';
        }
      }
    });
  }
  // статус Push Mode (толчок препятствий клешнёй по HC-SR04)
  if (d.push && d.push.active !== undefined) {
    setPortedMode('push', d.push.active);
  }
  var mpu = d.mpu6050;
  if (!('mpu6050' in d)) {
    // поле не менялось — оставляем показания как есть
  } else if (mpu) {
    document.getElementById('sb-imu').textContent = 'R:' + mpu.roll + '\u00B0 P:' + mpu.pitch + '\u00B0';
    document.getElementById('mpu-ax').textContent = mpu.accel.x.toFixed(3);
    document.getElementById('mpu-ay').textContent = mpu.accel.y.toFixed(3);
    document.getElementById('mpu-az').textContent = mpu.accel.z.toFixed(3);
    document.getElementById('mpu-gx').textContent = mpu.gyro.x.toFixed(1);
    document.getElementById('mpu-gy').textContent = mpu.gyro.y.toFixed(1);
    document.getElementById('mpu-gz').textContent = mpu.gyro.z.toFixed(1);
    document.getElementById('mpu-roll').textContent = mpu.roll.toFixed(1);
    document.getElementById('mpu-pitch').textContent = mpu.pitch.toFixed(1);
  } else {
    document.getElementById('sb-imu').textContent = 'N/A';
  }
  if (!('ir_left' in d)) {
    // не менялось
  } else if (d.ir_left !== undefined && d.ir_left !== null) {
    document.getElementById('ir-left-val').textContent = d.ir_left ? 'LINE' : 'CLEAR';
    document.getElementById('ir-left-val').style.color = d.ir_left ? '#ea4335' : '#34a853';
  } else {
    document.getElementById('ir-left-val').textContent = 'N/A';
    document.getElementById('ir-left-val').style.color = '#9aa0a6';
  }
  if (!('ir_middle' in d)) {
    // не менялось
  } else if (d.ir_middle !== undefined && d.ir_middle !== null) {
    document.getElementById('ir-middle-val').textContent = d.ir_middle ? 'LINE' : 'CLEAR';
    document.getElementById('ir-middle-val').style.color = d.ir_middle ? '#ea4335' : '#34a853';
  } else {
    document.getElementById('ir-middle-val').textContent = 'N/A';
    document.getElementById('ir-middle-val').style.color = '#9aa0a6';
  }
  if (!('ir_right' in d)) {
    // не менялось
  } else if (d.ir_right !== undefined && d.ir_right !== null) {
    document.getElementById('ir-right-val').textContent = d.ir_right ? 'LINE' : 'CLEAR';
    document.getElementById('ir-right-val').style.color = d.ir_right ? '#ea4335' : '#34a853';
  } else {
    document.getElementById('ir-right-val').textContent = 'N/A';
    document.getElementById('ir-right-val').style.color = '#9aa0a6';
  }
  var lvBanner = document.getElementById('low-voltage-banner');
  if (lvBanner && 'low_voltage' in d) lvBanner.style.display = d.low_voltage ? 'block' : 'none';
  var bat = d.battery;
  var batItem = document.getElementById('sb-battery-item');
  if (batItem && 'battery' in d) {
    if (bat && bat.adc && bat.percent !== null && bat.percent !== undefined) {
      batItem.style.display = '';
      var batEl = document.getElementById('sb-battery');
      batEl.textContent = bat.percent + '%';
      batEl.style.color = bat.low ? '#ea4335' : '#34a853';
    } else {
      batItem.style.display = 'none';
    }
  }
  var steadyItem = document.getElementById('sb-steady-item');
  if (steadyItem && ('steady_active' in d || 'hw' in d)) {
    var steadyOn = !!d.steady_active;
    steadyItem.style.display = hw.mpu6050 ? '' : 'none';
    var stEl = document.getElementById('sb-steady');
    stEl.textContent = steadyOn ? 'ON' : 'OFF';
    stEl.style.color = steadyOn ? '#34a853' : '#9aa0a6';
    var stBtn = document.getElementById('steady-toggle');
    if (stBtn) stBtn.classList.toggle('active', steadyOn);
  }
  var ds4 = d.ds4;
  if (ds4) {
    document.getElementById('sb-ds4').textContent = ds4.connected ? 'ON' : '--';
    var ds4Dot = document.getElementById('ds4-status-dot');
    var ds4Text = document.getElementById('ds4-status-text');
    if (ds4Dot) ds4Dot.style.background = ds4.connected ? '#34a853' : '#ea4335';
    if (ds4Text) {
      ds4Text.textContent = ds4.connected ? 'Connected (' + ds4.connect_count + '\u00D7)' : 'Not connected';
      ds4Text.style.color = ds4.connected ? '#34a853' : '#9aa0a6';
    }
    var btDiscBtn = document.getElementById('bt-disconnect-btn');
    if (btDiscBtn) btDiscBtn.style.display = ds4.connected ? '' : 'none';
    if (ds4.crane_arm_closed !== undefined) {
      craneArmClosed = ds4.crane_arm_closed;
      updateCraneArmUI();
    }
    if (ds4.crane_grip !== undefined) {
      craneGripPosition = ds4.crane_grip;
      updateCraneGripUI();
    }
  }
  if (d.crane_arm_closed !== undefined) {
    craneArmClosed = d.crane_arm_closed;
    updateCraneArmUI();
  }
  if (d.crane_grip_position !== undefined) {
    craneGripPosition = d.crane_grip_position;
    updateCraneGripUI();
  }
  if (d.cv_mode !== undefined) {
    var badge = document.getElementById('cv-badge');
    if (d.cv_mode && d.cv_mode !== 'none') {
      badge.textContent = 'CV: ' + d.cv_mode;
      badge.classList.add('visible');
    } else {
      badge.classList.remove('visible');
    }
  }
  if (d.headlight !== undefined) {
    hlMainOn = d.headlight;
    var hlMainBtn = document.getElementById('hl-main');
    if (hlMainBtn) hlMainBtn.className = 'headlight-btn ' + (hlMainOn ? 'on' : 'off');
  }
  if (d.side_lights !== undefined) {
    hlSideOn = d.side_lights;
    var hlSideBtn = document.getElementById('hl-side');
    if (hlSideBtn) hlSideBtn.className = 'headlight-btn ' + (hlSideOn ? 'on' : 'off');
  }
  if (d.left_blinker !== undefined) {
    hlLeftSignal = d.left_blinker;
    document.getElementById('hl-left-signal').className = 'signal-btn ' + (hlLeftSignal ? 'on' : 'off');
    var lStat = document.getElementById('blinker-left-status');
    if (lStat) { lStat.textContent = hlLeftSignal ? 'ON' : 'OFF'; lStat.style.color = hlLeftSignal ? '#fbbc04' : '#9aa0a6'; }
    var lDot = document.getElementById('blinker-left-dot');
    if (lDot) lDot.dataset.on = hlLeftSignal ? '1' : '0';
  }
  if (d.right_blinker !== undefined) {
    hlRightSignal = d.right_blinker;
    document.getElementById('hl-right-signal').className = 'signal-btn ' + (hlRightSignal ? 'on' : 'off');
    var rStat = document.getElementById('blinker-right-status');
    if (rStat) { rStat.textContent = hlRightSignal ? 'ON' : 'OFF'; rStat.style.color = hlRightSignal ? '#fbbc04' : '#9aa0a6'; }
    var rDot = document.getElementById('blinker-right-dot');
    if (rDot) rDot.dataset.on = hlRightSignal ? '1' : '0';
  }
  if (d.voice) {
    var v = d.voice;
    var vDot = document.getElementById('voice-status-dot');
    var vText = document.getElementById('voice-status-text');
    var vStart = document.getElementById('voice-start-btn');
    var vStop = document.getElementById('voice-stop-btn');
    if (vDot) vDot.style.background = v.active ? '#34a853' : (v.available ? '#fbbc04' : '#9aa0a6');
    if (vText) {
      vText.textContent = v.active ? 'Listening...' : (v.available ? 'Ready' : 'Inactive');
      vText.style.color = v.active ? '#34a853' : '#9aa0a6';
    }
    if (vStart && vStop) {
      if (v.active) { vStart.style.display = 'none'; vStop.style.display = ''; }
      else { vStart.style.display = ''; vStop.style.display = 'none'; }
    }
    if (v.last_command) document.getElementById('voice-last-cmd').textContent = v.last_command;
  }
  if (d.led_mode !== undefined) {
    currentLedMode = d.led_mode;
    document.querySelectorAll('#led-group .gbtn').forEach(function(b) {
      b.classList.toggle('active', b.dataset.led === currentLedMode);
    });
  }
}

function updateCraneArmUI() {
  var toggleBtn = document.getElementById('crane-arm-toggle');
  if (!toggleBtn) return;
  if (craneArmClosed) {
    toggleBtn.classList.add('active');
    toggleBtn.textContent = 'Release';
  } else {
    toggleBtn.classList.remove('active');
    toggleBtn.textContent = 'Grab / Release';
  }
}

function updateCraneGripUI() {
  var lowBtn = document.getElementById('crane-grip-low');
  var midBtn = document.getElementById('crane-grip-mid');
  var label = document.getElementById('crane-grip-label');
  if (!lowBtn) return;
  lowBtn.classList.remove('active');
  midBtn.classList.remove('active');
  var angleMap = { low: 100, mid: 135 };
  var labelMap = { low: 'Low', mid: 'Mid' };
  if (craneGripPosition === 'low') lowBtn.classList.add('active');
  else if (craneGripPosition === 'mid') midBtn.classList.add('active');
  if (label && angleMap[craneGripPosition] !== undefined) {
    label.textContent = labelMap[craneGripPosition] + ' (' + angleMap[craneGripPosition] + '\u00B0)';
  }
}

// ===================== Servo Control (степперы ±1°) =====================
// Степперы: точная настройка по 1 градусу с удержанием (автоповтор)
// и прямым вводом угла. Лимиты — только из config.py (SERVO_LIMITS),
// редактирование из UI удалено v1.3.0.

function servoDefById(id) {
  for (var i = 0; i < servoDefs.length; i++) {
    if (servoDefs[i].id === id) return servoDefs[i];
  }
  return null;
}

function clampAngle(def, angle) {
  angle = parseInt(angle, 10);
  if (isNaN(angle)) angle = def.init;
  return Math.max(def.min, Math.min(def.max, angle));
}

function sendServoAngle(id, angle) {
  var def = servoDefById(id);
  if (!def) return;
  var clamped = clampAngle(def, angle);
  def.cur = clamped;
  var inp = document.querySelector('input.servo-angle-input[data-servo="' + id + '"]');
  var val = document.querySelector('label .val[data-servo-val="' + id + '"]');
  if (inp) inp.value = clamped;
  if (val) val.textContent = clamped + '\u00B0';
  sendCommand('servo', { id: id, angle: clamped });
}

function buildServoGrid() {
  var grid = document.getElementById('servo-grid');
  if (!grid) return;
  grid.innerHTML = '';
  servoDefs.forEach(function(s) {
    if (s.cur === undefined) s.cur = s.init;
    var item = document.createElement('div');
    item.className = 'servo-item';
    item.innerHTML = '<label>' + s.name + ' <span class="val" data-servo-val="' + s.id + '">' + s.cur + '\u00B0</span></label>' +
      '<div class="servo-step-row">' +
      '<button type="button" class="servo-step-btn" data-servo-dec="' + s.id + '" title="\u22121\u00B0 (удерживайте для автоповтора)">&minus;</button>' +
      '<input type="number" class="servo-angle-input" data-servo="' + s.id + '" min="' + s.min + '" max="' + s.max + '" value="' + s.cur + '">' +
      '<button type="button" class="servo-step-btn" data-servo-inc="' + s.id + '" title="+1\u00B0 (удерживайте для автоповтора)">+</button>' +
      '</div>' +
      '<div class="servo-limits-row"><span class="servo-limit-label">' + s.min + '\u2013' + s.max + '\u00B0</span></div>';
    grid.appendChild(item);
  });

  // --- степперы: тап = ±1°, удержание = автоповтор ----------------------
  document.querySelectorAll('.servo-step-btn').forEach(function(btn) {
    var repeatTimer = null, delayTimer = null;
    function stepOnce() {
      var isInc = btn.hasAttribute('data-servo-inc');
      var id = parseInt(isInc ? btn.getAttribute('data-servo-inc')
                              : btn.getAttribute('data-servo-dec'), 10);
      var def = servoDefById(id);
      if (!def) return;
      sendServoAngle(id, def.cur + (isInc ? 1 : -1));
    }
    function stopRepeat() {
      if (delayTimer) { clearTimeout(delayTimer); delayTimer = null; }
      if (repeatTimer) { clearInterval(repeatTimer); repeatTimer = null; }
    }
    btn.addEventListener('pointerdown', function(e) {
      e.preventDefault();
      stepOnce();
      delayTimer = setTimeout(function() {
        repeatTimer = setInterval(stepOnce, 120);   // ~8°/с при удержании
      }, 400);
    });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach(function(ev) {
      btn.addEventListener(ev, stopRepeat);
    });
    btn.addEventListener('contextmenu', function(e) { e.preventDefault(); });
  });

  // --- прямой ввод угла ---------------------------------------------------
  document.querySelectorAll('input.servo-angle-input').forEach(function(inp) {
    inp.addEventListener('change', function() {
      sendServoAngle(parseInt(inp.getAttribute('data-servo'), 10), inp.value);
    });
  });
}

function applyServoLimits(limits) {
  for (var sid in limits) {
    var lim = limits[sid];
    var id = parseInt(sid, 10);
    var def = servoDefById(id);
    if (!def) continue;
    def.min = lim.min;
    def.max = lim.max;
    if (def.cur === undefined) def.cur = def.init;
    def.cur = clampAngle(def, def.cur);
    var angleInp = document.querySelector('input.servo-angle-input[data-servo="' + sid + '"]');
    if (angleInp) {
      angleInp.min = lim.min;
      angleInp.max = lim.max;
      // не затираем значение, пока пользователь редактирует поле
      if (document.activeElement !== angleInp) angleInp.value = def.cur;
    }
    var val = document.querySelector('label .val[data-servo-val="' + sid + '"]');
    if (val) val.textContent = def.cur + '\u00B0';
  }
}

var joystickContainer = document.getElementById('joystick-container');
var joystickKnob = document.getElementById('joystick-knob');
var joystickLabel = document.getElementById('joystick-label');

var dirLabels = {
  forward: 'Forward', backward: 'Backward',
  forward_left: 'Fwd-Left', forward_right: 'Fwd-Right',
  backward_left: 'Back-Left', backward_right: 'Back-Right', stop: 'Stopped',
  steer_left: 'Steer left', steer_right: 'Steer right',
  spin_left: 'Spin left', spin_right: 'Spin right'
};

// Универсальный джойстик на Pointer Events: у каждого свой pointerId, поэтому
// «колёса» и «камера» работают одновременно двумя пальцами. Раньше оба
// слушали общие touchend/touchmove и брали touches[0]: отпускание одного
// пальца сбрасывало второй джойстик, а камера «читала» палец колёс.
function makeStick(container, knob, pad, onMove, onEnd) {
  if (!container || !knob) return null;
  var activeId = null, rect = null;
  function center() {
    if (!rect) {          // rect кэшируется на время драга (без reflow на каждый move)
      var b = container.getBoundingClientRect();
      rect = { x: b.left + b.width / 2, y: b.top + b.height / 2, r: b.width / 2 - pad };
    }
    return rect;
  }
  function handle(e) {
    var c = center();
    var dx = e.clientX - c.x, dy = e.clientY - c.y;
    var dist = Math.sqrt(dx * dx + dy * dy);
    if (dist > c.r) { dx = dx / dist * c.r; dy = dy / dist * c.r; }
    knob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
    onMove(dx, dy, c.r);
  }
  function finish() {
    if (activeId === null) return;
    activeId = null;
    knob.classList.add('spring-back');
    knob.style.transform = 'translate(-50%, -50%)';
    setTimeout(function() { knob.classList.remove('spring-back'); }, 300);
    onEnd();
  }
  container.addEventListener('pointerdown', function(e) {
    if (activeId !== null) return;
    activeId = e.pointerId;
    rect = null;
    try { container.setPointerCapture(e.pointerId); } catch (err) {}
    knob.classList.remove('spring-back');
    e.preventDefault();
    handle(e);
  });
  container.addEventListener('pointermove', function(e) {
    if (e.pointerId === activeId) handle(e);
  });
  function up(e) { if (e.pointerId === activeId) finish(); }
  container.addEventListener('pointerup', up);
  container.addEventListener('pointercancel', up);
  container.addEventListener('lostpointercapture', up);
  return { release: finish, active: function() { return activeId !== null; } };
}

function sendMoveDir(dir, force) {
  var now = Date.now();
  if (dir !== lastSentDir || (force && now - moveThrottle > 150)) {
    sendCommand('move', { dir: dir });
    lastSentDir = dir;
    moveThrottle = now;
  }
}

var wheelsStick = makeStick(joystickContainer, joystickKnob, 24, function(dx, dy) {
  var dir = PanelCore.vectorToDir(dx, dy, 10);
  if (joystickLabel) joystickLabel.textContent = dirLabels[dir] || dir;
  sendMoveDir(dir, true);          // тот же темп повторов, что и раньше (150 мс)
}, function() {
  if (joystickLabel) joystickLabel.textContent = 'Wheels \u2014 WASD / joystick';
  sendMoveDir('stop', false);
});

var camJoystickContainer = document.getElementById('cam-joystick-container');
var camJoystickKnob = document.getElementById('cam-joystick-knob');
var camJoystickLabel = document.getElementById('cam-joystick-label');
var camPan = 90, camTilt = 90;

function camClamp(id, v) {
  var def = servoDefById(id);
  var lo = def ? def.min : 0, hi = def ? def.max : 180;
  return Math.max(lo, Math.min(hi, v));
}

var camLastSent = { pan: 0, tilt: 0 };

function setCamera(newPan, newTilt) {
  // троттлинг: touchmove на телефоне стреляет до 120 раз/с — спам
  // командами серво давал дрожание камеры и нагрев (не чаще 25 Гц)
  var now = Date.now();
  if (newPan !== camPan && now - camLastSent.pan > 40) {
    sendCommand('servo', { id: 1, angle: newPan });
    camPan = newPan;
    camLastSent.pan = now;
  }
  if (newTilt !== camTilt && now - camLastSent.tilt > 40) {
    sendCommand('servo', { id: 2, angle: newTilt });
    camTilt = newTilt;
    camLastSent.tilt = now;
  }
}

var camStick = makeStick(camJoystickContainer, camJoystickKnob, 21, function(dx, dy, r) {
  // v1.6.1: драг в ту же сторону, что стрелки (см. PanelCore.stickToCam):
  // вверх — камера поднимается, вправо — панорама вправо (pan уменьшается).
  var tgt = PanelCore.stickToCam(dx, dy, r);
  var newPan = camClamp(1, tgt.pan);
  var newTilt = camClamp(2, tgt.tilt);
  setCamera(newPan, newTilt);
  if (camJoystickLabel) {
    camJoystickLabel.textContent = (Math.abs(dx) < 5 && Math.abs(dy) < 5)
      ? 'Camera \u2014 drag / arrows' : 'Pan:' + newPan + '\u00B0 Tilt:' + newTilt + '\u00B0';
  }
}, function() {
  if (camJoystickLabel) camJoystickLabel.textContent = 'Camera \u2014 drag / arrows';
});

// ---- клавиатура: W/A/S/D — колёса (A/D сами по себе только руль),
// Q/E — разворот на месте, Пробел — стоп, Esc — аварийный стоп,
// стрелки и I/J/K/L — камера (v1.6.0: стрелки отданы камере) ----
// Камера: стрелки и I/J/K/L, шаг 5° за нажатие. Таблица дельт — в
// panel-core.js (PanelCore.camKeyDelta), проверяется node-тестами:
// вверх = tilt +5, вниз = tilt -5, вправо = pan -5, влево = pan +5
// (v1.6.1: убрана инверсия tilt у стрелок и I/K).
var heldKeys = {};
function typingTarget(e) {
  var t = e.target && e.target.tagName;
  return t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT';
}
function applyKeyDir() {
  var dir = PanelCore.combineKeys(heldKeys);
  if (joystickLabel && !(wheelsStick && wheelsStick.active())) {
    joystickLabel.textContent = dir === 'stop' ? 'Wheels \u2014 WASD / joystick' : (dirLabels[dir] || dir);
  }
  sendMoveDir(dir, false);
}
document.addEventListener('keydown', function(e) {
  if (typingTarget(e) || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'Escape') { emergencyStop(); e.preventDefault(); return; }
  if (e.key === ' ') {
    heldKeys = {};
    sendMoveDir('stop', false);
    e.preventDefault();
    return;
  }
  var ck = PanelCore.camKeyDelta(e.key.length === 1 ? e.key.toLowerCase() : e.key);
  if (ck) {
    setCamera(camClamp(1, camPan + ck[0]), camClamp(2, camTilt + ck[1]));
    e.preventDefault();
    return;
  }
  var act = PanelCore.keyAction(e.key);
  if (!act) return;
  e.preventDefault();               // стрелки не должны скроллить страницу
  if (e.repeat) return;
  heldKeys[act] = true;
  applyKeyDir();
});
document.addEventListener('keyup', function(e) {
  if (typingTarget(e)) return;
  var act = PanelCore.keyAction(e.key);
  if (!act) return;
  delete heldKeys[act];
  applyKeyDir();                    // отпустили одну из двух — едем по оставшейся
});

// Безопасность: потеряли фокус / вкладку скрыли / пальцы ушли — стоп.
// Иначе робот ехал бы дальше с «залипшей» клавишей или зажатым джойстиком.
function releaseAllInputs() {
  heldKeys = {};
  if (wheelsStick) wheelsStick.release();
  if (camStick) camStick.release();
  if (lastSentDir !== 'stop') sendMoveDir('stop', false);
}
window.addEventListener('blur', releaseAllInputs);
document.addEventListener('visibilitychange', function() {
  if (document.hidden) releaseAllInputs();
  reportView();
  flushPendingStatus();
  if (!document.hidden) reconnectNow();
});

function emergencyStop() {
  heldKeys = {};
  if (wheelsStick) wheelsStick.release();
  lastSentDir = 'stop';
  sendCommand('estop', {});
  toast('EMERGENCY STOP', 'error');
}

document.querySelectorAll('.cv-btn[data-cv]').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.cv-btn[data-cv]').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    var mode = btn.dataset.cv;
    var badge = document.getElementById('cv-badge');
    var handRow = document.getElementById('hand-color-row');
    if (mode === 'findlineCV') {
      badge.textContent = 'CV: Line Follow';
      badge.classList.add('visible');
      sendCommand('auto', { func: 'trackLineCV' });
      if (handRow) handRow.style.display = 'none';
    } else if (mode === 'trackHand') {
      badge.textContent = 'CV: Hand Track';
      badge.classList.add('visible');
      sendCommand('auto', { func: 'trackHand' });
      if (handRow) handRow.style.display = '';
    } else if (mode === 'findColor') {
      badge.textContent = 'CV: Color Track';
      badge.classList.add('visible');
      sendCommand('auto', { func: 'findColor' });
      if (handRow) handRow.style.display = '';
    } else {
      badge.textContent = 'CV: ' + mode.charAt(0).toUpperCase() + mode.slice(1);
      badge.classList.toggle('visible', mode !== 'none');
      if (mode !== 'none') sendCommand('auto', { func: 'stop' });
      sendCommand('cv_mode', { mode: mode });
      if (handRow) handRow.style.display = 'none';
    }
  });
});

document.querySelectorAll('#hand-color-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#hand-color-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    sendCommand('hand_color', { preset: btn.dataset.preset });
  });
});

function bindHandSlider(id, valId) {
  var el = document.getElementById(id);
  var valEl = document.getElementById(valId);
  if (!el || !valEl) return;
  el.addEventListener('input', function() { valEl.textContent = el.value; });
}
bindHandSlider('hand-h-low', 'hand-h-low-val');
bindHandSlider('hand-s-low', 'hand-s-low-val');
bindHandSlider('hand-v-low', 'hand-v-low-val');
bindHandSlider('hand-h-high', 'hand-h-high-val');
bindHandSlider('hand-s-high', 'hand-s-high-val');
bindHandSlider('hand-v-high', 'hand-v-high-val');

var handColorApply = document.getElementById('hand-color-apply');
if (handColorApply) {
  handColorApply.addEventListener('click', function() {
    sendCommand('hand_color', {
      h_low: parseInt(document.getElementById('hand-h-low').value),
      s_low: parseInt(document.getElementById('hand-s-low').value),
      v_low: parseInt(document.getElementById('hand-v-low').value),
      h_high: parseInt(document.getElementById('hand-h-high').value),
      s_high: parseInt(document.getElementById('hand-s-high').value),
      v_high: parseInt(document.getElementById('hand-v-high').value),
    });
    document.querySelectorAll('#hand-color-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    toast('Custom hand color applied', 'success');
  });
}

var speedSlider = document.getElementById('speed-slider');
var speedVal = document.getElementById('speed-val');
if (speedSlider) {
  speedSlider.addEventListener('input', function() { speedVal.textContent = speedSlider.value + '%'; });
  speedSlider.addEventListener('change', function() { sendCommand('speed', { value: parseInt(speedSlider.value) }); });
}

document.querySelectorAll('.crane-arm-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    craneArmClosed = !craneArmClosed;
    sendCommand('crane', { action: craneArmClosed ? 'arm_close' : 'arm_open' });
    updateCraneArmUI();
  });
});

document.querySelectorAll('.crane-grip-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var action = btn.dataset.action;
    if (action === 'grip_low') craneGripPosition = 'low';
    else if (action === 'grip_mid') craneGripPosition = 'mid';
    // v1.6.4: grip_high удалён — осталось low/mid
    sendCommand('crane', { action: action });
    updateCraneGripUI();
  });
});

document.getElementById('hl-main').addEventListener('click', function() {
  hlMainOn = !hlMainOn;
  sendCommand('headlight', { action: hlMainOn ? 'on' : 'off' });
  this.className = 'headlight-btn ' + (hlMainOn ? 'on' : 'off');
});

// v1.6.4: боковые габариты (Side lights) — кнопка в окне фар + геймпад L1
var hlSideOn = false;
document.getElementById('hl-side').addEventListener('click', function() {
  hlSideOn = !hlSideOn;
  sendCommand('side_lights', { action: hlSideOn ? 'on' : 'off' });
  this.className = 'headlight-btn ' + (hlSideOn ? 'on' : 'off');
});

document.getElementById('hl-left-signal').addEventListener('click', function() {
  hlLeftSignal = !hlLeftSignal;
  if (hlLeftSignal) {
    hlRightSignal = false;
    document.getElementById('hl-right-signal').className = 'signal-btn off';
    var rDot = document.getElementById('blinker-right-dot');
    if (rDot) rDot.dataset.on = '0';
    var rStat = document.getElementById('blinker-right-status');
    if (rStat) { rStat.textContent = 'OFF'; rStat.style.color = '#9aa0a6'; }
  }
  sendCommand('blinker', { side: 'left', active: hlLeftSignal });
  this.className = 'signal-btn ' + (hlLeftSignal ? 'on' : 'off');
  var lDot = document.getElementById('blinker-left-dot');
  if (lDot) lDot.dataset.on = hlLeftSignal ? '1' : '0';
  var lStat = document.getElementById('blinker-left-status');
  if (lStat) { lStat.textContent = hlLeftSignal ? 'ON' : 'OFF'; lStat.style.color = hlLeftSignal ? '#fbbc04' : '#9aa0a6'; }
});

document.getElementById('hl-right-signal').addEventListener('click', function() {
  hlRightSignal = !hlRightSignal;
  if (hlRightSignal) {
    hlLeftSignal = false;
    document.getElementById('hl-left-signal').className = 'signal-btn off';
    var lDot = document.getElementById('blinker-left-dot');
    if (lDot) lDot.dataset.on = '0';
    var lStat = document.getElementById('blinker-left-status');
    if (lStat) { lStat.textContent = 'OFF'; lStat.style.color = '#9aa0a6'; }
  }
  sendCommand('blinker', { side: 'right', active: hlRightSignal });
  this.className = 'signal-btn ' + (hlRightSignal ? 'on' : 'off');
  var rDot = document.getElementById('blinker-right-dot');
  if (rDot) rDot.dataset.on = hlRightSignal ? '1' : '0';
  var rStat = document.getElementById('blinker-right-status');
  if (rStat) { rStat.textContent = hlRightSignal ? 'ON' : 'OFF'; rStat.style.color = hlRightSignal ? '#fbbc04' : '#9aa0a6'; }
});

var ledColorInput = document.getElementById('led-color');

document.querySelectorAll('.color-preset').forEach(function(btn) {
  btn.addEventListener('click', function() {
    ledColorInput.value = btn.dataset.hex;
    if (currentLedMode !== 'off' && currentLedMode !== 'rainbow' && currentLedMode !== 'police') {
      sendCommand('led', { mode: currentLedMode, color: hexToRgb(ledColorInput.value) });
    }
  });
});
ledColorInput.addEventListener('input', function() {
  if (currentLedMode === 'solid' || currentLedMode === 'breath' || currentLedMode === 'flow' || currentLedMode === 'colorWipe') {
    sendCommand('led', { mode: currentLedMode, color: hexToRgb(ledColorInput.value) });
  }
});
ledColorInput.addEventListener('change', function() {
  if (currentLedMode !== 'off' && currentLedMode !== 'rainbow' && currentLedMode !== 'police') {
    sendCommand('led', { mode: currentLedMode, color: hexToRgb(ledColorInput.value) });
  }
});
document.querySelectorAll('#led-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#led-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    currentLedMode = btn.dataset.led;
    sendCommand('led', { mode: currentLedMode, color: hexToRgb(ledColorInput.value) });
  });
});

document.querySelectorAll('#buzzer-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    if (btn.dataset.buzzer === 'stop') {
      sendCommand('buzzer_stop', {});
    } else {
      sendCommand('buzzer', { melody: btn.dataset.buzzer });
    }
  });
});

updateCraneArmUI();
updateCraneGripUI();

document.querySelectorAll('#auto-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#auto-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    if (btn.dataset.auto !== 'stop') btn.classList.add('active');
    sendCommand('auto', { func: btn.dataset.auto });
  });
});

var voiceStartBtn = document.getElementById('voice-start-btn');
var voiceStopBtn = document.getElementById('voice-stop-btn');
if (voiceStartBtn) {
  voiceStartBtn.addEventListener('click', function() {
    sendCommand('voice', { action: 'start' });
  });
}
if (voiceStopBtn) {
  voiceStopBtn.addEventListener('click', function() {
    sendCommand('voice', { action: 'stop' });
  });
}

var steadyToggle = document.getElementById('steady-toggle');
if (steadyToggle) {
  steadyToggle.addEventListener('click', function() {
    sendCommand('steady', { action: 'toggle' });
  });
}

// --- Push Mode: расталкивание препятствий клешнёй по HC-SR04 ----------------
var portedModes = {
  push:  { btn: 'push-toggle', cmd: 'push', active: false,
           labelOn: 'Stop Push', labelOff: 'Push Mode' },
};

Object.keys(portedModes).forEach(function(key) {
  var m = portedModes[key];
  var btn = document.getElementById(m.btn);
  if (!btn) return;
  btn.addEventListener('click', function() {
    var action = m.active ? 'stop' : 'start';
    sendCommand(m.cmd, { action: action });
    // оптимистичное переключение — статус с сервера подтвердит/откатит
    setPortedMode(key, !m.active);
    toast(m.labelOff + ': ' + (m.active ? 'запущен' : 'остановлен'));
  });
});

function setPortedMode(key, active) {
  var m = portedModes[key];
  if (!m) return;
  m.active = !!active;
  var btn = document.getElementById(m.btn);
  if (btn) {
    btn.classList.toggle('active', m.active);
    btn.textContent = m.active ? m.labelOn : m.labelOff;
  }
}

var btScanBtn = document.getElementById('bt-scan-btn');
var btAutoBtn = document.getElementById('bt-auto-btn');
var btDisconnectBtn = document.getElementById('bt-disconnect-btn');
var btDeviceList = document.getElementById('bt-device-list');
var btDevices = document.getElementById('bt-devices');
var btScanning = document.getElementById('bt-scanning');
var btSavedInfo = document.getElementById('bt-saved-info');

function btMsg(text, color) {
  // имя устройства приходит из эфира (его задаёт кто угодно рядом) — только textContent
  btDevices.textContent = '';
  var d = document.createElement('div');
  d.style.cssText = 'font-size:.75rem;padding:4px;color:' + (color || '#5f6368');
  d.textContent = text;
  btDevices.appendChild(d);
  btDeviceList.style.display = '';
}

function renderBtDevices(devs) {
  btDevices.textContent = '';
  devs.forEach(function(dev) {
    var item = document.createElement('div');
    item.className = 'bt-device-item' + (dev.is_gamepad ? ' bt-gamepad' : '');
    var label = document.createElement('span');
    label.className = 'bt-device-name';
    var tags = [];
    if (dev.connected) tags.push('connected'); else if (dev.paired) tags.push('paired');
    if (dev.rssi !== null && dev.rssi !== undefined) tags.push(dev.rssi + ' dBm');
    label.textContent = dev.name + ' (' + dev.mac + ')' + (tags.length ? ' \u00B7 ' + tags.join(', ') : '');
    var btn = document.createElement('button');
    btn.className = 'btn-sm btn-primary bt-connect-btn';
    btn.dataset.mac = dev.mac;
    btn.textContent = dev.connected ? 'Connected' : (dev.paired ? 'Connect' : 'Pair');
    btn.disabled = !!dev.connected;
    btn.addEventListener('click', function() {
      var label0 = btn.textContent;
      btn.textContent = '...';
      btn.disabled = true;
      if (!dev.paired) toast('Pairing: keep the controller in pairing mode (hold Share + PS)', 'info');
      fetch('/api/bt/connect', { method: 'POST', headers: authHeaders(),
        body: JSON.stringify({ mac: dev.mac, name: dev.name }) })
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if (d.ok) { toast('Connected!', 'success'); btn.textContent = 'Connected'; }
          else { toast(d.message || d.error || 'Connection failed', 'error'); btn.textContent = label0; btn.disabled = false; }
        })
        .catch(function() { toast('Connection error', 'error'); btn.textContent = label0; btn.disabled = false; });
    });
    item.appendChild(label);
    item.appendChild(btn);
    btDevices.appendChild(item);
  });
  btDeviceList.style.display = '';
}

btScanBtn.addEventListener('click', function() {
  if (btScanBtn.disabled) return;
  btScanBtn.disabled = true;
  btScanning.style.display = '';
  btDeviceList.style.display = 'none';
  btDevices.textContent = '';
  fetch('/api/bt/scan', { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      btScanning.style.display = 'none';
      btScanBtn.disabled = false;
      if (d.ok === false) {
        var hint = d.error || 'Scan failed';
        if (d.diagnose && d.diagnose.rfkill_blocked) hint += ' (Bluetooth is blocked by rfkill)';
        btMsg(hint, '#ea4335');
      } else if (d.devices && d.devices.length > 0) {
        renderBtDevices(d.devices);
      } else {
        btMsg('No devices found. Put the controller in pairing mode (hold Share + PS) and scan again.');
      }
    })
    .catch(function() {
      btScanning.style.display = 'none';
      btScanBtn.disabled = false;
      btMsg('Scan failed', '#ea4335');
    });
});

btAutoBtn.addEventListener('click', function() {
  fetch('/api/bt/auto_connect', { method: 'POST', headers: authHeaders() }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) toast('Auto-connect started', 'success');
    else toast(d.error || d.message || 'Auto-connect failed', 'error');
  }).catch(function() { toast('Auto-connect error', 'error'); });
});

btDisconnectBtn.addEventListener('click', function() {
  fetch('/api/bt/disconnect', { method: 'POST', headers: authHeaders() }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) toast('Disconnected', 'success');
    else toast(d.error || 'Disconnect failed', 'error');
  }).catch(function() { toast('Disconnect error', 'error'); });
});

fetch('/api/bt/status', { headers: (function(){ var h = {}; var t = getToken(); if (t) h['X-Auth-Token'] = t; return h; })() }).then(function(r) { return r.json(); }).then(function(d) {
  if (d.saved_mac && btSavedInfo) {
    btSavedInfo.textContent = 'Saved: ' + d.saved_mac;
  }
}).catch(function() {});

function getLogLevel(text) {
  if (!text) return 'debug';
  var t = text.toUpperCase();
  if (t.indexOf('| ERROR') !== -1 || t.indexOf('[ERROR]') !== -1) return 'error';
  if (t.indexOf('| WARNING') !== -1 || t.indexOf('| WARN') !== -1 || t.indexOf('[WARN') !== -1) return 'warn';
  if (t.indexOf('| DEBUG') !== -1 || t.indexOf('[DEBUG]') !== -1) return 'debug';
  if (t.indexOf('| INFO') !== -1 || t.indexOf('[INFO]') !== -1) return 'info';
  return 'info';
}

var MAX_CONSOLE_LINES = 300;
var CONSOLE_HISTORY_LINES = 250;

function appendConsoleLine(text, ts) {
  var output = document.getElementById('console-output');
  if (!output) return;
  var level = getLogLevel(text);
  logCounts[level] = (logCounts[level] || 0) + 1;
  // пока вкладка Console не активна — только счётчики: DOM консоли
  // на телефоне не растёт из-за фоновых логов прошивки
  var consoleTab = document.getElementById('content-console');
  if (consoleTab && consoleTab.classList.contains('active')) {
    var line = document.createElement('div');
    line.className = 'log-line log-' + level;
    line.dataset.level = level;
    if (ts) {
      var d = new Date(ts * 1000);
      text = d.toLocaleTimeString() + ' ' + text;
    }
    line.textContent = text;
    line.style.display = logFilters[level] ? '' : 'none';
    output.appendChild(line);
    while (output.children.length > MAX_CONSOLE_LINES) {
      output.removeChild(output.firstChild);
    }
    if (consoleAutoScroll) output.scrollTop = output.scrollHeight;
    var countEl = document.getElementById('console-line-count');
    if (countEl) countEl.textContent = output.children.length + ' lines';
  }
  updateLogCounters();
}

function updateLogCounters() {
  ['info', 'warn', 'error', 'debug'].forEach(function(level) {
    var el = document.getElementById('log-count-' + level);
    if (el) el.textContent = logCounts[level] || 0;
  });
}

function applyLogFilters() {
  var output = document.getElementById('console-output');
  if (!output) return;
  output.querySelectorAll('.log-line').forEach(function(line) {
    var level = line.dataset.level || 'info';
    line.style.display = logFilters[level] ? '' : 'none';
  });
}

function applyLogSort() {
  var output = document.getElementById('console-output');
  if (!output) return;
  var lines = Array.from(output.querySelectorAll('.log-line'));
  var levelOrder = { error: 0, warn: 1, info: 2, debug: 3 };
  if (logSortMode === 'level') {
    lines.sort(function(a, b) {
      var la = levelOrder[a.dataset.level] || 2;
      var lb = levelOrder[b.dataset.level] || 2;
      return la - lb;
    });
    lines.forEach(function(line) { output.appendChild(line); });
  }
}

document.querySelectorAll('.log-filter-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var level = btn.dataset.level;
    logFilters[level] = !logFilters[level];
    btn.classList.toggle('active', logFilters[level]);
    applyLogFilters();
  });
});

document.getElementById('log-sort-select').addEventListener('change', function() {
  logSortMode = this.value;
  applyLogSort();
});

document.getElementById('console-autoscroll').addEventListener('change', function() {
  consoleAutoScroll = this.checked;
});

document.getElementById('console-clear-btn').addEventListener('click', function() {
  var output = document.getElementById('console-output');
  output.innerHTML = '';
  logCounts = { info: 0, warn: 0, error: 0, debug: 0 };
  updateLogCounters();
  var countEl = document.getElementById('console-line-count');
  if (countEl) countEl.textContent = '0 lines';
  sendCommand('clear_log', {});
});


// ============================ Миссии ============================
var missionSel = document.getElementById('mission-select');
var missionState = {active: false, recording: false, has_path: false};

function refreshMissions() { sendCommand('mission', {action: 'list'}); }

function renderMissionList(list) {
  if (!missionSel) return;
  var prev = missionSel.value;
  missionSel.innerHTML = '';
  list.forEach(function(m) {
    var o = document.createElement('option');
    o.value = m.name;
    o.dataset.builtin = m.builtin ? '1' : '0';
    o.textContent = m.name + ' (' + m.steps + ' steps, ~' + m.sec + ' s)' + (m.builtin ? '' : ' *');
    missionSel.appendChild(o);
  });
  if (prev) missionSel.value = prev;
  updateMissionButtons();
}

function updateMissionButtons() {
  var st = missionState;
  var sel = missionSel && missionSel.options[missionSel.selectedIndex];
  document.getElementById('mission-start').disabled = st.active || st.recording || !sel;
  document.getElementById('mission-stop').disabled = !st.active;
  document.getElementById('mission-home').disabled = st.active || st.recording || !st.has_path;
  document.getElementById('mission-rec').textContent = st.recording ? 'Stop & save' : 'Record path';
  document.getElementById('mission-rec').dataset.rec = st.recording ? '1' : '0';
  document.getElementById('mission-del').style.display =
    (sel && sel.dataset.builtin === '0') ? '' : 'none';
}

function renderMissionStatus(s) {
  if (!s) return;
  ['active', 'recording', 'has_path'].forEach(function(k) {
    if (s[k] !== undefined) missionState[k] = !!s[k];
  });
  var el = document.getElementById('mission-status');
  if (el) {
    var txt = 'Idle', st = s.state || 'idle';
    if (missionState.recording) txt = 'Recording\u2026 drive the robot (' + (s.rec_events || 0) + ' moves)';
    else if (missionState.active) txt = 'Running ' + (s.name || '') + ': ' + (s.done || 0) + '/' + (s.total || 0);
    else if (st === 'done') txt = 'Finished: ' + (s.name || '');
    else if (st === 'stopped') txt = 'Stopped';
    else if (st === 'obstacle') txt = 'Stopped: obstacle ahead';
    else if (st === 'error') txt = 'Error: ' + (s.error || 'unknown');
    el.textContent = txt;
    el.dataset.state = missionState.active ? 'running'
      : (missionState.recording ? 'recording' : st);
  }
  updateMissionButtons();
}

document.getElementById('mission-start').addEventListener('click', function() {
  if (missionSel && missionSel.value) sendCommand('mission', {action: 'start', name: missionSel.value});
});
document.getElementById('mission-stop').addEventListener('click', function() {
  sendCommand('mission', {action: 'stop'});
});
document.getElementById('mission-home').addEventListener('click', function() {
  sendCommand('mission', {action: 'return_home'});
});
document.getElementById('mission-rec').addEventListener('click', function() {
  if (!missionState.recording) {
    sendCommand('mission', {action: 'record_start'});
  } else {
    var name = '';
    try { name = (window.prompt('Save recorded path as (leave empty to keep only for "Return home"):', '') || '').trim(); } catch (e) {}
    sendCommand('mission', {action: 'record_stop', save_as: name});
  }
});
document.getElementById('mission-del').addEventListener('click', function() {
  if (missionSel && missionSel.value &&
      (!window.confirm || window.confirm('Delete mission "' + missionSel.value + '"?'))) {
    sendCommand('mission', {action: 'delete', name: missionSel.value});
  }
});
if (missionSel) missionSel.addEventListener('change', updateMissionButtons);

// ============================ Обновление ============================
function updMsg(text, state) {
  var el = document.getElementById('upd-status');
  el.textContent = text;
  el.dataset.state = state || '';
}

function loadUpdateInfo() {
  return fetch('/api/update/info', {headers: authHeaders()})
    .then(function(r) { return r.json(); })
    .then(function(i) {
      if (!i || !i.ok) return i;
      document.getElementById('upd-version').textContent = 'v' + i.version;
      var sel = document.getElementById('upd-backups');
      sel.innerHTML = '';
      i.backups.forEach(function(b) {
        var o = document.createElement('option');
        o.value = b.name;
        o.textContent = b.name.replace(/^backup_|\.tar\.gz$/g, '') + ' (' + Math.round(b.size / 1024) + ' KB)';
        sel.appendChild(o);
      });
      document.getElementById('upd-rollback').disabled = !i.backups.length;
      if (i.pending) updMsg('New version is on probation: it will auto-roll back if it fails to start.', 'running');
      return i;
    })
    .catch(function() {});
}

// После установки сервис перезапускается: ждём возвращения и перезагружаем страницу
function waitForRestart(expectVersion) {
  updMsg('Installed. Restarting the robot\u2026 this page will reload.', 'running');
  var tries = 0;
  var t = setInterval(function() {
    if (++tries > 60) { clearInterval(t); updMsg('Robot did not come back in 2 minutes \u2014 check it manually.', 'error'); return; }
    fetch('/api/update/info', {headers: authHeaders(), cache: 'no-store'})
      .then(function(r) { return r.json(); })
      .then(function(i) {
        if (i && i.ok && (!expectVersion || i.version === expectVersion)) { clearInterval(t); location.reload(); }
      })
      .catch(function() {});
  }, 2000);
}

function updateResult(r) {
  if (!r || !r.ok) { updMsg('Failed: ' + ((r && r.error) || 'unknown error'), 'error'); return; }
  var note = (r.config_changed && r.config_changed.length)
    ? ' config.py replaced (old one saved as config_user_prev.py); changed defaults: ' + r.config_changed.slice(0, 6).join(', ') : '';
  if (r.restarting) { updMsg('Updated ' + r.from + ' \u2192 ' + r.to + '.' + note, 'running'); waitForRestart(r.to); }
  else updMsg('Updated ' + r.from + ' \u2192 ' + r.to + '. Restart the service manually.' + note, 'done');
  loadUpdateInfo();
}

// Кастомный выбор файла: показываем имя выбранного архива в псевдо-кнопке
var updFileInput = document.getElementById('upd-file');
if (updFileInput) {
  updFileInput.addEventListener('change', function() {
    var nameEl = document.getElementById('upd-file-name');
    if (nameEl) nameEl.textContent = this.files.length ? this.files[0].name : 'Choose .tar.gz / .zip…';
  });
}

document.getElementById('upd-go').addEventListener('click', function() {
  var f = document.getElementById('upd-file').files[0];
  if (!f) { updMsg('Choose an archive first.', 'error'); return; }
  if (window.confirm && !window.confirm('Install ' + f.name + '? The robot will stop and restart.')) return;
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/update');
  var h = authHeaders();
  Object.keys(h).forEach(function(k) { if (k.toLowerCase() !== 'content-type') xhr.setRequestHeader(k, h[k]); });
  xhr.upload.onprogress = function(e) {
    if (e.lengthComputable) updMsg('Uploading\u2026 ' + Math.round(e.loaded * 100 / e.total) + '%', 'running');
  };
  xhr.onload = function() {
    var r = null;
    try { r = JSON.parse(xhr.responseText); } catch (e) {}
    if (xhr.status === 401) { handleUnauthorized(); return; }
    if (xhr.status === 409) { updMsg('Another device is driving \u2014 press "Take control" first.', 'error'); return; }
    updateResult(r);
  };
  xhr.onerror = function() { updMsg('Upload failed (connection lost).', 'error'); };
  var fd = new FormData();
  fd.append('file', f);
  updMsg('Uploading\u2026', 'running');
  xhr.send(fd);
});

document.getElementById('upd-rollback').addEventListener('click', function() {
  var name = document.getElementById('upd-backups').value;
  if (!name || (window.confirm && !window.confirm('Roll back to ' + name + '?'))) return;
  fetch('/api/update/rollback', {method: 'POST', headers: authHeaders(), body: JSON.stringify({name: name})})
    .then(function(r) { return r.json(); })
    .then(updateResult)
    .catch(function() { updMsg('Rollback request failed.', 'error'); });
});

loadUpdateInfo();

buildServoGrid();
restoreCollapsed();
wsConnect();
fetchConsoleHistory();
updateVideoFeed();

// кнопки шапки
document.getElementById('estop-btn').addEventListener('click', emergencyStop);
[document.getElementById('take-control-btn'), document.getElementById('take-control-banner-btn')]
  .forEach(function(b) {
    if (b) b.addEventListener('click', function() { sendCommand('take_control', {}); setDriver(CLIENT_ID); });
  });
document.querySelectorAll('.tab-btn').forEach(function(b) {
  b.addEventListener('click', function() { setTimeout(reportView, 0); });
});
// высота липкой статус-панели нужна CSS (липкое видео на телефоне)
(function() {
  var sb = document.getElementById('status-bar');
  function setH() { document.documentElement.style.setProperty('--sb-h', sb.offsetHeight + 'px'); }
  setH();
  if (window.ResizeObserver) new ResizeObserver(setH).observe(sb);
  else window.addEventListener('resize', setH);
})();
