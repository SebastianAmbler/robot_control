import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// --- 1. SETUP SCENE ---
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1a1a);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 1, 5000);
camera.position.set(500, 500, 500);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);
document.body.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
scene.add(new THREE.GridHelper(1000, 20, 0x444444, 0x222222));
scene.add(new THREE.AmbientLight(0xffffff, 0.9));

const dirLight = new THREE.DirectionalLight(0xffffff, 1);
dirLight.position.set(100, 200, 100);
scene.add(dirLight);

// --- 2. UI STATUS (แสดงสถานะปุ่มกด) ---
const uiDiv = document.createElement('div');
uiDiv.style.position = 'absolute';
uiDiv.style.top = '20px';
uiDiv.style.left = '20px';
uiDiv.style.color = '#0f0';
uiDiv.style.fontFamily = 'monospace';
uiDiv.style.backgroundColor = 'rgba(0,0,0,0.7)';
uiDiv.style.padding = '10px';
uiDiv.innerHTML = `
    <b>CONTROLS:</b><br>
    [Y] Select FRONT Legs<br>
    [U] Select BACK Legs<br>
    [+ / -] Hold to Rotate<br>
    ----------------<br>
    SELECTED: <span id="selText" style="color:yellow">FRONT</span>
`;
document.body.appendChild(uiDiv);
const selText = document.getElementById('selText');


// --- 3. ROBOT GROUPS & VARIABLES ---
const robotGroup = new THREE.Group();
robotGroup.rotation.order = 'YXZ'; 
robotGroup.rotation.x = -Math.PI / 2; // นอนราบ
scene.add(robotGroup);

// สร้าง Array เก็บชิ้นส่วนขาเพื่อเอาไว้สั่งหมุน
const frontLegs = []; // เก็บขาคู่หน้า
const backLegs = [];  // เก็บขาคู่หลัง

// ตัวแปรควบคุม
let selectedPart = 'front'; // 'front' หรือ 'back'
let moveDirection = 0;      // 0=นิ่ง, 1=บวก, -1=ลบ

// --- 4. KEYBOARD EVENTS (INPUT) ---
window.addEventListener('keydown', (e) => {
    const key = e.key.toLowerCase();

    // เลือกขา
    if (key === 'y') {
        selectedPart = 'front';
        selText.innerText = "FRONT (Leg2)";
        selText.style.color = "yellow";
    }
    if (key === 'u') {
        selectedPart = 'back';
        selText.innerText = "BACK (Leg4)";
        selText.style.color = "cyan";
    }

    // สั่งเคลื่อนที่ (กดค้าง)
    if (key === '=' || key === '+') {
        moveDirection = 1;
    }
    if (key === '-' || key === '_') {
        moveDirection = -1;
    }
});

window.addEventListener('keyup', (e) => {
    const key = e.key.toLowerCase();
    // หยุดเคลื่อนที่เมื่อปล่อยปุ่ม
    if (['=', '+', '-', '_'].includes(key)) {
        moveDirection = 0;
    }
});


// --- 5. LOAD MODELS ---
const loader = new STLLoader();

function createMesh(geometry, color) {
    const material = new THREE.MeshPhongMaterial({ 
        color: color, 
        specular: 0x111111, 
        shininess: 80 
    });
    return new THREE.Mesh(geometry, material);
}

// 5.1 Center Body
loader.load('./models/STLForsimJ.center.stl', (geo) => {
    const centerMesh = createMesh(geo, 0xff0000); 
    robotGroup.add(centerMesh);
});

// 5.2 LEGS SETUP
const legSettings = [
    { file: 'leg2.stl', x: 50,  z: 80,  mirror: false, type: 'front' },
    { file: 'leg2.stl', x: -50, z: 80,  mirror: true,  type: 'front' },
    { file: 'leg4.stl', x: 50,  z: -80, mirror: false, type: 'back' },
    { file: 'leg4.stl', x: -50, z: -80, mirror: true,  type: 'back' }
];

legSettings.forEach(config => {
    loader.load(`./models/${config.file}`, (geo) => {
        const legMesh = createMesh(geo, 0x00ff00);
        
        // จัดตำแหน่ง
        legMesh.position.set(config.x, 0, config.z);
        if (config.mirror) legMesh.scale.x = -1;

        // เพิ่มเข้ากลุ่มหลัก
        robotGroup.add(legMesh);

        // **สำคัญ: เก็บ Object ขาลง Array ตามประเภท**
        if (config.type === 'front') {
            frontLegs.push(legMesh);
        } else {
            backLegs.push(legMesh);
        }
    });
});

// 5.3 ARMS
const arm1Group = new THREE.Group();
const arm2Group = new THREE.Group();
const arm3Group = new THREE.Group();

// Expose to global scope so WebSocket handler can access them
window.arm1Group  = arm1Group;
window.arm2Group  = arm2Group;
window.arm3Group  = arm3Group;
window.frontLegs  = frontLegs;
window.backLegs   = backLegs;

loader.load('./models/STLFORSIM.Arm1.stl', (geo) => {
    const m = createMesh(geo, 0xffa500);
    m.rotation.z = Math.PI; // flip arm1 to face backward
    arm1Group.add(m);
    robotGroup.add(arm1Group);
});
loader.load('./models/STLFORSIM.Arm2.stl', (geo) => {
    const m = createMesh(geo, 0x00ffff);
    arm2Group.add(m);
    arm1Group.add(arm2Group);
});
loader.load('./models/STLFORSIM.Arm3.stl', (geo) => {
    const m = createMesh(geo, 0xffa500);
    arm3Group.add(m);
    arm2Group.add(arm3Group);
});
loader.load('./models/STLFORSIM.Gripper1.stl', (geo) => arm3Group.add(createMesh(geo, 0xff00ff)));
loader.load('./models/STLFORSIM.Gripper2.stl', (geo) => arm3Group.add(createMesh(geo, 0xffc0cb)));


// --- 6. SERIAL CONNECTION ---
const connectBtn = document.getElementById('connectBtn'); // ถ้ามีปุ่มใน HTML
if(connectBtn) {
    connectBtn.addEventListener('click', async () => {
        // ... (โค้ด Serial เดิมของคุณ ใส่ตรงนี้ได้เลย) ...
        try {
            const port = await navigator.serial.requestPort();
            await port.open({ baudRate: 115200 });
            const decoder = new TextDecoderStream();
            port.readable.pipeTo(decoder.writable);
            const reader = decoder.readable.getReader();
            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                // Simple parsing for rotation
                // (ปรับปรุงตามโค้ดเดิมของคุณ)
            }
        } catch(e) { console.log(e); }
    });
}

// --- 6b. WEBSOCKET: receive live servo positions from rescue.py ---
const WS_URL = 'ws://localhost:8765';
let ws = null;

// Status indicator
const wsStatus = document.createElement('div');
wsStatus.style.cssText = `
    position:absolute; top:20px; right:20px;
    color:#0f0; font-family:monospace; font-size:13px;
    background:rgba(0,0,0,0.7); padding:8px 12px; border-radius:6px;
`;
wsStatus.innerHTML = `<span id="wsDot" style="color:gray">●</span> WebSocket: <span id="wsState">Connecting...</span>
<br><br><b style="color:#aaa">SERVO ANGLES</b><br>
<div id="wsAngles" style="color:yellow; margin-top:4px; line-height:1.6"></div>`;
document.body.appendChild(wsStatus);

const wsDot   = document.getElementById('wsDot');
const wsState = document.getElementById('wsState');
const wsAngles = document.getElementById('wsAngles');

const SERVO_NAMES = [
    'Front motor', 'Back motor',
    'Arm 1', 'Arm 2', 'Arm 3', 'Arm 4', 'Arm 5',
    'Gripper'
];

function updateAngleDisplay(angles, motorState) {
    wsAngles.innerHTML = angles.map((a, i) => {
        const extra = (i === 7) ? ` <span style="color:#0ff">[${(motorState||'stop').toUpperCase()}]</span>` : '';
        return `${SERVO_NAMES[i]}: <b>${a}°</b>${extra}`;
    }).join('<br>');
}

// Servo physical limits from rescue.py
// SERVO_MINS = [  0,   0,  0, 100,  0,  45,  60, 45]
// SERVO_MAXS = [180, 180,105, 180,180, 125, 180,140]
const SERVO_MINS_VIS = [0,   0,   0,  100,  0,  45,  60, 45];
const SERVO_MAXS_VIS = [180, 180, 105, 180, 180, 125, 180, 140];

// Map a servo angle to a visual rotation in radians.
// Full servo range maps to visualRange radians (default ±90° = π total).
function servoToRad(angle, idx, visualRange = Math.PI) {
    const lo = SERVO_MINS_VIS[idx];
    const hi = SERVO_MAXS_VIS[idx];
    const t  = (angle - lo) / (hi - lo);   // 0.0 → 1.0
    return (t - 0.5) * visualRange;        // centered at 0
}

function applyServoAngles(angles) {
    if (!angles || angles.length < 8) return;

    // Use window.* to guarantee access across ES module scope
    if (window.frontLegs)  window.frontLegs.forEach(leg => { leg.rotation.x = servoToRad(angles[0], 0); });
    if (window.backLegs)   window.backLegs.forEach(leg  => { leg.rotation.x = servoToRad(angles[1], 1); });
    if (window.arm1Group)  window.arm1Group.rotation.x  = servoToRad(angles[2], 2);
    if (window.arm2Group)  window.arm2Group.rotation.x  = servoToRad(angles[3], 3);
    if (window.arm3Group)  window.arm3Group.rotation.x  = servoToRad(angles[4], 4);

    console.log('[WS] angles:', angles.join(', '));
}

function connectWS() {
    ws = new WebSocket(WS_URL);

    ws.addEventListener('open', () => {
        wsDot.style.color   = '#0f0';
        wsState.textContent = 'Connected';
        console.log('[WS] Connected to rescue.py');
    });

    ws.addEventListener('message', (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type !== 'servo') return;
            applyServoAngles(data.angles);
            updateAngleDisplay(data.angles, data.motor_state);
        } catch(e) {
            console.warn('[WS] Parse error:', e);
        }
    });

    ws.addEventListener('close', () => {
        wsDot.style.color   = 'red';
        wsState.textContent = 'Disconnected — retrying in 3s...';
        console.log('[WS] Disconnected, retrying...');
        setTimeout(connectWS, 3000);
    });

    ws.addEventListener('error', (e) => {
        wsDot.style.color   = 'orange';
        wsState.textContent = 'Error';
        console.warn('[WS] Error:', e);
    });
}

connectWS();

// --- 7. ANIMATION LOOP ---
// Rotations are now driven by WebSocket servo data in applyServoAngles().
function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
}
animate();

window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});