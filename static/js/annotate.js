/**
 * annotate.js v3 — violin_bowing_scene · 9 keypoints
 * visibility: 2=visible  1=occluded  3=outside  0=미처리
 * B=bbox  V/O/U=가시/가림/프레임밖  C=이전복사
 */

const VIS_UNSET    = 0;
const VIS_OCCLUDED = 1;
const VIS_VISIBLE  = 2;
const VIS_OUTSIDE  = 3;

const POINT_RADIUS   = 7;
const POINT_SELECTED = 11;
const HIT_RADIUS     = 15;
const MIN_SCALE      = 0.1;
const MAX_SCALE      = 14;

const canvas = document.getElementById('canvas');
const ctx    = canvas.getContext('2d');
const img    = new Image();

let keypoints = SCHEMA.map(kp => ({
  kp_id: kp.id, name: kp.name, color: kp.color, group: kp.group,
  label_short: kp.label_short || kp.name,
  desc: kp.desc || '',
  x: null, y: null, visible: VIS_UNSET,
}));

let bbox = null, selectedKP = 0, mode = 'keypoint';
let groupFilter = null;
let isDragging = false, dragTarget = null, bboxStart = null;
let isPanning = false, panStart = null;
let scale = 1, offsetX = 0, offsetY = 0;
let mouseImgPos = {x: 0, y: 0};

fetch(`/api/frames/${FRAME_ID}/start`, {method: 'POST'}).catch(() => {});

img.onload = () => {
  fitToCanvas();
  loadExisting();
  selectedKP = keypoints.findIndex(k => k.visible === VIS_UNSET);
  if (selectedKP < 0) selectedKP = 0;
  redraw(); updatePanel(); updateProgress(); updateCurrentCard();
};
img.onerror = () => { document.getElementById('canvas-hint').textContent = '로드 실패'; };
img.src = IMG_URL;

function loadExisting() {
  if (EXISTING?.keypoints) {
    EXISTING.keypoints.forEach(e => {
      const kp = keypoints.find(k => k.kp_id === e.kp_id);
      if (kp) {
        kp.x = e.x; kp.y = e.y; kp.visible = e.visible ?? VIS_UNSET;
      }
    });
  } else if (Array.isArray(EXISTING)) {
    EXISTING.forEach(e => {
      const kp = keypoints.find(k => k.kp_id === e.kp_id);
      if (kp) { kp.x = e.x; kp.y = e.y; kp.visible = e.visible ?? VIS_UNSET; }
    });
  }
  if (EXISTING_BBOX) bbox = {...EXISTING_BBOX};
  if (EXISTING_NOTES) document.getElementById('notes').value = EXISTING_NOTES;
  if (EXISTING_QUALITY) document.getElementById('quality').value = EXISTING_QUALITY;
}

function activeIndices() {
  return keypoints.map((_, i) => i).filter(i => {
    if (groupFilter && keypoints[i].group !== groupFilter) return false;
    return true;
  });
}

function isAddressed(kp) { return kp.visible !== VIS_UNSET; }

function imgToCanvas(x, y) { return [x * scale + offsetX, y * scale + offsetY]; }
function canvasToImg(cx, cy) { return [(cx - offsetX) / scale, (cy - offsetY) / scale]; }
function clampImg(x, y) { return [Math.max(0, Math.min(IMG_W, x)), Math.max(0, Math.min(IMG_H, y))]; }

function fitToCanvas() {
  const wrap = document.getElementById('canvas-wrap');
  canvas.width = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
  const s = Math.min(canvas.width / IMG_W, canvas.height / IMG_H) * 0.94;
  scale = s;
  offsetX = (canvas.width - IMG_W * s) / 2;
  offsetY = (canvas.height - IMG_H * s) / 2;
}

function visLabel(v) {
  return {0: '미처리', 1: '가림', 2: '가시', 3: '밖'}[v] || '?';
}

function redraw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#080810';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (img.complete) ctx.drawImage(img, offsetX, offsetY, IMG_W * scale, IMG_H * scale);

  if (bbox?.w > 0 && bbox?.h > 0) {
    const [bx, by] = imgToCanvas(bbox.x, bbox.y);
    ctx.save();
    ctx.strokeStyle = '#00d4ff';
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(bx, by, bbox.w * scale, bbox.h * scale);
    ctx.fillStyle = 'rgba(0,212,255,0.06)';
    ctx.fillRect(bx, by, bbox.w * scale, bbox.h * scale);
    ctx.restore();
  }

  CONNECTIONS.forEach(([a, b]) => {
    const A = keypoints[a], B = keypoints[b];
    if (!A || !B || A.visible !== VIS_VISIBLE || B.visible !== VIS_VISIBLE) return;
    if (A.x == null || B.x == null) return;
    const [ax, ay] = imgToCanvas(A.x, A.y);
    const [bx, by2] = imgToCanvas(B.x, B.y);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by2);
    ctx.strokeStyle = A.group === 'bow' ? 'rgba(255,120,50,0.7)' : 'rgba(150,200,180,0.45)';
    ctx.lineWidth = 1.5; ctx.stroke();
  });

  keypoints.forEach((kp, idx) => {
    if (groupFilter && kp.group !== groupFilter) return;
    if (!isAddressed(kp)) return;
    if (kp.visible === VIS_OUTSIDE) return;
    if (kp.visible === VIS_OCCLUDED && kp.x == null) return;

    const [cx, cy] = imgToCanvas(kp.x ?? 0, kp.y ?? 0);
    const isSel = idx === selectedKP;
    const isOcc = kp.visible === VIS_OCCLUDED;
    const r = isSel ? POINT_SELECTED : POINT_RADIUS;

    if (isSel) {
      ctx.beginPath(); ctx.arc(cx, cy, r + 5, 0, Math.PI * 2);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
    }
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = isOcc ? 'rgba(25,25,35,0.9)' : kp.color;
    ctx.fill();
    if (isOcc) {
      ctx.strokeStyle = kp.color; ctx.lineWidth = 2; ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx-4,cy-4); ctx.lineTo(cx+4,cy+4);
      ctx.moveTo(cx+4,cy-4); ctx.lineTo(cx-4,cy+4);
      ctx.stroke();
    }
  });

  const cur = keypoints[selectedKP];
  if (mode === 'keypoint' && cur?.visible === VIS_UNSET && mouseImgPos.x > 0) {
    const [gx, gy] = imgToCanvas(mouseImgPos.x, mouseImgPos.y);
    ctx.beginPath(); ctx.arc(gx, gy, POINT_RADIUS, 0, Math.PI * 2);
    ctx.strokeStyle = cur.color; ctx.lineWidth = 2; ctx.setLineDash([3,3]); ctx.stroke();
    ctx.setLineDash([]);
  }
}

function updateCurrentCard() {
  const cur = keypoints[selectedKP];
  if (!cur) return;
  const el = id => document.getElementById(id);
  if (el('kp-current-name')) el('kp-current-name').textContent = `${selectedKP + 1}. ${cur.label_short}`;
  if (el('kp-current-sub')) el('kp-current-sub').textContent = cur.name;
  if (el('kp-current-desc')) el('kp-current-desc').textContent = cur.desc;
  if (el('kp-current-vis')) el('kp-current-vis').textContent = visLabel(cur.visible);
  if (el('kp-current-vis')) el('kp-current-vis').className = 'vis-badge vis-' + cur.visible;
}

function updatePanel() {
  keypoints.forEach((kp, idx) => {
    const item = document.getElementById(`kp-item-${kp.kp_id}`);
    const stat = document.getElementById(`kp-status-${kp.kp_id}`);
    if (!item || !stat) return;
    item.classList.toggle('kp-selected', idx === selectedKP);
    item.classList.toggle('kp-dimmed', groupFilter && kp.group !== groupFilter);
    item.classList.toggle('kp-done', isAddressed(kp));

    if (!isAddressed(kp)) { stat.textContent = '—'; stat.style.color = '#555'; }
    else if (kp.visible === VIS_VISIBLE) { stat.textContent = '●'; stat.style.color = kp.color; }
    else if (kp.visible === VIS_OCCLUDED) { stat.textContent = '◐'; stat.style.color = '#aaa'; }
    else if (kp.visible === VIS_OUTSIDE) { stat.textContent = '○'; stat.style.color = '#666'; }
  });
  const el = document.getElementById(`kp-item-${SCHEMA[selectedKP]?.id}`);
  if (el) el.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  updateCurrentCard();
}

function updateProgress() {
  const addressed = keypoints.filter(isAddressed).length;
  const visible = keypoints.filter(k => k.visible === VIS_VISIBLE).length;
  const bar = document.getElementById('kp-progress-bar');
  const label = document.getElementById('kp-progress-label');
  const pct = Math.round(addressed / keypoints.length * 100);
  if (bar) bar.style.width = pct + '%';
  if (label) label.textContent = `처리 ${addressed}/9 · 가시 ${visible}`;
  const bboxEl = document.getElementById('bbox-status');
  if (bboxEl) bboxEl.textContent = bbox?.w > 0 ? 'bbox ✓' : 'bbox 필요';
  if (bboxEl) bboxEl.style.color = bbox?.w > 0 ? 'var(--green)' : 'var(--warn)';
}

function setVisState(vis) {
  const kp = keypoints[selectedKP];
  kp.visible = vis;
  if (vis === VIS_OUTSIDE) { kp.x = 0; kp.y = 0; }
  if (vis === VIS_OCCLUDED && kp.x == null) { kp.x = 0; kp.y = 0; }
  advanceToNext();
  redraw(); updatePanel(); updateProgress();
}

function advanceToNext() {
  const indices = activeIndices();
  const pos = indices.indexOf(selectedKP);
  const next = indices.slice(pos + 1).find(i => !isAddressed(keypoints[i]));
  if (next !== undefined) selectedKP = next;
}

canvas.addEventListener('mousemove', e => {
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
  mouseImgPos = Object.fromEntries(['x','y'].map((k,i) => [k, canvasToImg(cx,cy)[i]]));
  if (isPanning && panStart) {
    offsetX = panStart.ox + (cx - panStart.cx);
    offsetY = panStart.oy + (cy - panStart.cy);
    redraw(); return;
  }
  if (isDragging && dragTarget?.type === 'kp') {
    const [nx, ny] = clampImg(...canvasToImg(cx, cy));
    const kp = keypoints[dragTarget.idx];
    kp.x = nx; kp.y = ny;
    if (kp.visible === VIS_UNSET) kp.visible = VIS_VISIBLE;
    redraw(); updatePanel(); return;
  }
  if (isDragging && dragTarget?.type === 'bbox' && bboxStart) {
    const [nx, ny] = clampImg(...canvasToImg(cx, cy));
    bbox = { x: Math.min(bboxStart[0], nx), y: Math.min(bboxStart[1], ny),
             w: Math.abs(nx - bboxStart[0]), h: Math.abs(ny - bboxStart[1]) };
    redraw(); updateProgress(); return;
  }
  canvas.style.cursor = mode === 'bbox' ? 'crosshair' : (getHitKP(cx,cy) >= 0 ? 'move' : 'crosshair');
  if (mode === 'keypoint') redraw();
});

canvas.addEventListener('mousedown', e => {
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
  const [ix, iy] = canvasToImg(cx, cy);
  if (e.button === 1) { isPanning = true; panStart = {cx,cy,ox:offsetX,oy:offsetY}; e.preventDefault(); return; }

  if (mode === 'bbox') {
    const [nx, ny] = clampImg(ix, iy);
    bboxStart = [nx, ny];
    bbox = {x: nx, y: ny, w: 0, h: 0};
    isDragging = true; dragTarget = {type: 'bbox'};
    return;
  }

  const hit = getHitKP(cx, cy);
  if (hit >= 0) {
    isDragging = true; dragTarget = {type: 'kp', idx: hit}; selectedKP = hit; updatePanel();
  } else if (keypoints[selectedKP].visible === VIS_UNSET || keypoints[selectedKP].visible === VIS_VISIBLE) {
    const [nx, ny] = clampImg(ix, iy);
    keypoints[selectedKP].x = nx; keypoints[selectedKP].y = ny;
    keypoints[selectedKP].visible = VIS_VISIBLE;
    redraw(); updatePanel(); updateProgress(); advanceToNext();
  }
});

canvas.addEventListener('mouseup', () => { isDragging = false; dragTarget = null; isPanning = false; panStart = null; redraw(); });
canvas.addEventListener('contextmenu', e => { e.preventDefault(); setVisState(VIS_OCCLUDED); });
canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
  const ns = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * (e.deltaY < 0 ? 1.12 : 0.89)));
  offsetX = cx - (cx - offsetX) * (ns / scale);
  offsetY = cy - (cy - offsetY) * (ns / scale);
  scale = ns; redraw();
}, {passive: false});
window.addEventListener('resize', () => { fitToCanvas(); redraw(); });

function getHitKP(cx, cy) {
  for (let i = keypoints.length - 1; i >= 0; i--) {
    const kp = keypoints[i];
    if (kp.visible !== VIS_VISIBLE && kp.visible !== VIS_OCCLUDED) continue;
    if (kp.x == null) continue;
    const [kx, ky] = imgToCanvas(kp.x, kp.y);
    if (Math.hypot(cx - kx, cy - ky) < HIT_RADIUS) return i;
  }
  return -1;
}

function selectKP(idx) { if (idx >= 0 && idx < keypoints.length) { selectedKP = idx; redraw(); updatePanel(); } }
function setGroupFilter(g) {
  groupFilter = g === 'all' ? null : g;
  document.querySelectorAll('.group-filter-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.group === (g || 'all')));
  redraw(); updatePanel();
}
function setMode(m) {
  mode = m;
  document.getElementById('mode-kp')?.classList.toggle('active', m === 'keypoint');
  document.getElementById('mode-bbox')?.classList.toggle('active', m === 'bbox');
  document.getElementById('mode-hint').textContent =
    m === 'bbox' ? 'bbox: 9점+활 전체 포함' : '클릭=가시  우클릭=가림  U=프레임밖';
}

function resetAll() {
  if (!confirm('초기화?')) return;
  keypoints.forEach(k => { k.x = null; k.y = null; k.visible = VIS_UNSET; });
  bbox = null; selectedKP = 0;
  redraw(); updatePanel(); updateProgress();
}

async function copyFromPrev() {
  const st = document.getElementById('save-status');
  st.textContent = '복사중...';
  try {
    const d = await (await fetch(`/api/annotations/prev/${FRAME_ID}`)).json();
    if (!d?.keypoints) { st.textContent = '이전 없음'; return; }
    d.keypoints.forEach(e => {
      const kp = keypoints.find(k => k.kp_id === e.kp_id);
      if (kp && e.visible !== VIS_UNSET) { kp.x = e.x; kp.y = e.y; kp.visible = e.visible; }
    });
    if (d.bbox) bbox = {...d.bbox};
    selectedKP = keypoints.findIndex(k => !isAddressed(k));
    if (selectedKP < 0) selectedKP = 0;
    redraw(); updatePanel(); updateProgress();
    st.textContent = '복사됨'; st.style.color = 'var(--green)';
  } catch (e) { st.textContent = e.message; }
}

async function saveAnnotation(autoAdvance = true) {
  const st = document.getElementById('save-status');
  const quality = document.getElementById('quality')?.value || null;
  try {
    const r = await fetch('/api/annotations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        frame_id: FRAME_ID,
        keypoints: keypoints.map(k => ({ kp_id: k.kp_id, x: k.x??0, y: k.y??0, visible: k.visible })),
        bbox, notes: document.getElementById('notes').value, quality,
      }),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    if (d.complete) {
      st.textContent = '저장 완료'; st.style.color = 'var(--green)';
      if (autoAdvance && NEXT_ID) setTimeout(() => location.href = `/annotate/${NEXT_ID}`, 350);
    } else {
      st.textContent = '미완료: ' + (d.missing || []).join(', ');
      st.style.color = 'var(--warn)';
    }
  } catch (e) { st.textContent = e.message; st.style.color = 'var(--red)'; }
}

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  switch (e.key) {
    case 'Enter': e.preventDefault(); saveAnnotation(); break;
    case 'ArrowRight': document.getElementById('btn-next')?.href && (location.href = document.getElementById('btn-next').href); break;
    case 'ArrowLeft': PREV_ID ? location.href = `/annotate/${PREV_ID}` : history.back(); break;
    case 'c': case 'C': copyFromPrev(); break;
    case 'b': case 'B': setMode(mode === 'bbox' ? 'keypoint' : 'bbox'); break;
    case 'v': case 'V': if (keypoints[selectedKP].x != null) { keypoints[selectedKP].visible = VIS_VISIBLE; updatePanel(); redraw(); } break;
    case 'o': case 'O': setVisState(VIS_OCCLUDED); break;
    case 'u': case 'U': setVisState(VIS_OUTSIDE); break;
    case 'r': case 'R': keypoints[selectedKP].visible = VIS_UNSET; keypoints[selectedKP].x = null; keypoints[selectedKP].y = null; updatePanel(); redraw(); break;
    case 'f': case 'F': fitToCanvas(); redraw(); break;
    case 'Tab':
      e.preventDefault();
      const idx = activeIndices();
      const p = idx.indexOf(selectedKP);
      selectKP(idx[(p + 1) % idx.length]);
      break;
    case '0': selectKP(9); break;
  }
  if (e.key >= '1' && e.key <= '9') selectKP(parseInt(e.key) - 1);
});
