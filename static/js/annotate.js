/**
 * annotate.js v3.2 — save mutex, bbox handles, zoom tune
 * visibility: 0=unset  1=occluded  2=visible  3=outside
 * Space+drag / middle-click = pan · slower wheel zoom
 */

const VIS_UNSET    = 0;
const VIS_OCCLUDED = 1;
const VIS_VISIBLE  = 2;
const VIS_OUTSIDE  = 3;

const POINT_RADIUS   = 8;
const POINT_SELECTED = 12;
const HIT_RADIUS     = 16;
const MIN_SCALE      = 0.15;
const MAX_SCALE      = 18;
const ZOOM_IN        = 1.06;
const ZOOM_OUT       = 0.94;
const WHEEL_ZOOM_SENS = 0.0026;
const BBOX_HANDLE_R  = 7;
const BBOX_HANDLE_HIT = 14;
const BBOX_OPPOSITE  = {tl: 'br', tr: 'bl', bl: 'tr', br: 'tl'};
const BBOX_CURSORS   = {tl: 'nwse-resize', br: 'nwse-resize', tr: 'nesw-resize', bl: 'nesw-resize'};

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
let spaceHeld = false;
let scale = 1, offsetX = 0, offsetY = 0;
let mouseImgPos = {x: 0, y: 0};
let dirty = false;
let saveInFlight = null;

fetch(`/api/frames/${FRAME_ID}/start`, {method: 'POST'}).catch(() => {});

img.onload = () => {
  fitToCanvas();
  loadExisting();
  selectedKP = keypoints.findIndex(k => !isAddressed(k));
  if (selectedKP < 0) selectedKP = 0;
  redraw(); updatePanel(); updateProgress(); updateCurrentCard();
  setStatus('ready', '');
};
img.onerror = () => setStatus('error', 'Image load failed');
img.src = IMG_URL;

function hasCoords(kp) {
  return kp.x != null && kp.y != null && Number.isFinite(kp.x) && Number.isFinite(kp.y);
}

function isDrawable(kp) {
  return kp.visible === VIS_VISIBLE && hasCoords(kp)
    || (kp.visible === VIS_OCCLUDED && hasCoords(kp));
}

function normalizeLoadedKp(kp, e) {
  kp.visible = e.visible ?? VIS_UNSET;
  if (kp.visible === VIS_OUTSIDE) {
    kp.x = null; kp.y = null;
    return;
  }
  if (kp.visible === VIS_OCCLUDED && (e.x == null || e.y == null || (e.x === 0 && e.y === 0))) {
    kp.x = null; kp.y = null;
    return;
  }
  if (kp.visible === VIS_VISIBLE || kp.visible === VIS_OCCLUDED) {
    if (e.x != null && e.y != null && !(e.x === 0 && e.y === 0 && kp.visible === VIS_OCCLUDED)) {
      kp.x = e.x; kp.y = e.y;
    } else if (kp.visible === VIS_VISIBLE) {
      kp.x = e.x; kp.y = e.y;
    }
  }
}

function loadExisting() {
  let raw = null;
  if (EXISTING?.keypoints) raw = EXISTING.keypoints;
  else if (Array.isArray(EXISTING_KEYPOINTS)) raw = EXISTING_KEYPOINTS;

  if (raw) {
    const arr = typeof raw === 'string' ? JSON.parse(raw) : raw;
    arr.forEach(e => {
      const kp = keypoints.find(k => k.kp_id === e.kp_id);
      if (kp) normalizeLoadedKp(kp, e);
    });
  }
  if (EXISTING_BBOX && EXISTING_BBOX.w > 0) bbox = {...EXISTING_BBOX};
  if (EXISTING_NOTES) document.getElementById('notes').value = EXISTING_NOTES;
  if (EXISTING_QUALITY) document.getElementById('quality').value = EXISTING_QUALITY;
}

function isAddressed(kp) { return kp.visible !== VIS_UNSET; }

function activeIndices() {
  return keypoints.map((_, i) => i).filter(i =>
    !groupFilter || keypoints[i].group === groupFilter);
}

function imgToCanvas(x, y) { return [x * scale + offsetX, y * scale + offsetY]; }
function canvasToImg(cx, cy) { return [(cx - offsetX) / scale, (cy - offsetY) / scale]; }
function clampImg(x, y) {
  return [Math.max(0, Math.min(IMG_W, x)), Math.max(0, Math.min(IMG_H, y))];
}

function bboxCorners(b) {
  return {
    tl: {x: b.x, y: b.y},
    tr: {x: b.x + b.w, y: b.y},
    bl: {x: b.x, y: b.y + b.h},
    br: {x: b.x + b.w, y: b.y + b.h},
  };
}

function bboxFromAnchor(anchor, nx, ny) {
  const x1 = Math.min(anchor.x, nx);
  const y1 = Math.min(anchor.y, ny);
  const x2 = Math.max(anchor.x, nx);
  const y2 = Math.max(anchor.y, ny);
  return {x: x1, y: y1, w: x2 - x1, h: y2 - y1};
}

function pointInBbox(ix, iy) {
  if (!bbox?.w || !bbox?.h) return false;
  return ix >= bbox.x && ix <= bbox.x + bbox.w && iy >= bbox.y && iy <= bbox.y + bbox.h;
}

function getHitBboxHandle(cx, cy) {
  if (!bbox?.w || !bbox?.h || mode !== 'bbox') return null;
  for (const [id, pt] of Object.entries(bboxCorners(bbox))) {
    const [hx, hy] = imgToCanvas(pt.x, pt.y);
    if (Math.hypot(cx - hx, cy - hy) < BBOX_HANDLE_HIT) return id;
  }
  return null;
}

function drawBboxHandles() {
  if (!bbox?.w || !bbox?.h || mode !== 'bbox') return;
  Object.entries(bboxCorners(bbox)).forEach(([id, pt]) => {
    const [hx, hy] = imgToCanvas(pt.x, pt.y);
    ctx.fillStyle = '#00d4ff';
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(hx, hy, BBOX_HANDLE_R, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  });
}

function fitToCanvas() {
  const wrap = document.getElementById('canvas-wrap');
  canvas.width = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
  const s = Math.min(canvas.width / IMG_W, canvas.height / IMG_H) * 0.94;
  scale = s;
  offsetX = (canvas.width - IMG_W * s) / 2;
  offsetY = (canvas.height - IMG_H * s) / 2;
}

function zoomAt(cx, cy, factor) {
  const ns = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * factor));
  offsetX = cx - (cx - offsetX) * (ns / scale);
  offsetY = cy - (cy - offsetY) * (ns / scale);
  scale = ns;
  redraw();
}

function visLabel(v) {
  return {0: 'unset', 1: 'occluded', 2: 'visible', 3: 'outside'}[v] || '?';
}

function markDirty() { dirty = true; updateSaveHint(); }

function updateSaveHint() {
  const el = document.getElementById('save-hint');
  if (el) el.textContent = dirty ? '· unsaved' : '';
}

function setStatus(kind, msg) {
  const st = document.getElementById('save-status');
  if (!st) return;
  st.textContent = msg;
  st.className = 'save-status status-' + (kind || '');
}

function getMissingList() {
  const missing = [];
  keypoints.forEach(kp => {
    if (!isAddressed(kp)) missing.push(kp.label_short || kp.name);
    else if (kp.visible === VIS_VISIBLE && !hasCoords(kp)) missing.push(kp.label_short + ' (no coords)');
  });
  if (!bbox || bbox.w <= 0 || bbox.h <= 0) missing.push('bbox');
  return missing;
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
    ctx.fillStyle = 'rgba(0,212,255,0.05)';
    ctx.fillRect(bx, by, bbox.w * scale, bbox.h * scale);
    ctx.restore();
    drawBboxHandles();
  }

  CONNECTIONS.forEach(([a, b]) => {
    const A = keypoints[a], B = keypoints[b];
    if (!A || !B || !isDrawable(A) || !isDrawable(B)) return;
    const [ax, ay] = imgToCanvas(A.x, A.y);
    const [bx, by2] = imgToCanvas(B.x, B.y);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by2);
    ctx.strokeStyle = A.group === 'bow' ? 'rgba(255,120,50,0.65)' : 'rgba(150,200,180,0.4)';
    ctx.lineWidth = 1.5; ctx.stroke();
  });

  keypoints.forEach((kp, idx) => {
    if (groupFilter && kp.group !== groupFilter) return;
    if (!isDrawable(kp)) return;

    const [cx, cy] = imgToCanvas(kp.x, kp.y);
    const isSel = idx === selectedKP;
    const isOcc = kp.visible === VIS_OCCLUDED;
    const r = isSel ? POINT_SELECTED : POINT_RADIUS;

    if (isSel) {
      ctx.beginPath(); ctx.arc(cx, cy, r + 6, 0, Math.PI * 2);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
    }
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = isOcc ? 'rgba(25,25,35,0.92)' : kp.color;
    ctx.fill();
    if (isOcc) {
      ctx.strokeStyle = kp.color; ctx.lineWidth = 2; ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx - 4, cy - 4); ctx.lineTo(cx + 4, cy + 4);
      ctx.moveTo(cx + 4, cy - 4); ctx.lineTo(cx - 4, cy + 4);
      ctx.stroke();
    }
  });

  const cur = keypoints[selectedKP];
  if (mode === 'keypoint' && cur && !hasCoords(cur) && cur.visible !== VIS_OUTSIDE
      && cur.visible !== VIS_OCCLUDED && mouseImgPos.x >= 0) {
    const [gx, gy] = imgToCanvas(mouseImgPos.x, mouseImgPos.y);
    ctx.beginPath(); ctx.arc(gx, gy, POINT_RADIUS, 0, Math.PI * 2);
    ctx.strokeStyle = cur.color; ctx.lineWidth = 2; ctx.setLineDash([4, 4]); ctx.stroke();
    ctx.setLineDash([]);
  }

  if (isPanning || spaceHeld) {
    canvas.style.cursor = 'grab';
  }
}

function updateCurrentCard() {
  const cur = keypoints[selectedKP];
  if (!cur) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('kp-current-name', `${selectedKP + 1}. ${cur.label_short}`);
  set('kp-current-sub', cur.name);
  set('kp-current-desc', cur.desc);
  const visEl = document.getElementById('kp-current-vis');
  if (visEl) {
    visEl.textContent = visLabel(cur.visible);
    visEl.className = 'vis-badge vis-' + cur.visible;
  }
}

function updatePanel() {
  keypoints.forEach((kp, idx) => {
    const item = document.getElementById(`kp-item-${kp.kp_id}`);
    const stat = document.getElementById(`kp-status-${kp.kp_id}`);
    if (!item || !stat) return;
    item.classList.toggle('kp-selected', idx === selectedKP);
    item.classList.toggle('kp-dimmed', groupFilter && kp.group !== groupFilter);
    item.classList.toggle('kp-done', isAddressed(kp));

    if (!isAddressed(kp)) { stat.textContent = 'unset'; stat.style.color = '#555'; }
    else if (kp.visible === VIS_VISIBLE) { stat.textContent = 'visible'; stat.style.color = kp.color; }
    else if (kp.visible === VIS_OCCLUDED) { stat.textContent = 'occluded'; stat.style.color = '#fbbf24'; }
    else if (kp.visible === VIS_OUTSIDE) { stat.textContent = 'outside'; stat.style.color = '#888'; }
  });
  document.getElementById(`kp-item-${SCHEMA[selectedKP]?.id}`)
    ?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  updateCurrentCard();
}

function updateProgress() {
  const addressed = keypoints.filter(isAddressed).length;
  const visible = keypoints.filter(k => k.visible === VIS_VISIBLE).length;
  const bar = document.getElementById('kp-progress-bar');
  const label = document.getElementById('kp-progress-label');
  if (bar) bar.style.width = Math.round(addressed / 9 * 100) + '%';
  if (label) label.textContent = `${addressed}/9 · visible ${visible}`;
  const bboxEl = document.getElementById('bbox-status');
  if (bboxEl) {
    bboxEl.textContent = bbox?.w > 0 ? 'bbox ok' : 'bbox needed';
    bboxEl.style.color = bbox?.w > 0 ? 'var(--green)' : 'var(--warn)';
  }
}

function setVisState(vis) {
  const kp = keypoints[selectedKP];
  if (vis === VIS_VISIBLE && !hasCoords(kp)) {
    setStatus('warn', 'Click image to place a visible point');
    return;
  }
  kp.visible = vis;
  if (vis === VIS_OUTSIDE) { kp.x = null; kp.y = null; }
  if (vis === VIS_OCCLUDED && !hasCoords(kp)) { kp.x = null; kp.y = null; }
  markDirty();
  advanceToNext();
  redraw(); updatePanel(); updateProgress();
}

function advanceToNext() {
  const indices = activeIndices();
  const pos = indices.indexOf(selectedKP);
  const next = indices.slice(pos + 1).find(i => !isAddressed(keypoints[i]));
  if (next !== undefined) selectedKP = next;
}

function startPan(cx, cy) {
  isPanning = true;
  panStart = {cx, cy, ox: offsetX, oy: offsetY};
  canvas.style.cursor = 'grabbing';
}

function canvasCoords(e) {
  const rect = canvas.getBoundingClientRect();
  return [e.clientX - rect.left, e.clientY - rect.top];
}

canvas.addEventListener('mousemove', e => {
  const [cx, cy] = canvasCoords(e);
  const [ix, iy] = canvasToImg(cx, cy);
  mouseImgPos = {x: ix, y: iy};

  if (isPanning && panStart) {
    offsetX = panStart.ox + (cx - panStart.cx);
    offsetY = panStart.oy + (cy - panStart.cy);
    redraw(); return;
  }
  if (isDragging && dragTarget?.type === 'kp') {
    const [nx, ny] = clampImg(ix, iy);
    const kp = keypoints[dragTarget.idx];
    kp.x = nx; kp.y = ny;
    kp.visible = VIS_VISIBLE;
    markDirty();
    redraw(); updatePanel(); updateProgress(); return;
  }
  if (isDragging && dragTarget?.type === 'bbox' && bboxStart) {
    const [nx, ny] = clampImg(ix, iy);
    bbox = bboxFromAnchor({x: bboxStart[0], y: bboxStart[1]}, nx, ny);
    markDirty();
    redraw(); updateProgress(); return;
  }
  if (isDragging && dragTarget?.type === 'bbox-handle') {
    const [nx, ny] = clampImg(ix, iy);
    bbox = bboxFromAnchor(dragTarget.anchor, nx, ny);
    markDirty();
    redraw(); updateProgress(); return;
  }
  if (isDragging && dragTarget?.type === 'bbox-move') {
    const dx = ix - dragTarget.startX;
    const dy = iy - dragTarget.startY;
    let nx = dragTarget.orig.x + dx;
    let ny = dragTarget.orig.y + dy;
    nx = Math.max(0, Math.min(IMG_W - dragTarget.orig.w, nx));
    ny = Math.max(0, Math.min(IMG_H - dragTarget.orig.h, ny));
    bbox = {x: nx, y: ny, w: dragTarget.orig.w, h: dragTarget.orig.h};
    markDirty();
    redraw(); updateProgress(); return;
  }

  if (spaceHeld) canvas.style.cursor = 'grab';
  else if (mode === 'bbox') {
    const handle = getHitBboxHandle(cx, cy);
    if (handle) canvas.style.cursor = BBOX_CURSORS[handle];
    else if (pointInBbox(ix, iy)) canvas.style.cursor = 'move';
    else canvas.style.cursor = 'crosshair';
    redraw();
  }
  else canvas.style.cursor = getHitKP(cx, cy) >= 0 ? 'move' : 'crosshair';

  if (mode === 'keypoint') redraw();
});

canvas.addEventListener('mousedown', e => {
  const [cx, cy] = canvasCoords(e);
  const [ix, iy] = canvasToImg(cx, cy);

  if (e.button === 1 || (e.button === 0 && (spaceHeld || e.altKey))) {
    startPan(cx, cy); e.preventDefault(); return;
  }

  if (mode === 'bbox' && e.button === 0) {
    const handle = getHitBboxHandle(cx, cy);
    if (handle && bbox) {
      const corners = bboxCorners(bbox);
      isDragging = true;
      dragTarget = {type: 'bbox-handle', corner: handle, anchor: corners[BBOX_OPPOSITE[handle]]};
      return;
    }
    if (pointInBbox(ix, iy) && bbox) {
      isDragging = true;
      dragTarget = {type: 'bbox-move', startX: ix, startY: iy, orig: {...bbox}};
      return;
    }
    const [nx, ny] = clampImg(ix, iy);
    bboxStart = [nx, ny];
    bbox = {x: nx, y: ny, w: 0, h: 0};
    isDragging = true; dragTarget = {type: 'bbox'};
    markDirty();
    return;
  }

  if (e.button !== 0) return;

  const hit = getHitKP(cx, cy);
  if (hit >= 0) {
    isDragging = true;
    dragTarget = {type: 'kp', idx: hit};
    selectedKP = hit;
    updatePanel();
    return;
  }

  const kp = keypoints[selectedKP];
  if (kp.visible === VIS_OUTSIDE) return;

  const [nx, ny] = clampImg(ix, iy);
  kp.x = nx; kp.y = ny;
  kp.visible = VIS_VISIBLE;
  markDirty();
  redraw(); updatePanel(); updateProgress();
  advanceToNext();
});

canvas.addEventListener('mouseup', () => {
  if (dragTarget?.type === 'bbox' && bbox && (bbox.w < 8 || bbox.h < 8)) {
    bbox = null;
    setStatus('warn', 'bbox too small — drag again');
  }
  bboxStart = null;
  isDragging = false; dragTarget = null;
  if (isPanning) { isPanning = false; panStart = null; }
  redraw(); updateProgress();
});

canvas.addEventListener('contextmenu', e => {
  e.preventDefault();
  const [cx, cy] = canvasCoords(e);
  const hit = getHitKP(cx, cy);
  if (hit >= 0) {
    const kp = keypoints[hit];
    if (hasCoords(kp)) {
      kp.visible = kp.visible === VIS_OCCLUDED ? VIS_VISIBLE : VIS_OCCLUDED;
    } else {
      kp.visible = VIS_OCCLUDED;
      kp.x = null; kp.y = null;
    }
    selectedKP = hit;
  } else {
    setVisState(VIS_OCCLUDED);
    return;
  }
  markDirty();
  redraw(); updatePanel(); updateProgress();
});

canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const [cx, cy] = canvasCoords(e);
  if (e.shiftKey) {
    offsetX -= e.deltaY * 0.4;
    redraw();
    return;
  }
  const delta = -e.deltaY;
  const factor = Math.exp(delta * WHEEL_ZOOM_SENS);
  const clamped = Math.max(ZOOM_OUT, Math.min(ZOOM_IN, factor));
  zoomAt(cx, cy, clamped);
}, {passive: false});

window.addEventListener('resize', () => { fitToCanvas(); redraw(); });

document.addEventListener('keydown', e => {
  if (e.code === 'Space' && e.target.tagName !== 'TEXTAREA' && e.target.tagName !== 'INPUT') {
    spaceHeld = true; e.preventDefault();
  }
});
document.addEventListener('keyup', e => {
  if (e.code === 'Space') { spaceHeld = false; redraw(); }
});

function getHitKP(cx, cy) {
  for (let i = keypoints.length - 1; i >= 0; i--) {
    const kp = keypoints[i];
    if (!isDrawable(kp)) continue;
    const [kx, ky] = imgToCanvas(kp.x, kp.y);
    if (Math.hypot(cx - kx, cy - ky) < HIT_RADIUS) return i;
  }
  return -1;
}

function selectKP(idx) {
  if (idx >= 0 && idx < keypoints.length) { selectedKP = idx; redraw(); updatePanel(); }
}

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
    m === 'bbox'
      ? 'Drag bbox · drag corners to resize · drag inside to move · Space/Alt+drag pan'
      : 'Click=visible · Right-click=occluded · Space/Alt=pan';
}

function resetSelectedKP() {
  const kp = keypoints[selectedKP];
  kp.visible = VIS_UNSET; kp.x = null; kp.y = null;
  markDirty();
  redraw(); updatePanel(); updateProgress();
}

function resetAll() {
  if (!confirm('Reset all keypoints and bbox on this frame?')) return;
  keypoints.forEach(k => { k.x = null; k.y = null; k.visible = VIS_UNSET; });
  bbox = null; selectedKP = 0;
  markDirty();
  redraw(); updatePanel(); updateProgress();
}

async function copyFromPrev() {
  setStatus('', 'Loading previous…');
  try {
    const d = await (await fetch(`/api/annotations/prev/${FRAME_ID}`)).json();
    if (!d?.keypoints) { setStatus('warn', 'No previous annotation'); return; }
    d.keypoints.forEach(e => {
      const kp = keypoints.find(k => k.kp_id === e.kp_id);
      if (kp && e.visible !== VIS_UNSET) normalizeLoadedKp(kp, e);
    });
    if (d.bbox?.w > 0) bbox = {...d.bbox};
    selectedKP = keypoints.findIndex(k => !isAddressed(k));
    if (selectedKP < 0) selectedKP = 0;
    markDirty();
    redraw(); updatePanel(); updateProgress();
    setStatus('ok', 'Copied from previous frame');
  } catch (e) { setStatus('error', e.message); }
}

function buildPayload() {
  return {
    frame_id: FRAME_ID,
    keypoints: keypoints.map(k => ({
      kp_id: k.kp_id,
      x: hasCoords(k) ? k.x : null,
      y: hasCoords(k) ? k.y : null,
      visible: k.visible,
    })),
    bbox: bbox?.w > 0 ? bbox : null,
    notes: document.getElementById('notes').value,
    quality: document.getElementById('quality')?.value || null,
  };
}

async function saveToServer() {
  if (saveInFlight) return saveInFlight;
  saveInFlight = (async () => {
    const r = await fetch('/api/annotations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(buildPayload()),
    });
    let d = {};
    try { d = await r.json(); } catch (_) {}
    if (!r.ok) throw new Error(d.error || `Save failed (${r.status})`);
    if (d.error) throw new Error(d.error);
    dirty = false;
    updateSaveHint();
    return d;
  })();
  try {
    return await saveInFlight;
  } finally {
    saveInFlight = null;
  }
}

async function saveAnnotation(autoAdvance = false) {
  try {
    const d = await saveToServer();
    if (d.complete) {
      setStatus('ok', 'Saved (complete)');
      if (autoAdvance && NEXT_ID) setTimeout(() => { location.href = `/annotate/${NEXT_ID}`; }, 300);
    } else {
      setStatus('warn', 'Saved draft — missing: ' + (d.missing || []).join(', '));
      if (autoAdvance && NEXT_ID) {
        const go = confirm('Incomplete:\n' + (d.missing || []).join('\n') + '\n\nGo to next frame anyway?');
        if (go) location.href = `/annotate/${NEXT_ID}`;
      }
    }
  } catch (e) { setStatus('error', e.message); }
}

async function goNext() {
  const missing = getMissingList();
  if (missing.length) {
    const go = confirm('Incomplete:\n• ' + missing.join('\n• ') + '\n\nSave and go to next?');
    if (!go) return;
  }
  try {
    await saveToServer();
    if (NEXT_ID) location.href = `/annotate/${NEXT_ID}`;
  } catch (e) { setStatus('error', e.message); }
}

function zoomButton(dir) {
  zoomAt(canvas.width / 2, canvas.height / 2, dir > 0 ? ZOOM_IN : ZOOM_OUT);
}

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  switch (e.key) {
    case 'Enter': e.preventDefault(); saveAnnotation(true); break;
    case 'ArrowRight': e.preventDefault(); goNext(); break;
    case 'ArrowLeft':
      if (PREV_ID) location.href = `/annotate/${PREV_ID}`;
      else history.back();
      break;
    case 'c': case 'C': copyFromPrev(); break;
    case 'b': case 'B': setMode(mode === 'bbox' ? 'keypoint' : 'bbox'); break;
    case 'v': case 'V':
      if (hasCoords(keypoints[selectedKP])) {
        keypoints[selectedKP].visible = VIS_VISIBLE;
        markDirty(); updatePanel(); redraw();
      } else setStatus('warn', 'Click image to place visible point');
      break;
    case 'o': case 'O': setVisState(VIS_OCCLUDED); break;
    case 'u': case 'U': setVisState(VIS_OUTSIDE); break;
    case 'r': case 'R': resetSelectedKP(); break;
    case 'f': case 'F': fitToCanvas(); redraw(); break;
    case '+': case '=': zoomButton(1); break;
    case '-': case '_': zoomButton(-1); break;
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
