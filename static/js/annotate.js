/**
 * annotate.js - Canvas-based keypoint annotation
 * 생각 흐름:
 * 1. 이미지 로드 → canvas 크기 맞춤
 * 2. keypoints[] 배열로 상태 관리 (kp_id, x, y, visible: 0=없음 1=occluded 2=visible)
 * 3. 클릭 → 현재 선택 kp에 좌표 저장 → 다음 kp 자동선택
 * 4. 매 상태 변경마다 redraw()
 * 5. 줌/패닝: transform matrix로 canvas 좌표 ↔ 이미지 좌표 변환
 */

// ─── State ───────────────────────────────────────────────
const canvas = document.getElementById('canvas');
const ctx    = canvas.getContext('2d');
const img    = new Image();

let keypoints = SCHEMA.map(kp => ({
  kp_id: kp.id, name: kp.name, color: kp.color,
  x: null, y: null, visible: 0
}));

let bbox       = null;   // {x, y, w, h} in image coords
let selectedKP = 0;      // currently selected keypoint index
let mode       = 'keypoint'; // 'keypoint' | 'bbox'
let isDragging = false;
let dragTarget = null;   // {type:'kp', idx} | {type:'bbox_corner', corner}
let bboxStart  = null;

// Zoom/pan
let scale     = 1;
let offsetX   = 0;
let offsetY   = 0;
let isPanning = false;
let panStart  = null;

// ─── Init ─────────────────────────────────────────────────
img.onload = function() {
  document.getElementById('canvas-hint').style.display = 'none';
  fitToCanvas();

  // Load existing
  if (EXISTING && Array.isArray(EXISTING)) {
    EXISTING.forEach(e => {
      const kp = keypoints.find(k => k.kp_id === e.kp_id);
      if (kp) { kp.x = e.x; kp.y = e.y; kp.visible = e.visible ?? 2; }
    });
  }
  if (EXISTING_BBOX) bbox = {...EXISTING_BBOX};
  if (EXISTING_NOTES) document.getElementById('notes').value = EXISTING_NOTES;

  redraw();
  updatePanel();
  // Auto-select first unlabeled kp
  const firstEmpty = keypoints.findIndex(k => k.visible === 0);
  if (firstEmpty >= 0) selectKP(firstEmpty);
};
img.src = IMG_URL;

function fitToCanvas() {
  const wrap = document.getElementById('canvas-wrap');
  const maxW = wrap.clientWidth;
  const maxH = wrap.clientHeight;
  scale   = Math.min(maxW / IMG_W, maxH / IMG_H) * 0.95;
  offsetX = (maxW - IMG_W * scale) / 2;
  offsetY = (maxH - IMG_H * scale) / 2;
  canvas.width  = maxW;
  canvas.height = maxH;
}

// ─── Coord helpers ────────────────────────────────────────
function imgToCanvas(x, y) {
  return [x * scale + offsetX, y * scale + offsetY];
}
function canvasToImg(cx, cy) {
  return [(cx - offsetX) / scale, (cy - offsetY) / scale];
}
function clampImg(x, y) {
  return [Math.max(0, Math.min(IMG_W, x)), Math.max(0, Math.min(IMG_H, y))];
}

// ─── Draw ─────────────────────────────────────────────────
function redraw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Background
  ctx.fillStyle = '#1a1a2e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Image
  ctx.drawImage(img, offsetX, offsetY, IMG_W * scale, IMG_H * scale);

  // BBox
  if (bbox) {
    const [bx, by] = imgToCanvas(bbox.x, bbox.y);
    ctx.strokeStyle = '#00e5ff';
    ctx.lineWidth   = 2;
    ctx.setLineDash([6, 3]);
    ctx.strokeRect(bx, by, bbox.w * scale, bbox.h * scale);
    ctx.setLineDash([]);
  }

  // Skeleton connections
  CONNECTIONS.forEach(([a, b]) => {
    const kpA = keypoints[a], kpB = keypoints[b];
    if (kpA.visible > 0 && kpB.visible > 0 &&
        kpA.x !== null && kpB.x !== null) {
      const [ax, ay] = imgToCanvas(kpA.x, kpA.y);
      const [bx, by] = imgToCanvas(kpB.x, kpB.y);
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.strokeStyle = 'rgba(255,255,255,0.35)';
      ctx.lineWidth   = 1.5;
      ctx.stroke();
    }
  });

  // Keypoints
  keypoints.forEach((kp, idx) => {
    if (kp.visible === 0 || kp.x === null) return;
    const [cx, cy] = imgToCanvas(kp.x, kp.y);
    const r         = idx === selectedKP ? 9 : 6;
    const isOcc     = kp.visible === 1;

    // Outer ring for selected
    if (idx === selectedKP) {
      ctx.beginPath();
      ctx.arc(cx, cy, r + 4, 0, Math.PI * 2);
      ctx.strokeStyle = 'white';
      ctx.lineWidth   = 2;
      ctx.stroke();
    }

    // Fill
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = isOcc ? 'rgba(0,0,0,0.5)' : kp.color;
    ctx.fill();
    ctx.strokeStyle = kp.color;
    ctx.lineWidth   = 2;
    ctx.stroke();

    // X for occluded
    if (isOcc) {
      ctx.strokeStyle = kp.color;
      ctx.lineWidth   = 1.5;
      ctx.beginPath();
      ctx.moveTo(cx - 4, cy - 4); ctx.lineTo(cx + 4, cy + 4);
      ctx.moveTo(cx + 4, cy - 4); ctx.lineTo(cx - 4, cy + 4);
      ctx.stroke();
    }

    // Label
    ctx.fillStyle   = 'white';
    ctx.font        = `${Math.max(10, 11 * scale)}px monospace`;
    ctx.fillText(kp.name, cx + r + 3, cy + 4);
  });

  // Crosshair for active kp if not placed yet
  if (mode === 'keypoint' && keypoints[selectedKP]?.visible === 0) {
    ctx.strokeStyle = SCHEMA[selectedKP]?.color || 'white';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    const mid = canvas.width / 2;
    // small indicator at panel edge
    ctx.beginPath();
    ctx.arc(60, 60, 12, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = SCHEMA[selectedKP]?.color || 'white';
    ctx.font = '11px sans-serif';
    ctx.fillText(SCHEMA[selectedKP]?.name || '', 78, 65);
  }
}

// ─── Panel update ─────────────────────────────────────────
function updatePanel() {
  keypoints.forEach((kp, idx) => {
    const item = document.getElementById(`kp-item-${kp.kp_id}`);
    const stat = document.getElementById(`kp-status-${kp.kp_id}`);
    if (!item || !stat) return;
    item.classList.toggle('kp-selected', idx === selectedKP);
    if (kp.visible === 0 || kp.x === null) {
      stat.textContent = '—';
      stat.style.color = '#555';
    } else if (kp.visible === 1) {
      stat.textContent = '👁️‍🗨️';
      stat.style.color = '#aaa';
    } else {
      stat.textContent = `${Math.round(kp.x)},${Math.round(kp.y)}`;
      stat.style.color = kp.color;
    }
  });
  // scroll selected into view
  const el = document.getElementById(`kp-item-${SCHEMA[selectedKP]?.id}`);
  if (el) el.scrollIntoView({block:'nearest'});
}

// ─── Interaction ──────────────────────────────────────────
function getHitKP(cx, cy, radius = 12) {
  for (let i = keypoints.length - 1; i >= 0; i--) {
    const kp = keypoints[i];
    if (kp.x === null) continue;
    const [kx, ky] = imgToCanvas(kp.x, kp.y);
    if (Math.hypot(cx - kx, cy - ky) < radius) return i;
  }
  return -1;
}

canvas.addEventListener('mousedown', e => {
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;

  // Middle click or space+drag = pan
  if (e.button === 1) {
    isPanning = true;
    panStart = {cx, cy, ox: offsetX, oy: offsetY};
    return;
  }

  if (e.button === 2) {
    // Right click: toggle occluded on nearest kp
    const hit = getHitKP(cx, cy);
    if (hit >= 0) {
      keypoints[hit].visible = keypoints[hit].visible === 2 ? 1 : 2;
      redraw(); updatePanel();
    } else if (mode === 'keypoint' && keypoints[selectedKP].x !== null) {
      keypoints[selectedKP].visible =
        keypoints[selectedKP].visible === 2 ? 1 : 2;
      redraw(); updatePanel();
    }
    return;
  }

  // Left click
  if (mode === 'keypoint') {
    const hit = getHitKP(cx, cy);
    if (hit >= 0) {
      // Start drag
      isDragging = true;
      dragTarget = {type: 'kp', idx: hit};
      selectedKP = hit;
      updatePanel();
    } else {
      // Place keypoint
      let [ix, iy] = canvasToImg(cx, cy);
      [ix, iy] = clampImg(ix, iy);
      keypoints[selectedKP].x = ix;
      keypoints[selectedKP].y = iy;
      keypoints[selectedKP].visible = 2;
      redraw(); updatePanel();
      // Auto-advance to next empty
      const nextEmpty = keypoints.findIndex((k, i) => i > selectedKP && k.visible === 0);
      if (nextEmpty >= 0) selectKP(nextEmpty);
    }
  } else if (mode === 'bbox') {
    let [ix, iy] = canvasToImg(cx, cy);
    bboxStart = clampImg(ix, iy);
    bbox = {x: bboxStart[0], y: bboxStart[1], w: 0, h: 0};
    isDragging = true;
    dragTarget = {type: 'bbox_draw'};
  }
});

canvas.addEventListener('mousemove', e => {
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;

  if (isPanning && panStart) {
    offsetX = panStart.ox + (cx - panStart.cx);
    offsetY = panStart.oy + (cy - panStart.cy);
    redraw(); return;
  }

  if (!isDragging || !dragTarget) return;

  if (dragTarget.type === 'kp') {
    let [ix, iy] = canvasToImg(cx, cy);
    [ix, iy] = clampImg(ix, iy);
    keypoints[dragTarget.idx].x = ix;
    keypoints[dragTarget.idx].y = iy;
    redraw(); updatePanel();
  } else if (dragTarget.type === 'bbox_draw' && bboxStart) {
    let [ix, iy] = canvasToImg(cx, cy);
    [ix, iy] = clampImg(ix, iy);
    bbox = {
      x: Math.min(bboxStart[0], ix), y: Math.min(bboxStart[1], iy),
      w: Math.abs(ix - bboxStart[0]),  h: Math.abs(iy - bboxStart[1])
    };
    redraw();
  }
});

canvas.addEventListener('mouseup', () => {
  isDragging = false; dragTarget = null; isPanning = false; panStart = null;
});

canvas.addEventListener('contextmenu', e => e.preventDefault());

// Zoom with wheel
canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.1 : 0.91;
  const newScale = Math.max(0.2, Math.min(8, scale * factor));
  offsetX = cx - (cx - offsetX) * (newScale / scale);
  offsetY = cy - (cy - offsetY) * (newScale / scale);
  scale = newScale;
  redraw();
}, {passive: false});

// Window resize
window.addEventListener('resize', () => { fitToCanvas(); redraw(); });

// ─── Controls ─────────────────────────────────────────────
function selectKP(idx) {
  if (idx < 0 || idx >= keypoints.length) return;
  selectedKP = idx;
  redraw(); updatePanel();
}

function setMode(m) {
  mode = m;
  document.getElementById('mode-kp').classList.toggle('active', m === 'keypoint');
  document.getElementById('mode-bbox').classList.toggle('active', m === 'bbox');
  document.getElementById('mode-hint').textContent =
    m === 'keypoint'
      ? '키포인트를 순서대로 클릭하세요. 우클릭=안보임'
      : '드래그로 바운딩박스를 그리세요';
}

function resetAll() {
  if (!confirm('이 프레임의 모든 키포인트를 초기화할까요?')) return;
  keypoints.forEach(k => { k.x = null; k.y = null; k.visible = 0; });
  bbox = null;
  selectKP(0);
  redraw(); updatePanel();
}

// ─── Save ─────────────────────────────────────────────────
async function saveAnnotation() {
  const btn = document.getElementById('btn-save');
  const status = document.getElementById('save-status');
  btn.disabled = true;
  btn.textContent = '저장중...';

  const payload = {
    frame_id: FRAME_ID,
    keypoints: keypoints.map(k => ({
      kp_id: k.kp_id, x: k.x ?? 0, y: k.y ?? 0, visible: k.visible
    })),
    bbox: bbox,
    notes: document.getElementById('notes').value
  };

  try {
    const r = await fetch('/api/annotations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (d.status === 'saved') {
      status.textContent = '✅ 저장됨!';
      status.style.color = '#44ff88';
      btn.textContent = '💾 저장 (Enter)';
      btn.disabled = false;
      // Auto-advance
      if (NEXT_ID) {
        setTimeout(() => {
          window.location.href = `/annotate/${NEXT_ID}`;
        }, 600);
      }
    }
  } catch(err) {
    status.textContent = '❌ 저장 실패: ' + err;
    btn.textContent = '💾 저장 (Enter)';
    btn.disabled = false;
  }
}

// ─── Keyboard ─────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;

  if (e.key === 'Enter') { e.preventDefault(); saveAnnotation(); }
  else if (e.key === 'ArrowRight') {
    const btn = document.getElementById('btn-next');
    if (btn && btn.href) window.location.href = btn.href;
  }
  else if (e.key === 'ArrowLeft') { history.back(); }
  else if (e.key === 'Escape')   { selectKP(-1); }
  else if (e.key === 'r' || e.key === 'R') {
    // Reset selected kp
    if (selectedKP >= 0) {
      keypoints[selectedKP].x = null;
      keypoints[selectedKP].y = null;
      keypoints[selectedKP].visible = 0;
      redraw(); updatePanel();
    }
  }
  else if (e.key === 'f' || e.key === 'F') {
    // Fit to screen
    fitToCanvas(); redraw();
  }
  else if (e.key >= '1' && e.key <= '9') {
    selectKP(parseInt(e.key) - 1);
  }
});
