import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { authHeaders } from './api.js';

const loader = new GLTFLoader();

/**
 * Inspect viewer: PBR with a neutral room environment, orbit, wireframe / normals / matcap-free
 * modes, bounding box with dimensions. `scaleTo` (metres) previews a uniform-scale envelope fit.
 */
export function createViewer(el, url, { onStats, autoRotate = false, envelope } = {}) {
  el.innerHTML = '';
  el.classList.add('viewer');
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  el.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0d11);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.autoRotate = autoRotate;

  const hud = document.createElement('div');
  hud.className = 'hud';
  hud.textContent = 'loading…';
  el.appendChild(hud);
  const tools = document.createElement('div');
  tools.className = 'tools';
  el.appendChild(tools);

  let root = null;
  let boxHelper = null;
  let wire = false;
  const listeners = [];

  function resize() {
    const w = el.clientWidth || 300, h = el.clientHeight || 300;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(el);
  resize();

  function frame() {
    if (!root) return;
    const box = new THREE.Box3().setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const r = size.length() / 2 || 1;
    controls.target.copy(center);
    camera.position.copy(center).add(new THREE.Vector3(r * 1.6, r * 0.9, r * 1.9));
    camera.near = r / 100; camera.far = r * 100;
    camera.updateProjectionMatrix();
    controls.update();
  }

  function button(label, fn) {
    const b = document.createElement('button');
    b.textContent = label;
    b.onclick = fn;
    tools.appendChild(b);
    return b;
  }
  button('Frame', frame);
  button('Wire', () => {
    wire = !wire;
    root?.traverse((o) => { if (o.isMesh) [].concat(o.material).forEach((m) => { m.wireframe = wire; }); });
  });
  button('Box', () => {
    if (!boxHelper) return;
    boxHelper.visible = !boxHelper.visible;
  });
  button('PNG', () => {
    renderer.render(scene, camera);
    const a = document.createElement('a');
    a.href = renderer.domElement.toDataURL('image/png');
    a.download = 'view.png';
    a.click();
  });

  fetch(url, { headers: authHeaders() })
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.arrayBuffer(); })
    .then((buf) => new Promise((res, rej) => loader.parse(buf, '', res, rej)))
    .then((gltf) => {
      root = gltf.scene;
      scene.add(root);
      boxHelper = new THREE.BoxHelper(root, 0xed1c24);
      boxHelper.visible = false;
      scene.add(boxHelper);
      let tris = 0;
      root.traverse((o) => {
        if (o.isMesh) tris += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3;
      });
      const size = new THREE.Box3().setFromObject(root).getSize(new THREE.Vector3());
      let text = `${Math.round(tris).toLocaleString()} tris · ${size.x.toFixed(3)} × ${size.y.toFixed(3)} × ${size.z.toFixed(3)} (x·y·z)`;
      if (envelope?.L) {
        const s = envelope.L / Math.max(size.x, size.z);
        text += ` · at envelope L ${envelope.L} m → ${(size.x * s).toFixed(2)} × ${(size.y * s).toFixed(2)} × ${(size.z * s).toFixed(2)} m`;
      }
      hud.textContent = text;
      onStats?.({ tris, size });
      frame();
    })
    .catch((e) => { hud.textContent = `failed to load: ${e.message}`; });

  controls.addEventListener('change', () => listeners.forEach((fn) => fn()));
  let raf = 0;
  (function tick() {
    raf = requestAnimationFrame(tick);
    controls.update();
    renderer.render(scene, camera);
  })();

  return {
    camera, controls,
    onChange: (fn) => listeners.push(fn),
    dispose() { cancelAnimationFrame(raf); ro.disconnect(); renderer.dispose(); pmrem.dispose(); },
  };
}

/** Keep cameras in step across compare viewers, in normalised model space. */
export function syncViewers(viewers) {
  let syncing = false;
  viewers.forEach((leader) => {
    leader.onChange(() => {
      if (syncing) return;
      syncing = true;
      const offset = leader.camera.position.clone().sub(leader.controls.target);
      const dist = offset.length();
      viewers.forEach((v) => {
        if (v === leader) return;
        const own = v.camera.position.distanceTo(v.controls.target) || dist;
        v.camera.position.copy(v.controls.target).add(offset.clone().setLength(own));
        v.controls.update();
      });
      syncing = false;
    });
  });
}
