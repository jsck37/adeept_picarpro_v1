// ═══════════════════════════════════════════════════════════════
//  PiCar Pro — Front-end application
// ═══════════════════════════════════════════════════════════════

// ── Servo definitions (3 servos, crane disabled) ──
var servoDefs = [
  { id: 0, name: 'Steering', min: 30, max: 150, init: 90 },
  { id: 1, name: 'Cam Pan',  min: 0,  max: 180, init: 90 },
  { id: 2, name: 'Cam Tilt', min: 0,  max: 180, init: 90 },
];

// ── State ──
var hlLeft = false;
var hlRight = false;
var currentLedMode = 'off';
var lastSentDir = 'stop';
var moveThrottle = 0;

// Hardware availability
var hw = {
  motors: false, servos: false, leds: false, buzzer: false,
  switches: false, ultrasonic: false, mpu6050: false,
  oled: false, camera: false, autonomous: false,
};

// ═══════════════════════════════════════════════════════════════
//  TOAST
// ═══════════════════════════════════════════════════════════════
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

// ═══════════════════════════════════════════════════════════════
//  COLLAPSIBLE SECTIONS
// ═══════════════════════════════════════════════════════════════
document.querySelectorAll('.collapsible-header').forEach(function(header) {
  header.addEventListener('click', function() {
    var targetId = header.dataset.target;
    if (!targetId) return;
    var body = document.getElementById(targetId);
    if (!body) return;
    header.classList.toggle('open');
    body.classList.toggle('open');
  });
});

// ═══════════════════════════════════════════════════════════════
//  HARDWARE AVAILABILITY
// ═══════════════════════════════════════════════════════════════
function updateHardwareUI(hardwareStatus) {
  if (!hardwareStatus) return;
  hw = hardwareStatus;
  toggleHwSection('card-autonomous', 'auto-missing-tag', hw.autonomous);
  toggleHwSection(null, 'servo-missing-tag', hw.servos);
  toggleHwSection('card-headlights', 'hl-missing-tag', hw.switches);
  toggleHwSection('card-led', 'led-missing-tag', hw.leds);
  toggleHwSection('card-buzzer', 'buzzer-missing-tag', hw.buzzer);
  toggleHwSection(null, 'mpu-missing-tag', hw.mpu6050);
}

function toggleHwSection(cardId, tagId, available) {
  var tagEl = document.getElementById(tagId);
  if (!tagEl) return;
  if (available) {
    tagEl.style.display = 'none';
    if (cardId) {
      var card = document.getElementById(cardId);
      if (card) card.classList.remove('hw-missing');
    }
    // For cards without cardId (servo, mpu), find parent card
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

// ═══════════════════════════════════════════════════════════════
//  WEBSOCKET CONNECTION (port 8888)
// ═══════════════════════════════════════════════════════════════
var ws = null;
var wsReconnectTimer = null;
var usePolling = false;
var wsHost = location.hostname;
var wsPort = 8888;

function wsConnect() {
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  var url = 'ws://' + wsHost + ':' + wsPort;
  try {
    ws = new WebSocket(url);
    ws.onopen = function() {
      document.getElementById('connection-dot').classList.remove('offline');
      usePolling = false;
    };
    ws.onmessage = function(e) {
      try {
        var data = JSON.parse(e.data);
        var msgType = data.type || '';
        var msgData = data.data || {};
        if (msgType === 'status') updateStatus(msgData);
        else if (msgType === 'response' && msgData.error) toast(msgData.error, 'error');
      } catch(err) {}
    };
    ws.onclose = function() {
      document.getElementById('connection-dot').classList.add('offline');
      wsReconnectTimer = setTimeout(wsConnect, 3000);
    };
    ws.onerror = function() { ws.close(); };
  } catch(e) { usePolling = true; startPolling(); }
}

function sendCommand(cmd, params) {
  params = params || {};
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({cmd: cmd, params: params}));
  } else {
    var urlMap = {
      'move': '/cmd/move', 'speed': '/cmd/speed', 'servo': '/cmd/servo',
      'servo_home': '/cmd/servo_home', 'led': '/cmd/led', 'buzzer': '/cmd/buzzer',
      'switch': '/cmd/switch', 'cv_mode': '/cmd/cv_mode', 'auto': '/cmd/auto',
    };
    var url = urlMap[cmd];
    if (url) {
      fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(params) }).catch(function() {});
    }
  }
}

// ═══════════════════════════════════════════════════════════════
//  POLLING / SSE FALLBACK
// ═══════════════════════════════════════════════════════════════
var pollTimer = null;
function startPolling() {
  if (pollTimer) return;
  function poll() {
    fetch('/api/status').then(function(r) { return r.json(); }).then(function(d) {
      updateStatus(d); document.getElementById('connection-dot').classList.remove('offline');
    }).catch(function() { document.getElementById('connection-dot').classList.add('offline'); });
    pollTimer = setTimeout(poll, 1500);
  }
  poll();
}
function startSSE() {
  try {
    var source = new EventSource('/api/status/stream');
    source.onmessage = function(e) {
      try { var d = JSON.parse(e.data); updateStatus(d); document.getElementById('connection-dot').classList.remove('offline'); } catch(err) {}
    };
    source.onerror = function() { document.getElementById('connection-dot').classList.add('offline'); source.close(); startPolling(); };
  } catch(e) { startPolling(); }
}

// ═══════════════════════════════════════════════════════════════
//  STATUS UPDATE
// ═══════════════════════════════════════════════════════════════
var firstStatus = true;
function updateStatus(d) {
  if (!d) return;
  if (d.cpu_temp !== undefined) document.getElementById('sb-cpu-temp').textContent = d.cpu_temp + '\u00B0C';
  if (d.cpu_usage !== undefined) document.getElementById('sb-cpu-usage').textContent = d.cpu_usage + '%';
  if (d.ram_percent !== undefined) {
    var ramText;
    if (d.ram_used_mb !== undefined && d.ram_total_mb !== undefined) {
      ramText = d.ram_used_mb + '/' + d.ram_total_mb + 'M ' + d.ram_percent + '%';
    } else {
      ramText = d.ram_used + '/' + d.ram_total + 'G ' + d.ram_percent + '%';
    }
    document.getElementById('sb-ram').textContent = ramText;
  }
  if (d.distance !== undefined) document.getElementById('sb-distance').textContent = d.distance + 'cm';
  if (d.speed !== undefined) document.getElementById('sb-speed').textContent = d.speed + '%';
  document.getElementById('sb-module').textContent = d.running_module || 'Ready';
  if (d.hw) {
    updateHardwareUI(d.hw);
    if (firstStatus) { firstStatus = false; }
  }
  var mpu = d.mpu6050;
  if (mpu) {
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
}


// ═══════════════════════════════════════════════════════════════
//  CONNECT
// ═══════════════════════════════════════════════════════════════
wsConnect();
setTimeout(startSSE, 500);

// ═══════════════════════════════════════════════════════════════
//  TAB SWITCHING
// ═══════════════════════════════════════════════════════════════
document.querySelectorAll('.tab-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
    btn.classList.add('active');
    document.getElementById('content-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'modules') loadModules();
    if (btn.dataset.tab === 'info') loadDocs();
  });
});

// ═══════════════════════════════════════════════════════════════
//  CV MODE BUTTONS
// ═══════════════════════════════════════════════════════════════
document.querySelectorAll('.cv-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.cv-btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    var mode = btn.dataset.cv;
    var badge = document.getElementById('cv-badge');
    badge.textContent = 'CV: ' + mode.charAt(0).toUpperCase() + mode.slice(1);
    badge.classList.toggle('visible', mode !== 'none');
    sendCommand('cv_mode', { mode: mode });
  });
});

// ═══════════════════════════════════════════════════════════════
//  MOTOR SPEED SLIDER
// ═══════════════════════════════════════════════════════════════
var speedSlider = document.getElementById('speed-slider');
var speedVal = document.getElementById('speed-val');
speedSlider.addEventListener('input', function() { speedVal.textContent = speedSlider.value + '%'; });
speedSlider.addEventListener('change', function() { sendCommand('speed', { value: parseInt(speedSlider.value) }); });

// ═══════════════════════════════════════════════════════════════
//  WHEEL JOYSTICK (touch/mouse + WASD keyboard)
// ═══════════════════════════════════════════════════════════════
var joystickContainer = document.getElementById('joystick-container');
var joystickKnob = document.getElementById('joystick-knob');
var joystickLabel = document.getElementById('joystick-label');
var joystickDragging = false;
var joystickRafId = null;

function getJoystickCenter() {
  var rect = joystickContainer.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, r: rect.width / 2 - 24 };
}

function getDirection(dx, dy) {
  var angle = Math.atan2(-dy, dx) * 180 / Math.PI;
  var dist = Math.sqrt(dx * dx + dy * dy);
  if (dist < 10) return 'stop';
  if (angle > -22.5 && angle <= 22.5) return 'right';
  if (angle > 22.5 && angle <= 67.5) return 'forward_right';
  if (angle > 67.5 && angle <= 112.5) return 'forward';
  if (angle > 112.5 && angle <= 157.5) return 'forward_left';
  if (angle > 157.5 || angle <= -157.5) return 'left';
  if (angle > -157.5 && angle <= -112.5) return 'backward_left';
  if (angle > -112.5 && angle <= -67.5) return 'backward';
  if (angle > -67.5 && angle <= -22.5) return 'backward_right';
  return 'stop';
}

var dirLabels = {
  forward: 'Forward', backward: 'Backward', left: 'Left', right: 'Right',
  forward_left: 'Fwd-Left', forward_right: 'Fwd-Right',
  backward_left: 'Back-Left', backward_right: 'Back-Right', stop: 'Stopped'
};

function updateJoystick(clientX, clientY) {
  var center = getJoystickCenter();
  var dx = clientX - center.x;
  var dy = clientY - center.y;
  var dist = Math.sqrt(dx * dx + dy * dy);
  var maxR = center.r;
  if (dist > maxR) { dx = dx / dist * maxR; dy = dy / dist * maxR; }
  joystickKnob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
  var dir = getDirection(dx, dy);
  joystickLabel.textContent = dirLabels[dir] || dir;
  var now = Date.now();
  if (dir !== lastSentDir || now - moveThrottle > 150) {
    sendCommand('move', { dir: dir });
    lastSentDir = dir;
    moveThrottle = now;
  }
}

function moveKnobToDirection(dir) {
  var center = getJoystickCenter();
  var dist = center.r * 0.7;
  var dx = 0, dy = 0;
  switch (dir) {
    case 'forward':         dy = -dist; break;
    case 'backward':        dy = dist; break;
    case 'left':            dx = -dist; break;
    case 'right':           dx = dist; break;
    case 'forward_left':    dx = -dist * 0.7; dy = -dist * 0.7; break;
    case 'forward_right':   dx = dist * 0.7;  dy = -dist * 0.7; break;
    case 'backward_left':   dx = -dist * 0.7; dy = dist * 0.7;  break;
    case 'backward_right':  dx = dist * 0.7;  dy = dist * 0.7;  break;
    default: break;
  }
  joystickKnob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
  joystickKnob.classList.add('dragging');
}

function resetJoystick() {
  joystickKnob.classList.add('spring-back');
  joystickKnob.style.transform = 'translate(-50%, -50%)';
  joystickLabel.textContent = 'Wheels — WASD';
  sendCommand('move', { dir: 'stop' });
  lastSentDir = 'stop';
  setTimeout(function() { joystickKnob.classList.remove('spring-back'); joystickKnob.classList.remove('dragging'); }, 300);
}

joystickKnob.addEventListener('pointerdown', function(e) {
  e.preventDefault();
  joystickDragging = true;
  joystickKnob.classList.add('dragging');
  joystickKnob.setPointerCapture(e.pointerId);
});
document.addEventListener('pointermove', function(e) {
  if (!joystickDragging) return;
  if (joystickRafId) cancelAnimationFrame(joystickRafId);
  joystickRafId = requestAnimationFrame(function() { updateJoystick(e.clientX, e.clientY); });
});
document.addEventListener('pointerup', function() {
  if (!joystickDragging) return;
  joystickDragging = false;
  resetJoystick();
});
document.addEventListener('pointercancel', function() {
  if (!joystickDragging) return;
  joystickDragging = false;
  resetJoystick();
});

// ── WASD keyboard control — wheels only (using e.code for layout independence) ──
var keysDown = {};
var wasdTimer = null;

function wasdGetDirection() {
  var w = keysDown['w'];
  var a = keysDown['a'];
  var s = keysDown['s'];
  var d = keysDown['d'];
  if (w && a) return 'forward_left';
  if (w && d) return 'forward_right';
  if (s && a) return 'backward_left';
  if (s && d) return 'backward_right';
  if (w) return 'forward';
  if (s) return 'backward';
  if (a) return 'left';
  if (d) return 'right';
  return 'stop';
}

function wasdUpdate() {
  var dir = wasdGetDirection();
  if (dir !== lastSentDir) {
    moveKnobToDirection(dir);
    joystickLabel.textContent = dirLabels[dir] || dir;
    sendCommand('move', { dir: dir });
    lastSentDir = dir;
    moveThrottle = Date.now();
  }
  if (dir === 'stop') {
    resetJoystick();
  }
}

// ── Arrow keyboard control — camera pan/tilt ──
var arrowKeysDown = {};
var CAM_ARROW_STEP = 5;   // degrees per keypress
var CAM_ARROW_REPEAT = 80; // ms between repeats when held
var camArrowTimer = null;

function arrowCamUpdate() {
  var up    = arrowKeysDown['arrowup'];
  var down  = arrowKeysDown['arrowdown'];
  var left  = arrowKeysDown['arrowleft'];
  var right = arrowKeysDown['arrowright'];

  var newPan = camPanAngle;
  var newTilt = camTiltAngle;
  if (left)  newPan  = Math.max(0,  camPanAngle  - CAM_ARROW_STEP);
  if (right) newPan  = Math.min(180, camPanAngle  + CAM_ARROW_STEP);
  if (up)    newTilt = Math.max(0,  camTiltAngle - CAM_ARROW_STEP);
  if (down)  newTilt = Math.min(180, camTiltAngle + CAM_ARROW_STEP);

  if (newPan !== camPanAngle || newTilt !== camTiltAngle) {
    camPanAngle = newPan;
    camTiltAngle = newTilt;
    var now = Date.now();
    if (now - camThrottle > 60) {
      sendCommand('servo', { id: 1, angle: camPanAngle });
      sendCommand('servo', { id: 2, angle: camTiltAngle });
      camThrottle = now;
      // sync servo sliders
      var panSlider = servoGrid.querySelector('[data-servo="1"]');
      var tiltSlider = servoGrid.querySelector('[data-servo="2"]');
      if (panSlider) { panSlider.value = camPanAngle; document.getElementById('sv-1').textContent = camPanAngle + '\u00B0'; }
      if (tiltSlider) { tiltSlider.value = camTiltAngle; document.getElementById('sv-2').textContent = camTiltAngle + '\u00B0'; }
    }
    // move camera joystick knob visually
    moveCamKnobToAngles(camPanAngle, camTiltAngle);
    camJoystickLabel.textContent = 'Pan:' + camPanAngle + '\u00B0 Tilt:' + camTiltAngle + '\u00B0';
  }

  // if no arrow keys held, stop repeating
  if (!up && !down && !left && !right) {
    if (camArrowTimer) { clearInterval(camArrowTimer); camArrowTimer = null; }
  }
}

function moveCamKnobToAngles(pan, tilt) {
  var center = getCamJoystickCenter();
  var maxR = center.r;
  var dx = ((pan - 90) / 90) * maxR;
  var dy = ((tilt - 90) / 90) * maxR;
  camJoystickKnob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
  camJoystickKnob.classList.add('dragging');
}

// Combined keydown / keyup handlers
var WASD_CODES = ['keyw','keya','keys','keyd'];
var ARROW_CODES = ['arrowup','arrowdown','arrowleft','arrowright'];

document.addEventListener('keydown', function(e) {
  var code = e.code.toLowerCase();
  if (WASD_CODES.indexOf(code) === -1 && ARROW_CODES.indexOf(code) === -1) return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  e.preventDefault();

  // WASD → wheels
  if (WASD_CODES.indexOf(code) !== -1) {
    var key = code.replace('key', '');
    keysDown[key] = true;
    wasdUpdate();
  }

  // Arrow → camera
  if (ARROW_CODES.indexOf(code) !== -1) {
    if (!arrowKeysDown[code]) {
      arrowKeysDown[code] = true;
      arrowCamUpdate();
      if (!camArrowTimer) {
        camArrowTimer = setInterval(arrowCamUpdate, CAM_ARROW_REPEAT);
      }
    }
  }
});

document.addEventListener('keyup', function(e) {
  var code = e.code.toLowerCase();
  if (WASD_CODES.indexOf(code) === -1 && ARROW_CODES.indexOf(code) === -1) return;

  // WASD → wheels
  if (WASD_CODES.indexOf(code) !== -1) {
    var key = code.replace('key', '');
    delete keysDown[key];
    wasdUpdate();
  }

  // Arrow → camera
  if (ARROW_CODES.indexOf(code) !== -1) {
    delete arrowKeysDown[code];
    // if no arrows held anymore, reset camera joystick knob
    var anyArrow = arrowKeysDown['arrowup'] || arrowKeysDown['arrowdown'] || arrowKeysDown['arrowleft'] || arrowKeysDown['arrowright'];
    if (!anyArrow) {
      if (camArrowTimer) { clearInterval(camArrowTimer); camArrowTimer = null; }
      camJoystickKnob.classList.remove('dragging');
      camJoystickKnob.classList.add('spring-back');
      camJoystickKnob.style.transform = 'translate(-50%, -50%)';
      camJoystickLabel.textContent = 'Camera \u2014 Arrows';
      setTimeout(function() { camJoystickKnob.classList.remove('spring-back'); }, 300);
    }
  }
});

// ═══════════════════════════════════════════════════════════════
//  SERVO CONTROL (angle sliders + home)
// ═══════════════════════════════════════════════════════════════
var servoGrid = document.getElementById('servo-grid');
var servoValues = [];

servoDefs.forEach(function(sd) {
  servoValues.push(sd.init);
  var item = document.createElement('div');
  item.className = 'servo-item';
  item.innerHTML =
    '<label>' + sd.name + ' <span class="val" id="sv-' + sd.id + '">' + sd.init + '\u00B0</span></label>' +
    '<input type="range" min="' + sd.min + '" max="' + sd.max + '" value="' + sd.init + '" data-servo="' + sd.id + '">';
  servoGrid.appendChild(item);
});

servoGrid.addEventListener('input', function(e) {
  if (e.target.dataset.servo === undefined) return;
  var idx = parseInt(e.target.dataset.servo);
  var val = parseInt(e.target.value);
  servoValues[idx] = val;
  document.getElementById('sv-' + idx).textContent = val + '\u00B0';
});

servoGrid.addEventListener('change', function(e) {
  if (e.target.dataset.servo === undefined) return;
  var idx = parseInt(e.target.dataset.servo);
  sendCommand('servo', { id: idx, angle: servoValues[idx] });
});

document.getElementById('servo-home').addEventListener('click', function() {
  servoDefs.forEach(function(sd) {
    servoValues[sd.id] = sd.init;
    var slider = servoGrid.querySelector('[data-servo="' + sd.id + '"]');
    if (slider) slider.value = sd.init;
    document.getElementById('sv-' + sd.id).textContent = sd.init + '\u00B0';
  });
  sendCommand('servo_home', {});
});

// ═══════════════════════════════════════════════════════════════
//  CAMERA JOYSTICK (cam pan = servo 1, cam tilt = servo 2)
// ═══════════════════════════════════════════════════════════════
var camJoystickContainer = document.getElementById('cam-joystick-container');
var camJoystickKnob = document.getElementById('cam-joystick-knob');
var camJoystickLabel = document.getElementById('cam-joystick-label');
var camJoystickDragging = false;
var camJoystickRafId = null;
var camPanAngle = 90;
var camTiltAngle = 90;
var camThrottle = 0;

function getCamJoystickCenter() {
  var rect = camJoystickContainer.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, r: rect.width / 2 - 21 };
}

function updateCamJoystick(clientX, clientY) {
  var center = getCamJoystickCenter();
  var dx = clientX - center.x;
  var dy = clientY - center.y;
  var dist = Math.sqrt(dx * dx + dy * dy);
  var maxR = center.r;
  if (dist > maxR) { dx = dx / dist * maxR; dy = dy / dist * maxR; }
  camJoystickKnob.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px))';
  var panRange = 90;
  var tiltRange = 90;
  var newPan = Math.round(90 + (dx / maxR) * panRange);
  var newTilt = Math.round(90 + (dy / maxR) * tiltRange);
  newPan = Math.max(0, Math.min(180, newPan));
  newTilt = Math.max(0, Math.min(180, newTilt));
  camJoystickLabel.textContent = 'Pan:' + newPan + '\u00B0 Tilt:' + newTilt + '\u00B0';
  var now = Date.now();
  if ((newPan !== camPanAngle || newTilt !== camTiltAngle) && now - camThrottle > 100) {
    sendCommand('servo', { id: 1, angle: newPan });
    sendCommand('servo', { id: 2, angle: newTilt });
    camPanAngle = newPan;
    camTiltAngle = newTilt;
    camThrottle = now;
    var panSlider = servoGrid.querySelector('[data-servo="1"]');
    var tiltSlider = servoGrid.querySelector('[data-servo="2"]');
    if (panSlider) { panSlider.value = newPan; document.getElementById('sv-1').textContent = newPan + '\u00B0'; }
    if (tiltSlider) { tiltSlider.value = newTilt; document.getElementById('sv-2').textContent = newTilt + '\u00B0'; }
  }
}

function resetCamJoystick() {
  camJoystickKnob.classList.add('spring-back');
  camJoystickKnob.style.transform = 'translate(-50%, -50%)';
  camJoystickLabel.textContent = 'Camera \u2014 Arrows';
  setTimeout(function() { camJoystickKnob.classList.remove('spring-back'); camJoystickKnob.classList.remove('dragging'); }, 300);
}

camJoystickKnob.addEventListener('pointerdown', function(e) {
  e.preventDefault();
  camJoystickDragging = true;
  camJoystickKnob.classList.add('dragging');
  camJoystickKnob.setPointerCapture(e.pointerId);
});
document.addEventListener('pointermove', function(e) {
  if (!camJoystickDragging) return;
  if (camJoystickRafId) cancelAnimationFrame(camJoystickRafId);
  camJoystickRafId = requestAnimationFrame(function() { updateCamJoystick(e.clientX, e.clientY); });
});
document.addEventListener('pointerup', function() {
  if (!camJoystickDragging) return;
  camJoystickDragging = false;
  resetCamJoystick();
});
document.addEventListener('pointercancel', function() {
  if (!camJoystickDragging) return;
  camJoystickDragging = false;
  resetCamJoystick();
});

// ═══════════════════════════════════════════════════════════════
//  HEADLIGHTS
// ═══════════════════════════════════════════════════════════════
function updateHeadlightUI() {
  document.getElementById('hl-left').className = 'headlight-btn ' + (hlLeft ? 'on' : 'off');
  document.getElementById('hl-right').className = 'headlight-btn ' + (hlRight ? 'on' : 'off');
  document.getElementById('hl-both').className = 'headlight-btn ' + (hlLeft && hlRight ? 'on' : 'off');
}

document.getElementById('hl-left').addEventListener('click', function() {
  hlLeft = !hlLeft; sendCommand('switch', { id: 0, state: hlLeft }); updateHeadlightUI();
});
document.getElementById('hl-right').addEventListener('click', function() {
  hlRight = !hlRight; sendCommand('switch', { id: 1, state: hlRight }); updateHeadlightUI();
});
document.getElementById('hl-both').addEventListener('click', function() {
  var ns = !(hlLeft && hlRight); hlLeft = ns; hlRight = ns;
  sendCommand('switch', { id: 0, state: hlLeft }); sendCommand('switch', { id: 1, state: hlRight }); updateHeadlightUI();
});

// ═══════════════════════════════════════════════════════════════
//  LED STRIP
// ═══════════════════════════════════════════════════════════════
function hexToRgb(hex) {
  return [parseInt(hex.slice(1,3),16), parseInt(hex.slice(3,5),16), parseInt(hex.slice(5,7),16)];
}

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
  if (currentLedMode === 'solid' || currentLedMode === 'breath' || currentLedMode === 'colorWipe') {
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

// ═══════════════════════════════════════════════════════════════
//  BUZZER
// ═══════════════════════════════════════════════════════════════
document.querySelectorAll('#buzzer-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() { sendCommand('buzzer', { melody: btn.dataset.buzzer }); });
});

// ═══════════════════════════════════════════════════════════════
//  AUTONOMOUS
// ═══════════════════════════════════════════════════════════════
document.querySelectorAll('#auto-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#auto-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    if (btn.dataset.auto !== 'stop') btn.classList.add('active');
    sendCommand('auto', { func: btn.dataset.auto });
  });
});

// ═══════════════════════════════════════════════════════════════
//  MODULES
// ═══════════════════════════════════════════════════════════════
var currentRunningModule = null;

async function loadModules() {
  var grid = document.getElementById('modules-grid');
  grid.innerHTML = '<div style="color:#5f6368;font-size:.82rem">Loading...</div>';
  try {
    var res = await fetch('/api/modules');
    var data = await res.json();
    var modules = data.modules || [];
    var uploads = data.uploads || [];
    currentRunningModule = data.running;
    var all = modules.concat(uploads);
    if (all.length === 0) { grid.innerHTML = '<div style="color:#5f6368;font-size:.82rem">No modules found</div>'; return; }
    grid.innerHTML = '';
    all.forEach(function(mod) {
      var isRunning = currentRunningModule === mod.id || currentRunningModule === mod.name;
      var card = document.createElement('div');
      card.className = 'module-card' + (isRunning ? ' running' : '');
      var tags = (mod.hardware || []).map(function(h) { return '<span class="module-tag">' + h + '</span>'; }).join('');
      card.innerHTML =
        '<div class="module-header">' +
          '<div class="module-icon">' + (mod.icon || '\u2699') + '</div>' +
          '<div class="module-info"><h3>' + (mod.name || mod.id) + '</h3>' +
          '<p>' + (mod.desc || '') + '</p></div></div>' +
        (tags ? '<div class="module-tags">' + tags + '</div>' : '') +
        '<div class="module-actions">' +
          (isRunning
            ? '<button class="btn-sm btn-danger" data-mod-stop="1">Stop</button><span style="font-size:.75rem;color:#34a853;font-weight:600">Running</span>'
            : '<button class="btn-sm btn-primary" data-mod-run="' + mod.id + '">Run</button>') +
        '</div>';
      grid.appendChild(card);
    });
    grid.querySelectorAll('[data-mod-run]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var mid = btn.dataset.modRun;
        sendCommand('module_start', { id: mid });
        fetch('/api/modules/start', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id: mid }) })
          .then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) { toast('Started: ' + mid, 'success'); setTimeout(loadModules, 500); }
            else { toast('Error: ' + (d.message || 'Failed'), 'error'); }
          }).catch(function() { toast('Started: ' + mid, 'success'); setTimeout(loadModules, 500); });
      });
    });
    grid.querySelectorAll('[data-mod-stop]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        sendCommand('module_stop', {});
        fetch('/api/modules/stop', { method: 'POST' }).then(function() { toast('Stopped', 'success'); setTimeout(loadModules, 500); });
      });
    });
  } catch(e) { grid.innerHTML = '<div style="color:#ea4335;font-size:.82rem">Error loading modules</div>'; }
}

// ═══════════════════════════════════════════════════════════════
//  FILE UPLOAD
// ═══════════════════════════════════════════════════════════════
var uploadArea = document.getElementById('upload-area');
var fileInput = document.getElementById('file-input');
var uploadBtn = document.getElementById('upload-btn');
var uploadProgress = document.getElementById('upload-progress');
var uploadProgressBar = document.getElementById('upload-progress-bar');

uploadArea.addEventListener('click', function() { fileInput.click(); });
uploadArea.addEventListener('dragover', function(e) { e.preventDefault(); uploadArea.classList.add('drag-over'); });
uploadArea.addEventListener('dragleave', function() { uploadArea.classList.remove('drag-over'); });
uploadArea.addEventListener('drop', function(e) { e.preventDefault(); uploadArea.classList.remove('drag-over'); if (e.dataTransfer.files.length > 0) uploadFiles(e.dataTransfer.files); });
uploadBtn.addEventListener('click', function(e) { e.stopPropagation(); if (fileInput.files.length > 0) uploadFiles(fileInput.files); });

function uploadFiles(files) {
  uploadProgress.classList.add('visible');
  var completed = 0, total = files.length;
  for (var i = 0; i < files.length; i++) {
    (function(file) {
      var fd = new FormData(); fd.append('file', file);
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/modules/upload');
      xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) uploadProgressBar.style.width = Math.round((completed / total) * 100 + (e.loaded / e.total) * (100 / total)) + '%';
      };
      xhr.onload = function() {
        completed++;
        if (completed === total) { uploadProgressBar.style.width = '100%'; setTimeout(function() { uploadProgress.classList.remove('visible'); uploadProgressBar.style.width = '0%'; toast('Upload complete!', 'success'); loadModules(); }, 500); }
      };
      xhr.onerror = function() { toast('Upload failed', 'error'); };
      xhr.send(fd);
    })(files[i]);
  }
}

// ═══════════════════════════════════════════════════════════════
//  INFO TAB — Documentation viewer
// ═══════════════════════════════════════════════════════════════
var docsData = null;
var docsLoaded = false;
var currentDocPage = 'overview';

async function loadDocs() {
  if (docsLoaded) return;
  var main = document.getElementById('info-main');
  main.innerHTML = '<div class="info-loading">Loading documentation...</div>';

  try {
    // Load all docs in parallel
    var [indexRes, pinoutRes] = await Promise.all([
      fetch('/docs/index.json').then(function(r) { return r.json(); }),
      fetch('/docs/pinout.json').then(function(r) { return r.json(); })
    ]);

    // Load component docs
    var compFetches = (indexRes.components || []).map(function(c) {
      if (c.documentation_path) {
        return fetch('/docs/' + c.documentation_path)
          .then(function(r) { return r.json(); })
          .then(function(d) { return { id: c.id, data: d }; })
          .catch(function() { return { id: c.id, data: null }; });
      }
      return Promise.resolve({ id: c.id, data: null });
    });
    var compResults = await Promise.all(compFetches);
    var compMap = {};
    compResults.forEach(function(r) { if (r.data) compMap[r.id] = r.data; });

    docsData = { index: indexRes, pinout: pinoutRes, components: compMap };
    docsLoaded = true;

    // Build sidebar nav
    buildInfoNav(indexRes);
    renderDocPage('overview');

  } catch(e) {
    main.innerHTML = '<div class="info-loading" style="color:#ea4335">Error loading documentation: ' + e.message + '</div>';
  }
}

function buildInfoNav(indexData) {
  var compNav = document.getElementById('info-component-nav');
  compNav.innerHTML = '';
  (indexData.components || []).forEach(function(c) {
    var btn = document.createElement('button');
    btn.className = 'info-comp-btn';
    btn.dataset.doc = 'comp_' + c.id;
    btn.textContent = c.id.toUpperCase();
    btn.title = c.name || c.id;
    btn.addEventListener('click', function() {
      setActiveDocNav(btn);
      renderDocPage('comp_' + c.id);
    });
    compNav.appendChild(btn);
  });

  // Wire overview and pinout buttons
  document.querySelectorAll('.info-nav-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      setActiveDocNav(btn);
      renderDocPage(btn.dataset.doc);
    });
  });
}

function setActiveDocNav(activeBtn) {
  document.querySelectorAll('.info-nav-btn, .info-comp-btn').forEach(function(b) { b.classList.remove('active'); });
  activeBtn.classList.add('active');
}

function renderDocPage(page) {
  currentDocPage = page;
  var main = document.getElementById('info-main');
  var html = '';

  if (page === 'overview') {
    html = renderOverview();
  } else if (page === 'pinout') {
    html = renderPinout();
  } else if (page.startsWith('comp_')) {
    var compId = page.replace('comp_', '');
    html = renderComponent(compId);
  }

  main.innerHTML = html;
  main.scrollTop = 0;
}

function renderOverview() {
  var d = docsData.index;
  var html = '<div class="info-title">' + esc(d.project) + ' v' + esc(d.version) + '</div>';
  html += '<div class="info-subtitle">' + esc(d.description) + '</div>';

  // Board info
  html += '<div class="info-section">';
  html += '<div class="info-section-title">Board Information</div>';
  html += '<div class="info-field"><span class="info-field-label">Board</span><span class="info-field-value">' + esc(d.board) + '</span></div>';
  html += '<div class="info-field"><span class="info-field-label">Generated</span><span class="info-field-value">' + esc(d.generated) + '</span></div>';
  html += '</div>';

  // Components
  html += '<div class="info-section">';
  html += '<div class="info-section-title">Components (' + d.components.length + ')</div>';
  d.components.forEach(function(c) {
    html += '<div class="info-field" style="margin-bottom:10px;padding:8px 12px;background:#f8f9fa;border-radius:6px">';
    html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">';
    html += '<strong style="color:#1a73e8;font-size:.9rem">' + esc(c.id.toUpperCase()) + '</strong>';
    html += '<span style="font-size:.82rem;color:#202124">' + esc(c.name) + '</span>';
    html += '</div>';
    html += '<div style="font-size:.8rem;color:#5f6368">' + esc(c.description) + '</div>';
    if (c.i2c_address) {
      html += '<div style="font-size:.75rem;color:#5f6368;margin-top:3px">I2C: <code style="background:#e8f0fe;padding:1px 5px;border-radius:3px">' + esc(c.i2c_address) + '</code></div>';
    }
    if (c.pins_used && c.pins_used.length > 0) {
      html += '<div style="font-size:.75rem;color:#5f6368;margin-top:2px">Pins: ' + c.pins_used.map(function(p) { return '<code style="background:#f1f3f4;padding:1px 4px;border-radius:3px">' + esc(p) + '</code>'; }).join(' ') + '</div>';
    }
    if (c.datasheet_url) {
      html += '<div style="font-size:.75rem;margin-top:3px"><a href="' + esc(c.datasheet_url) + '" target="_blank" rel="noopener">Datasheet</a></div>';
    }
    html += '</div>';
  });
  html += '</div>';

  // I2C Bus Summary
  if (d.i2c_bus_summary) {
    var bus = d.i2c_bus_summary;
    html += '<div class="info-section">';
    html += '<div class="info-section-title">I2C Bus Summary</div>';
    html += '<div class="info-field"><span class="info-field-label">Bus</span><span class="info-field-value">' + esc(String(bus.bus)) + '</span></div>';
    html += '<div class="info-field"><span class="info-field-label">SDA Pin</span><span class="info-field-value">GPIO ' + esc(String(bus.sda_pin)) + '</span></div>';
    html += '<div class="info-field"><span class="info-field-label">SCL Pin</span><span class="info-field-value">GPIO ' + esc(String(bus.scl_pin)) + '</span></div>';
    html += '<div class="i2c-device-grid" style="margin-top:8px">';
    (bus.devices || []).forEach(function(dev) {
      html += '<div class="i2c-device"><span class="i2c-device-addr">' + esc(dev.address) + '</span> <span class="i2c-device-name">' + esc(dev.name) + '</span></div>';
    });
    html += '</div></div>';
  }

  // Additional Hardware
  if (d.additional_hardware && d.additional_hardware.length > 0) {
    html += '<div class="info-section">';
    html += '<div class="info-section-title">Additional Hardware</div>';
    d.additional_hardware.forEach(function(h) {
      html += '<div class="info-field" style="margin-bottom:8px;padding:6px 12px;background:#f8f9fa;border-radius:6px">';
      html += '<strong style="color:#1a73e8">' + esc(h.id.toUpperCase()) + '</strong> — ' + esc(h.name);
      html += '<div style="font-size:.8rem;color:#5f6368;margin-top:2px">' + esc(h.description) + '</div>';
      html += '</div>';
    });
    html += '</div>';
  }

  return html;
}

function renderPinout() {
  var d = docsData.pinout;
  var colors = d.color_categories || {};
  var html = '<div class="info-title">' + esc(d.description) + '</div>';
  html += '<div class="info-subtitle">' + esc(d.board) + ' | SoC: ' + esc(d.soc) + ' | Rev ' + esc(d.revision) + '</div>';

  html += '<div class="info-section">';
  html += '<div class="info-section-title">GPIO Pinout</div>';
  html += '<table class="pin-table"><thead><tr><th>Pin</th><th>GPIO</th><th>Function</th><th>Name</th><th>Module</th></tr></thead><tbody>';
  (d.pins || []).forEach(function(p) {
    var color = colors[p.color_category] || '#5f6368';
    html += '<tr>';
    html += '<td>' + esc(String(p.pin)) + '</td>';
    html += '<td>' + (p.gpio !== null ? '<span class="pin-color" style="background:' + color + '"></span>' + esc(String(p.gpio)) : '-') + '</td>';
    html += '<td>' + esc(p.function || '') + '</td>';
    html += '<td>' + esc(p.name || '') + '</td>';
    html += '<td>' + esc(p.module || '-') + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table></div>';

  // Pin conflicts
  if (d.pin_conflicts && d.pin_conflicts.length > 0) {
    html += '<div class="info-section">';
    html += '<div class="info-section-title">Pin Conflicts</div>';
    d.pin_conflicts.forEach(function(c) {
      html += '<div style="margin-bottom:10px;padding:8px 12px;background:#fef7e0;border-radius:6px;font-size:.84rem">';
      html += '<div style="font-weight:600;color:#b06000">GPIO ' + esc(String(c.gpio)) + ' (Pin ' + esc(String(c.pin)) + ')</div>';
      html += '<div style="color:#5f6368;margin-top:2px">' + esc(c.conflict) + '</div>';
      html += '<div style="color:#137333;margin-top:3px;font-weight:500">Resolution: ' + esc(c.resolution) + '</div>';
      html += '</div>';
    });
    html += '</div>';
  }

  // I2C Bus
  if (d.i2c_bus) {
    var bus = d.i2c_bus;
    html += '<div class="info-section">';
    html += '<div class="info-section-title">I2C Bus</div>';
    html += '<div class="i2c-device-grid">';
    (bus.devices || []).forEach(function(dev) {
      html += '<div class="i2c-device"><span class="i2c-device-addr">' + esc(dev.address) + '</span> <span class="i2c-device-name">' + esc(dev.name) + '</span></div>';
    });
    html += '</div></div>';
  }

  // Legend
  html += '<div class="info-section">';
  html += '<div class="info-section-title">Color Legend</div>';
  html += '<div style="display:flex;flex-wrap:wrap;gap:12px">';
  Object.keys(colors).forEach(function(cat) {
    html += '<div style="display:flex;align-items:center;gap:5px;font-size:.82rem"><span class="pin-color" style="background:' + colors[cat] + '"></span>' + esc(cat) + '</div>';
  });
  html += '</div></div>';

  return html;
}

function renderComponent(compId) {
  var c = docsData.components[compId];
  if (!c) return '<div class="info-loading">Documentation not available for ' + esc(compId) + '</div>';

  var html = '<div class="info-title">' + esc(c.name || compId) + '</div>';
  html += '<div class="info-subtitle">' + esc(c.description || '') + '</div>';

  // Chip info
  html += '<div class="info-section">';
  html += '<div class="info-section-title">General</div>';
  if (c.chip) html += '<div class="info-field"><span class="info-field-label">Chip</span><span class="info-field-value">' + esc(c.chip) + '</span></div>';
  if (c.manufacturer) html += '<div class="info-field"><span class="info-field-label">Manufacturer</span><span class="info-field-value">' + esc(c.manufacturer) + '</span></div>';
  if (c.i2c_address) html += '<div class="info-field"><span class="info-field-label">I2C Address</span><span class="info-field-value"><code style="background:#e8f0fe;padding:1px 5px;border-radius:3px">' + esc(c.i2c_address) + '</code></span></div>';
  if (c.i2c_address_alternates && c.i2c_address_alternates.length > 0) {
    html += '<div class="info-field"><span class="info-field-label">Alt Addresses</span><span class="info-field-value">' + c.i2c_address_alternates.map(function(a) { return '<code style="background:#f1f3f4;padding:1px 4px;border-radius:3px">' + esc(a) + '</code>'; }).join(' ') + '</span></div>';
  }
  if (c.datasheet_url) html += '<div class="info-field"><span class="info-field-label">Datasheet</span><span class="info-field-value"><a href="' + esc(c.datasheet_url) + '" target="_blank" rel="noopener">' + esc(c.datasheet_url) + '</a></span></div>';
  if (c.related_modules) html += '<div class="info-field"><span class="info-field-label">Related Modules</span><span class="info-field-value">' + c.related_modules.map(function(m) { return '<code style="background:#e8f0fe;padding:1px 4px;border-radius:3px">' + esc(m) + '</code>'; }).join(' ') + '</span></div>';
  html += '</div>';

  // Specs
  if (c.specs) {
    html += '<div class="info-section">';
    html += '<div class="info-section-title">Specifications</div>';
    html += '<div class="specs-grid">';
    Object.keys(c.specs).forEach(function(key) {
      var label = key.replace(/_/g, ' ').replace(/\b\w/g, function(l) { return l.toUpperCase(); });
      html += '<div class="spec-item"><span class="spec-key">' + esc(label) + '</span><span class="spec-val">' + esc(String(c.specs[key])) + '</span></div>';
    });
    html += '</div></div>';
  }

  // Pins
  if (c.pins && c.pins.length > 0) {
    html += '<div class="info-section">';
    html += '<div class="info-section-title">Pin Connections</div>';
    html += '<div class="comp-pins-list">';
    c.pins.forEach(function(p) {
      html += '<div class="comp-pin">';
      html += '<div class="comp-pin-name">' + esc(p.pin_name) + '</div>';
      if (p.connected_to) html += '<div class="comp-pin-conn">' + esc(p.connected_to) + '</div>';
      if (p.function) html += '<div class="comp-pin-func">' + esc(p.function) + '</div>';
      html += '</div>';
    });
    html += '</div></div>';
  }

  // Tips
  var tips = c.tips || [];
  if (tips.length > 0) {
    html += '<div class="info-section">';
    html += '<div class="info-section-title">Tips</div>';
    html += '<ul class="tips-list">';
    tips.forEach(function(t) {
      html += '<li>' + esc(t) + '</li>';
    });
    html += '</ul></div>';
  }

  return html;
}

function esc(str) {
  if (str === null || str === undefined) return '';
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}
