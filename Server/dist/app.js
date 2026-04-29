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

// Hardware availability (updated from server status)
var hwAvailable = {
  headlights: true,
  leds: true,
  buzzer: true,
  mpu6050: true,
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
        if (msgType === 'status') {
          updateStatus(msgData);
        } else if (msgType === 'response') {
          if (msgData.error) toast(msgData.error, 'error');
        }
      } catch(err) {}
    };
    ws.onclose = function() {
      document.getElementById('connection-dot').classList.add('offline');
      wsReconnectTimer = setTimeout(wsConnect, 3000);
    };
    ws.onerror = function() { ws.close(); };
  } catch(e) {
    usePolling = true;
    startPolling();
  }
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
      fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(params)
      }).catch(function() {});
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
      updateStatus(d);
      document.getElementById('connection-dot').classList.remove('offline');
    }).catch(function() {
      document.getElementById('connection-dot').classList.add('offline');
    });
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
    source.onerror = function() {
      document.getElementById('connection-dot').classList.add('offline');
      source.close();
      startPolling();
    };
  } catch(e) { startPolling(); }
}

// ═══════════════════════════════════════════════════════════════
//  STATUS UPDATE
// ═══════════════════════════════════════════════════════════════
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
  if (d.running_module) {
    document.getElementById('sb-module').textContent = d.running_module;
  } else {
    document.getElementById('sb-module').textContent = 'Ready';
  }

  // MPU6050 IMU data
  var mpu = d.mpu6050;
  if (mpu) {
    hwAvailable.mpu6050 = true;
    document.getElementById('sb-imu').textContent = 'R:' + mpu.roll + '\u00B0 P:' + mpu.pitch + '\u00B0';
    document.getElementById('mpu-status').textContent = 'Connected';
    document.getElementById('mpu-status').className = 'hw-status connected';
    document.getElementById('mpu-ax').textContent = mpu.accel.x.toFixed(3);
    document.getElementById('mpu-ay').textContent = mpu.accel.y.toFixed(3);
    document.getElementById('mpu-az').textContent = mpu.accel.z.toFixed(3);
    document.getElementById('mpu-gx').textContent = mpu.gyro.x.toFixed(1);
    document.getElementById('mpu-gy').textContent = mpu.gyro.y.toFixed(1);
    document.getElementById('mpu-gz').textContent = mpu.gyro.z.toFixed(1);
    document.getElementById('mpu-roll').textContent = mpu.roll.toFixed(1);
    document.getElementById('mpu-pitch').textContent = mpu.pitch.toFixed(1);
    drawTiltIndicator(mpu.roll, mpu.pitch);
  } else {
    document.getElementById('sb-imu').textContent = 'N/A';
    document.getElementById('mpu-status').textContent = 'MPU6050 not connected';
    document.getElementById('mpu-status').className = 'hw-status not-connected';
  }
}

// ═══════════════════════════════════════════════════════════════
//  MPU6050 TILT INDICATOR
// ═══════════════════════════════════════════════════════════════
var tiltCanvas = document.getElementById('mpu-tilt-canvas');
var tiltCtx = tiltCanvas ? tiltCanvas.getContext('2d') : null;

function drawTiltIndicator(roll, pitch) {
  if (!tiltCtx) return;
  var w = tiltCanvas.width, h = tiltCanvas.height;
  var cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 8;
  tiltCtx.clearRect(0, 0, w, h);
  // Outer circle
  tiltCtx.beginPath(); tiltCtx.arc(cx, cy, r, 0, Math.PI * 2);
  tiltCtx.fillStyle = '#f8f9fa'; tiltCtx.fill();
  tiltCtx.strokeStyle = '#dadce0'; tiltCtx.lineWidth = 2; tiltCtx.stroke();
  // Crosshair
  tiltCtx.beginPath();
  tiltCtx.moveTo(cx - r, cy); tiltCtx.lineTo(cx + r, cy);
  tiltCtx.moveTo(cx, cy - r); tiltCtx.lineTo(cx, cy + r);
  tiltCtx.strokeStyle = '#e0e0e0'; tiltCtx.lineWidth = 1; tiltCtx.stroke();
  // Level zone
  tiltCtx.beginPath(); tiltCtx.arc(cx, cy, r * 0.3, 0, Math.PI * 2);
  tiltCtx.strokeStyle = '#34a853'; tiltCtx.lineWidth = 1; tiltCtx.stroke();
  // Dot
  var dotX = cx + (roll / 90) * r;
  var dotY = cy - (pitch / 90) * r;
  var dx = dotX - cx, dy = dotY - cy;
  var dist = Math.sqrt(dx * dx + dy * dy);
  if (dist > r - 6) { dotX = cx + dx / dist * (r - 6); dotY = cy + dy / dist * (r - 6); }
  tiltCtx.beginPath(); tiltCtx.moveTo(cx, cy); tiltCtx.lineTo(dotX, dotY);
  tiltCtx.strokeStyle = '#1a73e8'; tiltCtx.lineWidth = 2; tiltCtx.stroke();
  tiltCtx.beginPath(); tiltCtx.arc(dotX, dotY, 5, 0, Math.PI * 2);
  tiltCtx.fillStyle = '#1a73e8'; tiltCtx.fill();
  tiltCtx.strokeStyle = '#fff'; tiltCtx.lineWidth = 2; tiltCtx.stroke();
  tiltCtx.beginPath(); tiltCtx.arc(cx, cy, 3, 0, Math.PI * 2);
  tiltCtx.fillStyle = '#5f6368'; tiltCtx.fill();
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
speedSlider.addEventListener('input', function() {
  speedVal.textContent = speedSlider.value + '%';
});
speedSlider.addEventListener('change', function() {
  sendCommand('speed', { value: parseInt(speedSlider.value) });
});

// ═══════════════════════════════════════════════════════════════
//  SERVO SPEED SLIDERS (separate speed per servo)
// ═══════════════════════════════════════════════════════════════
var servoSpeedGrid = document.getElementById('servo-speed-grid');
servoDefs.forEach(function(sd) {
  var row = document.createElement('div');
  row.className = 'slider-row';
  row.innerHTML =
    '<label>' + sd.name + '</label>' +
    '<input type="range" min="' + sd.min + '" max="' + sd.max + '" value="' + sd.init + '" data-servo-speed="' + sd.id + '">' +
    '<span class="val" id="sv-speed-' + sd.id + '">' + sd.init + '\u00B0</span>';
  servoSpeedGrid.appendChild(row);
});

servoSpeedGrid.addEventListener('input', function(e) {
  if (e.target.dataset.servoSpeed === undefined) return;
  var idx = parseInt(e.target.dataset.servoSpeed);
  document.getElementById('sv-speed-' + idx).textContent = e.target.value + '\u00B0';
});

servoSpeedGrid.addEventListener('change', function(e) {
  if (e.target.dataset.servoSpeed === undefined) return;
  var idx = parseInt(e.target.dataset.servoSpeed);
  var val = parseInt(e.target.value);
  sendCommand('servo', { id: idx, angle: val });
});

// ═══════════════════════════════════════════════════════════════
//  JOYSTICK
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

function resetJoystick() {
  joystickKnob.classList.add('spring-back');
  joystickKnob.style.transform = 'translate(-50%, -50%)';
  joystickLabel.textContent = 'Drag knob to move';
  sendCommand('move', { dir: 'stop' });
  lastSentDir = 'stop';
  setTimeout(function() { joystickKnob.classList.remove('spring-back'); }, 300);
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
  joystickKnob.classList.remove('dragging');
  resetJoystick();
});

document.addEventListener('pointercancel', function() {
  if (!joystickDragging) return;
  joystickDragging = false;
  joystickKnob.classList.remove('dragging');
  resetJoystick();
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
    // Also update the speed slider
    var speedSlider = servoSpeedGrid.querySelector('[data-servo-speed="' + sd.id + '"]');
    if (speedSlider) speedSlider.value = sd.init;
    document.getElementById('sv-speed-' + sd.id).textContent = sd.init + '\u00B0';
  });
  sendCommand('servo_home', {});
});

// ═══════════════════════════════════════════════════════════════
//  HEADLIGHTS
// ═══════════════════════════════════════════════════════════════
function updateHeadlightUI() {
  var leftBtn = document.getElementById('hl-left');
  var rightBtn = document.getElementById('hl-right');
  var bothBtn = document.getElementById('hl-both');
  leftBtn.className = 'headlight-btn ' + (hlLeft ? 'on' : 'off');
  rightBtn.className = 'headlight-btn ' + (hlRight ? 'on' : 'off');
  bothBtn.className = 'headlight-btn ' + (hlLeft && hlRight ? 'on' : 'off');
}

document.getElementById('hl-left').addEventListener('click', function() {
  hlLeft = !hlLeft;
  sendCommand('switch', { id: 1, state: hlLeft });
  updateHeadlightUI();
});

document.getElementById('hl-right').addEventListener('click', function() {
  hlRight = !hlRight;
  sendCommand('switch', { id: 2, state: hlRight });
  updateHeadlightUI();
});

document.getElementById('hl-both').addEventListener('click', function() {
  var newState = !(hlLeft && hlRight);
  hlLeft = newState;
  hlRight = newState;
  sendCommand('switch', { id: 1, state: hlLeft });
  sendCommand('switch', { id: 2, state: hlRight });
  updateHeadlightUI();
});

// ═══════════════════════════════════════════════════════════════
//  LED STRIP
// ═══════════════════════════════════════════════════════════════
function hexToRgb(hex) {
  var r = parseInt(hex.slice(1,3), 16);
  var g = parseInt(hex.slice(3,5), 16);
  var b = parseInt(hex.slice(5,7), 16);
  return [r, g, b];
}

var ledColorInput = document.getElementById('led-color');

document.querySelectorAll('.color-preset').forEach(function(btn) {
  btn.addEventListener('click', function() {
    ledColorInput.value = btn.dataset.hex;
    if (currentLedMode !== 'off' && currentLedMode !== 'rainbow' && currentLedMode !== 'police') {
      var rgb = hexToRgb(ledColorInput.value);
      sendCommand('led', { mode: currentLedMode, color: rgb });
    }
  });
});

ledColorInput.addEventListener('input', function() {
  if (currentLedMode === 'solid' || currentLedMode === 'breath' || currentLedMode === 'colorWipe') {
    var rgb = hexToRgb(ledColorInput.value);
    sendCommand('led', { mode: currentLedMode, color: rgb });
  }
});

ledColorInput.addEventListener('change', function() {
  if (currentLedMode !== 'off' && currentLedMode !== 'rainbow' && currentLedMode !== 'police') {
    var rgb = hexToRgb(ledColorInput.value);
    sendCommand('led', { mode: currentLedMode, color: rgb });
  }
});

document.querySelectorAll('#led-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#led-group .gbtn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    currentLedMode = btn.dataset.led;
    var rgb = hexToRgb(ledColorInput.value);
    sendCommand('led', { mode: currentLedMode, color: rgb });
  });
});

// ═══════════════════════════════════════════════════════════════
//  BUZZER
// ═══════════════════════════════════════════════════════════════
document.querySelectorAll('#buzzer-group .gbtn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    sendCommand('buzzer', { melody: btn.dataset.buzzer });
  });
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

    if (all.length === 0) {
      grid.innerHTML = '<div style="color:#5f6368;font-size:.82rem">No modules found</div>';
      return;
    }

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
          '<p>' + (mod.desc || '') + '</p></div>' +
        '</div>' +
        (tags ? '<div class="module-tags">' + tags + '</div>' : '') +
        '<div class="module-actions">' +
          (isRunning
            ? '<button class="btn-sm btn-danger" data-mod-stop="1">Stop</button>' +
              '<span style="font-size:.75rem;color:#34a853;font-weight:600">Running</span>'
            : '<button class="btn-sm btn-primary" data-mod-run="' + mod.id + '">Run</button>'
          ) +
        '</div>';
      grid.appendChild(card);
    });

    grid.querySelectorAll('[data-mod-run]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var mid = btn.dataset.modRun;
        sendCommand('module_start', { id: mid });
        fetch('/api/modules/start', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id: mid })
        }).then(function(r) { return r.json(); }).then(function(d) {
          if (d.ok) { toast('Module started: ' + mid, 'success'); setTimeout(loadModules, 500); }
          else { toast('Error: ' + (d.message || 'Failed'), 'error'); }
        }).catch(function() { toast('Module started: ' + mid, 'success'); setTimeout(loadModules, 500); });
      });
    });

    grid.querySelectorAll('[data-mod-stop]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        sendCommand('module_stop', {});
        fetch('/api/modules/stop', { method: 'POST' }).then(function() {
          toast('Module stopped', 'success');
          setTimeout(loadModules, 500);
        });
      });
    });
  } catch(e) {
    grid.innerHTML = '<div style="color:#ea4335;font-size:.82rem">Error loading modules</div>';
  }
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
  var completed = 0;
  var total = files.length;
  for (var i = 0; i < files.length; i++) {
    (function(file) {
      var fd = new FormData();
      fd.append('file', file);
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/modules/upload');
      xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) {
          var pct = Math.round((completed / total) * 100 + (e.loaded / e.total) * (100 / total));
          uploadProgressBar.style.width = pct + '%';
        }
      };
      xhr.onload = function() {
        completed++;
        if (completed === total) {
          uploadProgressBar.style.width = '100%';
          setTimeout(function() {
            uploadProgress.classList.remove('visible');
            uploadProgressBar.style.width = '0%';
            toast('Upload complete!', 'success');
            loadModules();
          }, 500);
        }
      };
      xhr.onerror = function() { toast('Upload failed', 'error'); };
      xhr.send(fd);
    })(files[i]);
  }
}
