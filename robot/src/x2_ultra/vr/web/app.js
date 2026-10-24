// Visor WebXR del N Club para las Meta Quest 3 (también se puede ver en pantalla para probar).
//
// La escena (scene.json + scene.bin + tex/) llega una vez; luego el PC manda por WebSocket, a 50 Hz, la pose de los
// cuerpos que se mueven (robot y puertas). El huésped lo mueven las gafas: el joystick calcula el paso y las colisiones
// aquí mismo (con el mapa de ocupación del simulador) y se envía la pose al PC. La cabeza solo gira y se inclina:
// moverse por la habitación no desplaza al huésped más de LEAN metros (solo se anda con los joysticks).
//
// Todo lo de MuJoCo va dentro de `world`, girado para pasar de z arriba (MuJoCo) a y arriba (three.js):
// (x, y, z) de MuJoCo = (x, z, -y) de three.js.
import * as THREE from './vendor/three.module.min.js';

const params = new URLSearchParams(location.search);
const EYE = Number(params.get('ojos') || 1.62);          // m, altura de los ojos del huésped
const SNAP = THREE.MathUtils.degToRad(Number(params.get('giro') || 30));
const LEAN = 0.15;                                       // m que la cabeza puede salirse del sitio
const DEADZONE = 0.18;
const RENDER_DELAY = 0.06;                               // s de colchón para interpolar el estado
const POSE_HZ = 30;
const RESET_HOLD = 1.5;                                  // s manteniendo B/Y para reiniciar la visita
const SUBTITLE_MIN = 3.0;
const HUD_WIDTH = 0.7;         // m de ancho del panel de subtítulos a HUD_DISTANCE: unos 27° de la vista
const HUD_DISTANCE = 1.5;      // m delante de la cabeza
const HUD_DROP = 0.42;         // m por debajo de los ojos

const $ = (id) => document.getElementById(id);
const mark = (id, ok, text) => { const el = $(id); el.className = ok ? 'ok' : 'mal'; el.textContent = text; };

THREE.ColorManagement.enabled = false;                   // colores como MuJoCo: sin conversión sRGB
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.xr.enabled = true;
renderer.xr.setReferenceSpaceType('local-floor');
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const world = new THREE.Group();
world.rotation.x = -Math.PI / 2;
scene.add(world);
const dolly = new THREE.Group();
scene.add(dolly);
const camera = new THREE.PerspectiveCamera(75, innerWidth / innerHeight, 0.05, 200);
camera.rotation.order = 'YXZ';
dolly.add(camera);

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

// ------------------------------------------------------------------------------------------------ escena
let S = null;                 // scene.json
let bodies = [];              // grupos de los cuerpos móviles, en el orden del estado
let layout = null;

function geometry(bin, c) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(bin, c.pos[0], c.pos[1]), 3));
  if (c.nrm) g.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(bin, c.nrm[0], c.nrm[1]), 3));
  if (c.uv) g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(bin, c.uv[0], c.uv[1]), 2));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(bin, c.idx[0], c.idx[1]), 1));
  g.computeBoundingSphere();
  return g;
}

function material(m, textures) {
  const color = new THREE.Color(m.rgba[0], m.rgba[1], m.rgba[2]);
  const transparent = m.rgba[3] < 1;
  const mat = new THREE.MeshPhongMaterial({
    color,
    specular: new THREE.Color().setScalar(m.specular * 0.2),
    shininess: Math.max(2, m.shininess * 128),
    emissive: color.clone().multiplyScalar(m.emission),
    flatShading: m.flat,
    transparent, opacity: m.rgba[3], depthWrite: !transparent,
    side: transparent ? THREE.DoubleSide : THREE.FrontSide,
  });
  if (m.texture >= 0) {
    mat.map = textures[m.texture];
    if (m.emission > 0) mat.emissiveMap = textures[m.texture];
  }
  return mat;
}

function light(l) {
  const color = new THREE.Color(...l.diffuse);
  const [px, py, pz] = l.pos;
  const [dx, dy, dz] = l.dir;
  let obj;
  if (l.type === 'spot') {
    obj = new THREE.SpotLight(color, Math.PI, 0, THREE.MathUtils.degToRad(l.cutoff), Math.min(1, l.exponent / 8), 0);
  } else if (l.type === 'directional') {
    obj = new THREE.DirectionalLight(color, Math.PI);
  } else {
    obj = new THREE.PointLight(color, Math.PI, 0, 0);
  }
  obj.position.set(px, py, pz);
  if (obj.target) {
    obj.target.position.set(px + dx, py + dy, pz + dz);
    world.add(obj.target);
  }
  world.add(obj);
}

function sky(top, horizon) {
  const geo = new THREE.SphereGeometry(120, 32, 16);
  const colors = [];
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const t = Math.max(0, pos.getY(i) / 120);
    colors.push(...new THREE.Color(...horizon).lerp(new THREE.Color(...top), t).toArray());
  }
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide, depthWrite: false }));
  mesh.renderOrder = -1;
  scene.add(mesh);
}

async function loadScene() {
  S = await (await fetch('scene.json')).json();
  const bin = await (await fetch('scene.bin')).arrayBuffer();
  const loader = new THREE.TextureLoader();
  const textures = await Promise.all(S.textures.map((url) => loader.loadAsync(url).then((t) => {
    t.flipY = false;                                     // fila 0 de MuJoCo = v 0
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.colorSpace = THREE.NoColorSpace;
    t.anisotropy = 4;
    t.needsUpdate = true;
    return t;
  })));
  const mats = S.materials.map((m) => material(m, textures));
  for (const c of S.static) {
    const mesh = new THREE.Mesh(geometry(bin, c), mats[c.material]);
    mesh.matrixAutoUpdate = false;
    world.add(mesh);
  }
  bodies = S.body_chunks.map((chunks) => {
    const g = new THREE.Group();
    for (const c of chunks) g.add(new THREE.Mesh(geometry(bin, c), mats[c.material]));
    world.add(g);
    return g;
  });
  let ambient = new THREE.Color(...S.headlight.ambient);
  for (const l of S.lights) { light(l); ambient.add(new THREE.Color(...l.ambient)); }
  scene.add(new THREE.AmbientLight(ambient, Math.PI));
  if (S.headlight.active) {
    const head = new THREE.DirectionalLight(new THREE.Color(...S.headlight.diffuse), Math.PI);
    head.position.set(0, 0, 0);
    head.target.position.set(0, 0, -1);
    camera.add(head, head.target);
  }
  sky(S.sky[0], S.sky[1]);
  layout = S.state_layout;
  occupancy.init(S.guest);
  if (!placedByServer) teleport(...S.guest.start);    // si el simulador ya dijo dónde está el huésped, manda él
  mark('e-escena', true, `Escena lista (${(S.triangles / 1000).toFixed(0)} mil triángulos)`);
}

// ------------------------------------------------------------------------------------------------ colisiones
// Las mismas reglas que show/guest.py: mapa de ocupación de las paredes y muebles, puertas y distancia a los robots.
const occupancy = {
  init(g) {
    this.g = g;
    const o = g.occupancy;
    this.ox = o.origin[0]; this.oy = o.origin[1]; this.cell = o.cell;
    this.nx = o.shape[0]; this.ny = o.shape[1];
    this.bits = Uint8Array.from(atob(o.bits), (c) => c.charCodeAt(0));
    const r = Math.ceil(g.radius / this.cell);
    this.disk = [];
    for (let i = -r; i <= r; i++) for (let j = -r; j <= r; j++) if (i * i + j * j <= r * r) this.disk.push([i, j]);
    this.r = r;
  },
  wall(i, j) {
    const k = i * this.ny + j;
    return (this.bits[k >> 3] >> (7 - (k & 7))) & 1;
  },
  free(x, y) {
    const R = this.g.radius;
    const i = Math.round((x - this.ox) / this.cell), j = Math.round((y - this.oy) / this.cell);
    if (!(i - this.r < 0 || j - this.r < 0 || i + this.r >= this.nx || j + this.r >= this.ny)) {
      for (const [di, dj] of this.disk) if (this.wall(i + di, j + dj)) return false;
    }
    if (latest) {
      const f = latest.floats;
      let k = 5;
      for (let d = 0; d < layout.doors; d++, k += 4) {
        if (f[k] - R < x && x < f[k + 2] + R && f[k + 1] - R < y && y < f[k + 3] + R) return false;
      }
      for (let r = 0; r < layout.robots; r++, k += 2) {
        if (Math.hypot(x - f[k], y - f[k + 1]) < this.g.robot_clearance) return false;
      }
    }
    return true;
  },
};

// ------------------------------------------------------------------------------------------------ huésped
const guest = { x: 0, y: 0 };
let dollyYaw = 0;
let pendingYaw = null;          // rumbo MuJoCo a aplicar cuando se conozca la cabeza
const anchor = new THREE.Vector3();
let anchored = false;
let lookYaw = 0, lookPitch = 0; // modo pantalla
let placedByServer = false;

function teleport(x, y, yaw) {
  guest.x = x; guest.y = y;
  pendingYaw = yaw;
  anchored = false;
}

function headYawMj() {
  const e = new THREE.Euler().setFromQuaternion(camera.getWorldQuaternion(new THREE.Quaternion()), 'YXZ');
  return e.y + Math.PI / 2;
}

function move(fwd, side, dt) {
  const yaw = headYawMj();
  const c = Math.cos(yaw), s = Math.sin(yaw);
  const sx = (c * fwd - s * side) * dt, sy = (s * fwd + c * side) * dt;
  for (const [tx, ty] of [[sx, sy], [sx, 0], [0, sy]]) {      // deslizar por las paredes
    if (Math.hypot(tx, ty) > 1e-9 && occupancy.free(guest.x + tx, guest.y + ty)) {
      guest.x += tx; guest.y += ty;
      return;
    }
  }
}

function placeDolly(xr) {
  if (xr) {
    const head = camera.position;                               // pose local que pone WebXR
    if (pendingYaw !== null) {
      const local = new THREE.Euler().setFromQuaternion(camera.quaternion, 'YXZ').y;
      dollyYaw = pendingYaw - Math.PI / 2 - local;
      pendingYaw = null;
    }
    if (!anchored) { anchor.copy(head); anchored = true; }
    const d = head.clone().sub(anchor);
    const len = d.length();
    if (len > LEAN) anchor.addScaledVector(d, 1 - LEAN / len);
    dolly.rotation.y = dollyYaw;
    const a = anchor.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), dollyYaw);
    dolly.position.set(guest.x - a.x, EYE - anchor.y, -guest.y - a.z);
  } else {
    if (pendingYaw !== null) { lookYaw = pendingYaw - Math.PI / 2; pendingYaw = null; }
    dolly.rotation.y = 0;
    dolly.position.set(guest.x, EYE, -guest.y);
    camera.position.set(0, 0, 0);
    camera.rotation.set(lookPitch, lookYaw, 0);
  }
}

// ------------------------------------------------------------------------------------------------ red
let ws = null;
let latest = null;
const states = [];              // [{t, arrive, floats}]
let clockOffset = null;

function connect() {
  ws = new WebSocket(`wss://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {
    mark('e-red', true, 'Conectado con el simulador');
    ws.send(JSON.stringify({ t: 'hello', ua: navigator.userAgent }));
  };
  ws.onclose = () => {
    mark('e-red', false, 'Sin conexión con el simulador; reintentando…');
    setTimeout(connect, 2000);
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') return onJson(JSON.parse(ev.data));
    const kind = new Uint8Array(ev.data, 0, 1)[0];
    if (kind === 1) onState(new Float32Array(ev.data, 4));
    else if (kind === 2) playWav(ev.data.slice(4));
  };
}

function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

function onJson(m) {
  if (m.t === 'teleport') { teleport(m.x, m.y, m.yaw); placedByServer = true; }
  else if (m.t === 'subtitle') hud.say(m.text);
  else if (m.t === 'status') hud.status(m.text);
  else if (m.t === 'cue') hud.cue(m.text);
}

function onState(floats) {
  const now = performance.now() / 1000;
  const t = floats[0];
  const sample = now - t;
  if (clockOffset === null || sample < clockOffset) clockOffset = sample;
  else clockOffset += (sample - clockOffset) * 0.02;   // el simulador puede ir más lento que el reloj
  latest = { t, floats };
  states.push(latest);
  while (states.length > 30) states.shift();
}

const qa = new THREE.Quaternion(), qb = new THREE.Quaternion();
function applyBodies() {
  if (!latest || !layout) return;
  const rt = performance.now() / 1000 - clockOffset - RENDER_DELAY;
  let a = states[0], b = latest, f = 1;
  for (let i = states.length - 1; i > 0; i--) {
    if (states[i - 1].t <= rt) { a = states[i - 1]; b = states[i]; f = Math.min(1, Math.max(0, (rt - a.t) / Math.max(1e-6, b.t - a.t))); break; }
  }
  if (rt <= states[0].t) { a = b = states[0]; f = 0; }
  const base = 5 + 4 * layout.doors + 2 * layout.robots;
  for (let i = 0; i < bodies.length; i++) {
    const k = base + 7 * i;
    const A = a.floats, B = b.floats;
    bodies[i].position.set(A[k] + (B[k] - A[k]) * f, A[k + 1] + (B[k + 1] - A[k + 1]) * f, A[k + 2] + (B[k + 2] - A[k + 2]) * f);
    qa.set(A[k + 4], A[k + 5], A[k + 6], A[k + 3]);
    qb.set(B[k + 4], B[k + 5], B[k + 6], B[k + 3]);
    bodies[i].quaternion.slerpQuaternions(qa, qb, f);
  }
}

// ------------------------------------------------------------------------------------------------ audio
let audio = null;
let micNode = null;
let voice = null;
let micPeak = 0;
let ptt = false;

// El AudioContext se crea dentro del toque (lo exige el navegador); el micrófono puede llegar después.
function startAudioContext() {
  if (!audio) audio = new AudioContext();
  audio.resume();
}

async function startMic() {
  if (micNode) return;
  startAudioContext();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      // sin control automático de ganancia: en las Quest subía tanto la voz que saturaba (picos a 1,0)
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false },
    });
    await audio.audioWorklet.addModule('mic-worklet.js');
    micNode = new AudioWorkletNode(audio, 'mic-16k');
    const mute = audio.createGain();
    mute.gain.value = 0;
    audio.createMediaStreamSource(stream).connect(micNode).connect(mute).connect(audio.destination);
    micNode.port.onmessage = ({ data }) => {
      micPeak = Math.max(micPeak * 0.8, data.peak);
      if (!ws || ws.readyState !== 1) return;
      const out = new Uint8Array(4 + data.pcm.byteLength);
      out.set([1, ptt ? 1 : 0, 0, 0]);
      out.set(new Uint8Array(data.pcm), 4);
      ws.send(out);
    };
    mark('e-micro', true, `Micrófono activo (${audio.sampleRate} Hz → 16 kHz)`);
  } catch (err) {
    mark('e-micro', false, `Micrófono no disponible: ${err.message}`);
  }
  await audio.resume();
}

async function playWav(buf) {
  if (!audio) return;
  try {
    const decoded = await audio.decodeAudioData(buf);
    if (voice) voice.stop();
    voice = audio.createBufferSource();
    voice.buffer = decoded;
    voice.connect(audio.destination);
    voice.start();
  } catch (err) {
    console.warn('audio', err);
  }
}

function setPtt(down) {
  if (down === ptt) return;
  ptt = down;
  send({ t: 'ptt', down });
}

// ------------------------------------------------------------------------------------------------ subtítulos
// Un panel que flota delante, algo por debajo de la mirada, y sigue la cabeza con retraso (no va pegado a la vista).
const hud = {
  init() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = 1024; this.canvas.height = 320;
    this.ctx = this.canvas.getContext('2d');
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.NoColorSpace;
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(HUD_WIDTH, HUD_WIDTH * 320 / 1024),
      new THREE.MeshBasicMaterial({ map: this.tex, transparent: true, depthTest: false }));
    this.mesh.renderOrder = 10;
    scene.add(this.mesh);
    this.text = ''; this.until = 0; this.statusText = ''; this.cueText = '';
    this.draw();
  },
  say(text) {
    this.text = text;
    this.until = performance.now() / 1000 + Math.max(SUBTITLE_MIN, 0.075 * text.length);
    this.draw();
  },
  status(text) { this.statusText = text; this.draw(); },
  cue(text) { this.cueText = text; this.draw(); },
  draw() {
    const c = this.ctx, W = this.canvas.width, H = this.canvas.height;
    c.clearRect(0, 0, W, H);
    // la frase del robot manda; si calla y le toca al visitante, su frase en dorado
    const mine = !this.text && this.cueText;
    const shown = this.text || (mine ? this.cueText : '');
    if (shown) {
      c.fillStyle = mine ? 'rgba(40, 30, 8, 0.85)' : 'rgba(12, 14, 20, 0.78)';
      c.beginPath(); c.roundRect(0, 0, W, 230, 24); c.fill();
      c.fillStyle = mine ? '#f2c94c' : '#ffffff';
      c.font = '600 44px system-ui, sans-serif';
      c.textAlign = 'center';
      const lines = wrap(c, shown, W - 70).slice(0, 4);
      const lh = 52, y0 = 115 - (lines.length - 1) * lh / 2 + 15;
      lines.forEach((l, i) => c.fillText(l, W / 2, y0 + i * lh));
    }
    if (this.statusText) {
      c.font = '500 30px system-ui, sans-serif';
      c.textAlign = 'center';
      const w = Math.min(W, c.measureText(this.statusText).width + 48);
      c.fillStyle = 'rgba(12, 14, 20, 0.65)';
      c.beginPath(); c.roundRect((W - w) / 2, 250, w, 54, 16); c.fill();
      c.fillStyle = '#d8a830';
      c.fillText(this.statusText, W / 2, 288);
    }
    this.tex.needsUpdate = true;
  },
  update(dt) {
    if (this.text && performance.now() / 1000 > this.until) { this.text = ''; this.draw(); }
    const head = camera.getWorldPosition(new THREE.Vector3());
    const yaw = new THREE.Euler().setFromQuaternion(camera.getWorldQuaternion(new THREE.Quaternion()), 'YXZ').y;
    const target = head.clone().add(new THREE.Vector3(-Math.sin(yaw), 0, -Math.cos(yaw)).multiplyScalar(HUD_DISTANCE));
    target.y = head.y - HUD_DROP;
    if (this.mesh.position.distanceTo(target) > 3) this.mesh.position.copy(target);
    else this.mesh.position.lerp(target, Math.min(1, dt * 3));
    this.mesh.lookAt(head.x, this.mesh.position.y + 0.1, head.z);
  },
};

function wrap(ctx, text, width) {
  const out = [];
  let line = '';
  for (const word of text.split(/\s+/)) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > width && line) { out.push(line); line = word; } else line = next;
  }
  if (line) out.push(line);
  return out;
}

// ------------------------------------------------------------------------------------------------ mandos
function controllerModel(i) {
  const grip = renderer.xr.getControllerGrip(i);
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.045, 0.03, 0.12), new THREE.MeshPhongMaterial({ color: 0x2b2f36 }));
  body.position.z = 0.03;
  grip.add(body);
  scene.add(grip);
  return grip;
}

const micDot = new THREE.Mesh(new THREE.SphereGeometry(0.012, 12, 8), new THREE.MeshBasicMaterial({ color: 0x5cc98a }));
let snapReady = { left: true, right: true };
let resetHeld = 0;
let lastPose = 0;

function readControllers(dt) {
  const session = renderer.xr.getSession();
  let fwd = 0, side = 0, run = false, trigger = false, resetBtn = false;
  if (session) {
    for (const src of session.inputSources) {
      const gp = src.gamepad;
      if (!gp) continue;
      const ax = gp.axes.length >= 4 ? [gp.axes[2], gp.axes[3]] : [gp.axes[0] || 0, gp.axes[1] || 0];
      const b = (k) => gp.buttons[k] && gp.buttons[k].pressed;
      trigger ||= b(0);
      resetBtn ||= b(5);
      if (src.handedness === 'left') {
        const [x, y] = ax.map((v) => (Math.abs(v) < DEADZONE ? 0 : v));
        run = b(1) || b(3);
        fwd = -y; side = -x;
      } else if (src.handedness === 'right') {
        const x = ax[0];
        if (snapReady.right && Math.abs(x) > 0.7) { dollyYaw -= Math.sign(x) * SNAP; snapReady.right = false; }
        if (Math.abs(x) < 0.3) snapReady.right = true;
      }
    }
  } else {
    fwd = (keys.KeyW ? 1 : 0) - (keys.KeyS ? 1 : 0);
    side = (keys.KeyA ? 1 : 0) - (keys.KeyD ? 1 : 0);
    run = !!keys.ShiftLeft;
    trigger = !!keys.Space;
  }
  setPtt(trigger);
  if (resetBtn) {
    resetHeld += dt;
    if (resetHeld >= RESET_HOLD) { send({ t: 'reset' }); resetHeld = -1e9; }
  } else resetHeld = 0;
  const g = S.guest;
  const f = fwd > 0 ? fwd * (run ? g.run : g.walk) : fwd * g.back;
  const s = side * (run ? g.side * 1.4 : g.side);
  if (f || s) move(f, s, dt);
  micDot.material.color.setHex(ptt ? 0x5cc98a : 0x3a4250);
  micDot.scale.setScalar(1 + 2 * Math.min(1, micPeak * 3));
}

// ------------------------------------------------------------------------------------------------ pantalla
const keys = {};
addEventListener('keydown', (e) => { keys[e.code] = true; });
addEventListener('keyup', (e) => { keys[e.code] = false; });
let dragging = false;
renderer.domElement.addEventListener('pointerdown', () => { dragging = true; });
addEventListener('pointerup', () => { dragging = false; });
addEventListener('pointermove', (e) => {
  if (!dragging || renderer.xr.isPresenting) return;
  lookYaw -= e.movementX * 0.004;
  lookPitch = Math.max(-1.3, Math.min(1.3, lookPitch - e.movementY * 0.004));
});

// ------------------------------------------------------------------------------------------------ bucle
const clock = new THREE.Clock();
function frame() {
  const dt = Math.min(clock.getDelta(), 0.1);
  if (S) {
    const xr = renderer.xr.isPresenting;
    readControllers(dt);
    placeDolly(xr);
    applyBodies();
    hud.update(dt);
    const now = performance.now() / 1000;
    if (now - lastPose > 1 / POSE_HZ) {
      lastPose = now;
      send({ t: 'pose', x: guest.x, y: guest.y, yaw: headYawMj() });
    }
  }
  renderer.render(scene, camera);
}

// requestSession tiene que ser lo primero que se hace al tocar el botón: si antes se espera a otra cosa (pedir el
// micrófono), el navegador de las Quest ya no lo cuenta como gesto del usuario y lo rechaza.
async function enterVR() {
  startAudioContext();
  const session = await navigator.xr.requestSession('immersive-vr', { optionalFeatures: ['local-floor', 'bounded-floor'] });
  startMic();
  renderer.xr.setFoveation(1);
  await renderer.xr.setSession(session);
  anchored = false;
  pendingYaw = headYawMj();               // conservar hacia dónde mira el huésped
  $('panel').classList.add('oculto');
  session.addEventListener('end', () => $('panel').classList.remove('oculto'));
}

async function enterScreen() {
  startAudioContext();
  await startMic();
  $('panel').classList.add('oculto');
}

async function main() {
  hud.init();
  const left = controllerModel(0);
  controllerModel(1);
  micDot.position.set(0, 0.025, 0.0);
  left.add(micDot);
  connect();
  try {
    await loadScene();
  } catch (err) {
    mark('e-escena', false, `No se pudo cargar la escena: ${err.message}`);
    return;
  }
  const vr = navigator.xr && await navigator.xr.isSessionSupported('immersive-vr').catch(() => false);
  $('entrar').disabled = !vr;
  if (!vr) $('entrar').textContent = 'VR no disponible en este navegador';
  $('pantalla').disabled = false;
  $('entrar').onclick = () => enterVR().catch((e) => alert(`No se pudo entrar en VR: ${e.message}`));
  $('pantalla').onclick = enterScreen;
  setInterval(() => { $('nivel').firstElementChild.style.width = `${Math.min(100, micPeak * 300)}%`; }, 100);
  renderer.setAnimationLoop(frame);
}

main();
