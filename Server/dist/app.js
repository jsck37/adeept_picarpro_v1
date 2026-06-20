'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let ws = null;
let lastDir = 'stop';
let moveThrottle = 0;
const craneGripAngle = { low: 0, mid: 135, high: 190 };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function $(id) { return document.getElementById(id); }

function toast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  $('toasts').appendChild(el);
  setTimeout(() => {
    el.style.animation = 'tout .3s ease forwards';
    setTimeout(() => el.remove(), 300);
  }, 2500);
}

function send(cmd, params) {
  params = params || {};
  if (cmd === 'move' || cmd === 'speed') {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({cmd: 'web_active', params: {active: true}}));
    }
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({cmd, params}));
  } else {
    fetch(`/cmd/${cmd}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params),
    }).catch(() => {});
  }
}

function hex2rgb(hex) {
  return [parseInt(hex.slice(1,3),16), parseInt(hex.slice(3,5),16), parseInt(hex.slice(5,7),16)];
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
function wsConnect() {
  try {
    ws = new WebSocket(`ws://${location.hostname}:8888`);
    ws.onopen = () => $('dot').classList.remove('offline');
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'status') updateStatus(msg.data);
        else if (msg.type === 'log') appendLog(msg.text, msg.ts);
        else if (msg.type === 'log_history' && msg.lines) {
          msg.lines.forEach(([ts, txt]) => appendLog(txt, ts));
        }
        else if (msg.type === 'response' && msg.data && msg.data.error) {
          toast(msg.data.error, 'error');
        }
      } catch (err) {}
    };
    ws.onclose = () => {
      $('dot').classList.add('offline');
      setTimeout(wsConnect, 3000);
    };
    ws.onerror = () => ws.close();
  } catch (e) {}
}

// ---------------------------------------------------------------------------
// Status update
// ---------------------------------------------------------------------------
function updateStatus(d) {
  if (!d) return;
  $('sb-cpu-temp').textContent = d.cpu_temp + '°C';
  $('sb-cpu-usage').textContent = d.cpu_usage + '%';
  $('sb-ram').textContent = `${d.ram.used_mb}/${d.ram.total_mb}M ${d.ram.percent}%`;
  $('sb-distance').textContent = d.distance + 'cm';
  $('sb-speed').textContent = d.speed + '%';
  const modes = {
    none: 'Ready', radarScan: 'Radar', automatic: 'Drive',
    trackLine: 'IR Line', trackLineCV: 'CV Line', trackHand: 'Hand',
    keepDistance: 'Distance',
  };
  $('sb-mode').textContent = modes[d.auto_mode] || d.auto_mode || 'Ready';
  if (d.mpu6050) {
    $('sb-imu').textContent = `R:${d.mpu6050.roll}° P:${d.mpu6050.pitch}°`;
    $('ax').textContent = d.mpu6050.accel.x.toFixed(2);
    $('ay').textContent = d.mpu6050.accel.y.toFixed(2);
    $('az').textContent = d.mpu6050.accel.z.toFixed(2);
    $('gx').textContent = d.mpu6050.gyro.x.toFixed(1);
    $('gy').textContent = d.mpu6050.gyro.y.toFixed(1);
    $('gz').textContent = d.mpu6050.gyro.z.toFixed(1);
    $('roll').textContent = d.mpu6050.roll.toFixed(1);
    $('pitch').textContent = d.mpu6050.pitch.toFixed(1);
  } else {
    $('sb-imu').textContent = 'N/A';
  }
  $('ir-l').textContent = d.ir_left === null ? '--' : (d.ir_left ? 'LINE' : 'CLR');
  $('ir-m').textContent = d.ir_middle === null ? '--' : (d.ir_middle ? 'LINE' : 'CLR');
  $('ir-r').textContent = d.ir_right === null ? '--' : (d.ir_right ? 'LINE' : 'CLR');
  $('lv-banner').style.display = d.low_voltage ? 'block' : 'none';
  if (d.ds4) {
    $('sb-ds4').textContent = d.ds4.connected ? 'ON' : '--';
    $('ds4-dot').style.background = d.ds4.connected ? '#34a853' : '#ea4335';
    $('ds4-text').textContent = d.ds4.connected
      ? `Connected (${d.ds4.connect_count}×)`
      : 'Not connected';
  }
  if (d.cv_mode && d.cv_mode !== 'none') {
    $('cv-badge').textContent = 'CV: ' + d.cv_mode;
    $('cv-badge').classList.add('visible');
  } else {
    $('cv-badge').classList.remove('visible');
  }
  $('headlight').classList.toggle('active', !!d.headlight);
}

// ---------------------------------------------------------------------------
// Log
// ---------------------------------------------------------------------------
function appendLog(text, ts) {
  const c = $('console');
  if (!c) return;
  const line = document.createElement('div');
  line.className = 'log-line ' + logLevel(text);
  if (ts) {
    const d = new Date(ts * 1000);
    text = d.toLocaleTimeString() + ' ' + text;
  }
  line.textContent = text;
  c.appendChild(line);
  if ($('log-auto').checked) c.scrollTop = c.scrollHeight;
  $('log-count').textContent = c.children.length + ' lines';
}
function logLevel(t) {
  t = (t || '').toUpperCase();
  if (t.includes('| ERROR') || t.includes('[ERROR]')) return 'error';
  if (t.includes('| WARNING') || t.includes('| WARN')) return 'warn';
  if (t.includes('| DEBUG') || t.includes('[DEBUG]')) return 'debug';
  return 'info';
}

// ---------------------------------------------------------------------------
// Servos
// ---------------------------------------------------------------------------
const servoDefs = [
  {id: 0, name: 'Steering', min: 30, max: 150, init: 90},
  {id: 1, name: 'Cam Pan',  min: 0,  max: 180, init: 90},
  {id: 2, name: 'Cam Tilt', min: 0,  max: 180, init: 90},
  {id: 5, name: 'Crane Grip', min: 0, max: 190, init: 190},
  {id: 6, name: 'Crane Arm',  min: 0, max: 180, init: 80},
];
function buildServos() {
  const grid = $('servo-grid');
  servoDefs.forEach(s => {
    const cell = document.createElement('div');
    cell.className = 'servo-cell';
    cell.innerHTML = `<label>${s.name}</label>
      <input type="range" min="${s.min}" max="${s.max}" value="${s.init}" data-servo="${s.id}">
      <b>${s.init}°</b>`;
    grid.appendChild(cell);
    const slider = cell.querySelector('input');
    const label = cell.querySelector('b');
    slider.addEventListener('input', () => label.textContent = slider.value + '°');
    slider.addEventListener('change', () =>
      send('servo', {id: s.id, angle: parseInt(slider.value)}));
  });
  $('servo-home').onclick = () => send('servo_home', {});
}

// ---------------------------------------------------------------------------
// Joystick
// ---------------------------------------------------------------------------
function setupJoy(joyId, knobId, labelId, sendMove) {
  const joy = $(joyId), knob = $(knobId), label = $(labelId);
  let dragging = false;
  function center() {
    const r = joy.getBoundingClientRect();
    return {x: r.left + r.width/2, y: r.top + r.height/2, r: r.width/2 - 20};
  }
  function dir(dx, dy) {
    const a = Math.atan2(-dy, dx) * 180 / Math.PI;
    const d = Math.hypot(dx, dy);
    if (d < 10) return 'stop';
    if (a > -22.5 && a <= 22.5) return 'right';
    if (a > 22.5 && a <= 67.5) return 'forward_right';
    if (a > 67.5 && a <= 112.5) return 'forward';
    if (a > 112.5 && a <= 157.5) return 'forward_left';
    if (a > 157.5 || a <= -157.5) return 'left';
    if (a > -157.5 && a <= -112.5) return 'backward_left';
    if (a > -112.5 && a <= -67.5) return 'backward';
    return 'backward_right';
  }
  const labels = {
    forward: 'Forward', backward: 'Back', left: 'Left', right: 'Right',
    forward_left: 'F-Left', forward_right: 'F-Right',
    backward_left: 'B-Left', backward_right: 'B-Right', stop: 'Stop',
  };
  function move(cx, cy) {
    const c = center();
    let dx = cx - c.x, dy = cy - c.y;
    const d = Math.hypot(dx, dy);
    if (d > c.r) { dx = dx/d*c.r; dy = dy/d*c.r; }
    knob.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`;
    const direction = dir(dx, dy);
    label.textContent = labels[direction] || direction;
    if (sendMove) {
      const now = Date.now();
      if (direction !== lastDir || now - moveThrottle > 150) {
        send('move', {dir: direction});
        lastDir = direction;
        moveThrottle = now;
      }
    } else {
      // Camera joystick — pan/tilt from direction + magnitude.
      if (direction !== 'stop') {
        send('servo', {id: 1, angle: 90 + Math.round(dx / c.r * 30)});
        send('servo', {id: 2, angle: 90 + Math.round(dy / c.r * 30)});
      }
    }
  }
  function start(e) {
    e.preventDefault();
    dragging = true;
    const t = e.touches ? e.touches[0] : e;
    move(t.clientX, t.clientY);
  }
  function drag(e) {
    if (!dragging) return;
    e.preventDefault();
    const t = e.touches ? e.touches[0] : e;
    move(t.clientX, t.clientY);
  }
  function end() {
    if (!dragging) return;
    dragging = false;
    knob.style.transform = 'translate(-50%, -50%)';
    if (sendMove) {
      send('move', {dir: 'stop'});
      lastDir = 'stop';
      label.textContent = 'Wheels — WASD';
    } else {
      label.textContent = 'Camera — Arrows';
    }
  }
  joy.addEventListener('mousedown', start);
  joy.addEventListener('touchstart', start, {passive: false});
  document.addEventListener('mousemove', drag);
  document.addEventListener('touchmove', drag, {passive: false});
  document.addEventListener('mouseup', end);
  document.addEventListener('touchend', end);
}

// ---------------------------------------------------------------------------
// Keyboard
// ---------------------------------------------------------------------------
function setupKeyboard() {
  const keymap = {
    'w': 'forward', 's': 'backward', 'a': 'left', 'd': 'right',
    'ArrowUp': 'forward', 'ArrowDown': 'backward',
    'ArrowLeft': 'left', 'ArrowRight': 'right',
  };
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const dir = keymap[e.key];
    if (dir && dir !== lastDir) {
      send('move', {dir});
      lastDir = dir;
    }
  });
  document.addEventListener('keyup', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (keymap[e.key] && lastDir !== 'stop') {
      send('move', {dir: 'stop'});
      lastDir = 'stop';
    }
  });
}

// ---------------------------------------------------------------------------
// Wiring up buttons / sliders
// ---------------------------------------------------------------------------
function wireUI() {
  // CV mode buttons
  document.querySelectorAll('.cvbtn').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('.cvbtn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const mode = btn.dataset.cv;
      $('hand-color-row').style.display = (mode === 'trackHand') ? 'block' : 'none';
      if (mode === 'findlineCV' || mode === 'trackHand') {
        send('auto', {func: mode === 'findlineCV' ? 'trackLineCV' : 'trackHand'});
      } else {
        send('auto', {func: 'stop'});
        send('cv_mode', {mode});
      }
    };
  });

  // Hand color presets
  document.querySelectorAll('.psbtn[data-preset]').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('.psbtn[data-preset]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      send('hand_color', {preset: btn.dataset.preset});
    };
  });

  // Hand color custom HSV sliders
  ['hl','sl','vl','hh','sh','vh'].forEach(id => {
    const el = $(id), val = $(id + '-v');
    el.oninput = () => val.textContent = el.value;
  });
  $('apply-hsv').onclick = () => {
    send('hand_color', {
      h_low: +$('hl').value, s_low: +$('sl').value, v_low: +$('vl').value,
      h_high: +$('hh').value, s_high: +$('sh').value, v_high: +$('vh').value,
    });
    document.querySelectorAll('.psbtn[data-preset]').forEach(b => b.classList.remove('active'));
    toast('Custom HSV applied', 'success');
  };

  // Auto mode buttons
  document.querySelectorAll('[data-auto]').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('[data-auto]').forEach(b => b.classList.remove('active'));
      if (btn.dataset.auto !== 'stop') btn.classList.add('active');
      send('auto', {func: btn.dataset.auto});
    };
  });

  // Crane buttons
  document.querySelectorAll('[data-crane]').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll(`[data-crane="${btn.dataset.crane}"]`)
        .forEach(b => b.classList.add('active'));
      // For grip presets, deselect other grip buttons
      if (btn.dataset.crane.startsWith('grip_')) {
        document.querySelectorAll('[data-crane^="grip_"]').forEach(b => {
          if (b !== btn) b.classList.remove('active');
        });
        const pos = btn.dataset.crane.replace('grip_', '');
        $('grip-slider').value = craneGripAngle[pos];
        $('grip-val').textContent = craneGripAngle[pos] + '°';
      }
      send('crane', {action: btn.dataset.crane});
    };
  });

  // Grip slider
  $('grip-slider').oninput = () => $('grip-val').textContent = $('grip-slider').value + '°';
  $('grip-slider').onchange = () => {
    send('crane', {action: 'grip_angle', angle: parseInt($('grip-slider').value)});
    document.querySelectorAll('[data-crane^="grip_"]').forEach(b => b.classList.remove('active'));
  };

  // Speed slider
  $('speed').oninput = () => $('speed-val').textContent = $('speed').value + '%';
  $('speed').onchange = () => send('speed', {value: parseInt($('speed').value)});

  // Headlight + blinkers
  $('headlight').onclick = () => send('headlight', {action: 'toggle'});
  $('left-blink').onclick = () => {
    $('left-blink').classList.toggle('active');
    send('blinker', {side: 'left', active: $('left-blink').classList.contains('active')});
  };
  $('right-blink').onclick = () => {
    $('right-blink').classList.toggle('active');
    send('blinker', {side: 'right', active: $('right-blink').classList.contains('active')});
  };
  $('both-blink').onclick = () => {
    $('left-blink').classList.remove('active');
    $('right-blink').classList.remove('active');
    send('blinker', {side: 'both_off'});
  };

  // Side lights
  document.querySelectorAll('[data-sw]').forEach(btn => {
    btn.onclick = () => {
      const which = btn.dataset.sw;
      if (which === 'both') {
        send('switch', {id: 0, state: true});
        send('switch', {id: 1, state: true});
      } else {
        send('switch', {id: parseInt(which), state: true});
      }
    };
  });

  // LED strip
  const colorInput = $('led-color');
  document.querySelectorAll('.psbtn[data-hex]').forEach(btn => {
    btn.onclick = () => {
      colorInput.value = btn.dataset.hex;
      send('led', {mode: 'solid', color: hex2rgb(btn.dataset.hex)});
    };
  });
  document.querySelectorAll('[data-led]').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('[data-led]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      send('led', {mode: btn.dataset.led, color: hex2rgb(colorInput.value)});
    };
  });

  // Buzzer
  document.querySelectorAll('[data-buzz]').forEach(btn => {
    btn.onclick = () => {
      if (btn.dataset.buzz === 'stop') send('buzzer_stop', {});
      else send('buzzer', {melody: btn.dataset.buzz});
    };
  });

  // I²C scan
  $('i2c-btn').onclick = () => {
    $('i2c-result').textContent = 'Scanning...';
    send('i2c_scan', {});
  };

  // Console clear
  $('log-clear').onclick = () => {
    $('console').innerHTML = '';
    $('log-count').textContent = '0 lines';
    send('clear_log', {});
  };

  // Bluetooth
  $('bt-scan').onclick = () => btScan();
  $('bt-auto').onclick = () => {
    fetch('/api/bt/auto_connect', {method: 'POST'})
      .then(r => r.json())
      .then(d => toast(d.ok ? 'Connected' : (d.error || 'Failed'),
                       d.ok ? 'success' : 'error'));
  };
  $('bt-disc').onclick = () => {
    fetch('/api/bt/disconnect', {method: 'POST'})
      .then(r => r.json())
      .then(() => toast('Disconnected'));
  };
}

function btScan() {
  const list = $('bt-list'), devs = $('bt-devs');
  list.style.display = '';
  devs.innerHTML = '<div style="color:#9aa0a6;padding:4px">Scanning...</div>';
  fetch('/api/bt/scan').then(r => r.json()).then(d => {
    devs.innerHTML = '';
    if (!d.devices || !d.devices.length) {
      devs.innerHTML = '<div style="color:#9aa0a6;padding:4px">No devices found</div>';
      return;
    }
    d.devices.forEach(dev => {
      const item = document.createElement('div');
      item.className = 'bt-item' + (dev.is_gamepad ? ' gamepad' : '');
      item.innerHTML = `<span>${dev.name} (${dev.mac})</span>
        <button class="btn small">Connect</button>`;
      devs.appendChild(item);
      item.querySelector('button').onclick = () => {
        item.querySelector('button').textContent = '...';
        fetch('/api/bt/connect', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({mac: dev.mac, name: dev.name}),
        }).then(r => r.json()).then(r => {
          toast(r.ok ? 'Connected!' : (r.message || 'Failed'),
                r.ok ? 'success' : 'error');
        });
      };
    });
  }).catch(() => {
    devs.innerHTML = '<div style="color:#ea4335;padding:4px">Scan failed</div>';
  });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
buildServos();
setupJoy('joy', 'knob', 'joy-label', true);
setupJoy('cam-joy', 'cam-knob', null, false);
setupKeyboard();
wireUI();
wsConnect();
fetch('/api/logs').then(r => r.json()).then(d => {
  if (d.lines) d.lines.forEach(([ts, txt]) => appendLog(txt, ts));
}).catch(() => {});
