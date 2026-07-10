/**
 * annotate.js v5 — BowLabel labeling canvas
 *
 *  Tools:  Instrument/optional points · Bow-axis polyline
 *  Visibility:  0 unset · 1 occluded · 2 visible · 3 outside
 *  Bow:  trace the visible stick centerline (frog→tip); auto-resampled to 5 pts.
 *  Admin opens frames in read-only REVIEW mode (see other labelers' overlays).
 */

const VIS_UNSET = 0, VIS_OCCLUDED = 1, VIS_VISIBLE = 2, VIS_OUTSIDE = 3;
const VIS_NAME = {0: 'unset', 1: 'occluded', 2: 'visible', 3: 'outside'};

const R_POINT = 7, R_SEL = 11, HIT = 15;
const MIN_SCALE = 0.1, MAX_SCALE = 24;
const WHEEL_SENS = 0.0022, ZOOM_STEP = 1.18;
const BBOX_MARGIN_RATIO = 0.08, BBOX_MIN_SIZE = 96;

// ── schema-derived ───────────────────────────────────────────────────────────
const DEF = {};                       // id -> def
SCHEMA.forEach(d => DEF[d.id] = d);
const POINT_DEFS = SCHEMA.filter(d => d.kind === 'point');       // instrument + optional
const BOW_DEFS   = SCHEMA.filter(d => d.kind === 'bow');         // 5 stick points
const BOW_IDS    = BOW_DEFS.map(d => d.id);
const CORE_IDS   = SCHEMA.filter(d => !d.optional).map(d => d.id);
const CORE_N     = CORE_IDS.length;

// ── state ────────────────────────────────────────────────────────────────────
const pts = {};                       // id -> {x, y, visible}
SCHEMA.forEach(d => pts[d.id] = {x: null, y: null, visible: VIS_UNSET});

let tool = 'point';                   // 'point' | 'bow'
let selectedId = POINT_DEFS[0]?.id ?? 0;
let bbox = null;
let bowDraft = [];                    // [[x,y],...] image coords being traced
let bowFinished = false;

let scale = 1, offX = 0, offY = 0;
let mouseImg = {x: -1, y: -1};
let dragging = null;                  // {type,...}
let panning = null;
let spaceHeld = false;
let dirty = false;

const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const img = new Image();

// ── init ─────────────────────────────────────────────────────────────────────
if (!REVIEW) fetch(`/api/frames/${FRAME_ID}/start`, {method: 'POST'}).catch(() => {});

img.onload = () => {
  fitToCanvas();
  loadExisting();
  buildList();
  selectFirstUnaddressed();
  setTool('point', true);
  redraw(); refreshPanel(); updateMetrics();
};
img.onerror = () => setStatus('error', 'image load failed');
img.src = IMG_URL;

function hasXY(p) { return p && p.x != null && p.y != null && isFinite(p.x) && isFinite(p.y); }
function drawable(id) {
  const p = pts[id];
  return (p.visible === VIS_VISIBLE || p.visible === VIS_OCCLUDED) && hasXY(p);
}
function addressed(id) { return pts[id].visible !== VIS_UNSET; }

function loadExisting() {
  if (!EXISTING) return;
  (EXISTING.keypoints || []).forEach(e => {
    const p = pts[e.kp_id];
    if (!p) return;
    p.visible = e.visible ?? VIS_UNSET;
    if (p.visible === VIS_OUTSIDE) { p.x = null; p.y = null; }
    else if (e.x != null && e.y != null) { p.x = e.x; p.y = e.y; }
  });
  const meta = EXISTING.meta || {};
  if (meta.bow_polyline && meta.bow_polyline.length >= 2) {
    bowDraft = meta.bow_polyline.map(p => [p[0], p[1]]);
  } else if (BOW_IDS.every(id => hasXY(pts[id]))) {
    bowDraft = BOW_IDS.map(id => [pts[id].x, pts[id].y]);
  }
  bowFinished = BOW_IDS.some(id => hasXY(pts[id]));
  bbox = calculateAutoBBox();
  const q = document.getElementById('quality'); if (q && EXISTING.quality) q.value = EXISTING.quality;
  const nt = document.getElementById('notes'); if (nt && EXISTING.notes) nt.value = EXISTING.notes;
}

// ── coordinate transforms ─────────────────────────────────────────────────────
function i2c(x, y) { return [x * scale + offX, y * scale + offY]; }
function c2i(cx, cy) { return [(cx - offX) / scale, (cy - offY) / scale]; }
function clampImg(x, y) { return [Math.max(0, Math.min(IMG_W, x)), Math.max(0, Math.min(IMG_H, y))]; }
function cpos(e) { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }

function fitToCanvas() {
  const wrap = document.getElementById('canvas-wrap');
  canvas.width = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
  const s = Math.min(canvas.width / IMG_W, canvas.height / IMG_H) * 0.95;
  scale = s;
  offX = (canvas.width - IMG_W * s) / 2;
  offY = (canvas.height - IMG_H * s) / 2;
}
function zoomAt(cx, cy, f) {
  const ns = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * f));
  offX = cx - (cx - offX) * (ns / scale);
  offY = cy - (cy - offY) * (ns / scale);
  scale = ns; redraw();
}
function zoomBtn(dir) { zoomAt(canvas.width / 2, canvas.height / 2, dir > 0 ? ZOOM_STEP : 1 / ZOOM_STEP); }

// ── deterministic interaction-region bbox ────────────────────────────────────
function calculateAutoBBox() {
  const usable = CORE_IDS.map(id => pts[id]).filter(p =>
    (p.visible === VIS_VISIBLE || p.visible === VIS_OCCLUDED) && hasXY(p));
  if (!usable.length) return null;

  let x1 = Math.min(...usable.map(p => p.x));
  let x2 = Math.max(...usable.map(p => p.x));
  let y1 = Math.min(...usable.map(p => p.y));
  let y2 = Math.max(...usable.map(p => p.y));
  const margin = Math.max(20, BBOX_MARGIN_RATIO * Math.max(x2 - x1, y2 - y1));
  x1 -= margin; x2 += margin; y1 -= margin; y2 += margin;

  function fitAxis(lo, hi, limit) {
    const target = Math.min(BBOX_MIN_SIZE, limit);
    if (hi - lo < target) {
      const center = (lo + hi) / 2;
      lo = center - target / 2; hi = center + target / 2;
    }
    lo = Math.max(0, lo); hi = Math.min(limit, hi);
    if (hi - lo < target) {
      if (lo <= 0) hi = Math.min(limit, target);
      else lo = Math.max(0, limit - target);
    }
    return [lo, hi];
  }
  [x1, x2] = fitAxis(x1, x2, IMG_W);
  [y1, y2] = fitAxis(y1, y2, IMG_H);
  return {x: x1, y: y1, w: x2 - x1, h: y2 - y1};
}

function refreshAutoBBox() {
  bbox = calculateAutoBBox();
}

// ── bow resample ──────────────────────────────────────────────────────────────
function resample(poly, n) {
  if (poly.length === 1) return Array.from({length: n}, () => poly[0].slice());
  const cum = [0];
  for (let i = 1; i < poly.length; i++)
    cum.push(cum[i - 1] + Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]));
  const total = cum[cum.length - 1];
  if (total === 0) return Array.from({length: n}, () => poly[0].slice());
  const out = [];
  for (let k = 0; k < n; k++) {
    const target = total * k / (n - 1);
    let i = 1; while (i < cum.length - 1 && cum[i] < target) i++;
    const seg = (cum[i] - cum[i - 1]) || 1;
    const t = (target - cum[i - 1]) / seg;
    out.push([poly[i - 1][0] + (poly[i][0] - poly[i - 1][0]) * t,
              poly[i - 1][1] + (poly[i][1] - poly[i - 1][1]) * t]);
  }
  return out;
}
function applyBowResample() {
  if (bowDraft.length < 2) return false;
  const s = resample(bowDraft, BOW_SAMPLES);
  BOW_IDS.forEach((id, i) => { pts[id] = {x: s[i][0], y: s[i][1], visible: VIS_VISIBLE}; });
  bowFinished = true;
  refreshAutoBBox();
  markDirty();
  return true;
}

// ── drawing ───────────────────────────────────────────────────────────────────
function redraw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#05050c'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (img.complete) ctx.drawImage(img, offX, offY, IMG_W * scale, IMG_H * scale);

  if (bbox && bbox.w > 0) {
    const [bx, by] = i2c(bbox.x, bbox.y);
    ctx.save();
    ctx.strokeStyle = '#00d4ff'; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
    ctx.strokeRect(bx, by, bbox.w * scale, bbox.h * scale);
    ctx.fillStyle = 'rgba(0,212,255,0.05)';
    ctx.fillRect(bx, by, bbox.w * scale, bbox.h * scale);
    ctx.restore();
  }

  // skeleton
  CONNECTIONS.forEach(([a, b]) => {
    if (!drawable(a) || !drawable(b)) return;
    const [ax, ay] = i2c(pts[a].x, pts[a].y), [bx, by] = i2c(pts[b].x, pts[b].y);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
    ctx.strokeStyle = DEF[a].group === 'bow' ? 'rgba(245,158,11,0.7)' : 'rgba(120,200,170,0.5)';
    ctx.lineWidth = 2; ctx.stroke();
  });

  // bow draft (being traced)
  if (tool === 'bow' && !bowFinished && bowDraft.length) {
    ctx.save();
    ctx.strokeStyle = 'rgba(245,158,11,0.9)'; ctx.lineWidth = 2; ctx.setLineDash([5, 4]);
    ctx.beginPath();
    bowDraft.forEach((p, i) => { const [cx, cy] = i2c(p[0], p[1]); i ? ctx.lineTo(cx, cy) : ctx.moveTo(cx, cy); });
    if (mouseImg.x >= 0) { const [cx, cy] = i2c(mouseImg.x, mouseImg.y); ctx.lineTo(cx, cy); }
    ctx.stroke(); ctx.setLineDash([]);
    bowDraft.forEach(p => { const [cx, cy] = i2c(p[0], p[1]);
      ctx.beginPath(); ctx.arc(cx, cy, 4, 0, 7); ctx.fillStyle = '#f59e0b'; ctx.fill(); });
    ctx.restore();
  }

  // points
  SCHEMA.forEach(d => {
    if (!drawable(d.id)) return;
    const p = pts[d.id];
    const [cx, cy] = i2c(p.x, p.y);
    const sel = (tool === 'point' && d.id === selectedId);
    const occ = p.visible === VIS_OCCLUDED;
    const r = sel ? R_SEL : R_POINT;
    if (sel) { ctx.beginPath(); ctx.arc(cx, cy, r + 5, 0, 7); ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke(); }
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7);
    ctx.fillStyle = occ ? 'rgba(20,20,30,0.9)' : d.color; ctx.fill();
    if (occ) {
      ctx.strokeStyle = d.color; ctx.lineWidth = 2; ctx.stroke();
      ctx.beginPath(); ctx.moveTo(cx - 4, cy - 4); ctx.lineTo(cx + 4, cy + 4);
      ctx.moveTo(cx + 4, cy - 4); ctx.lineTo(cx - 4, cy + 4); ctx.stroke();
    }
  });

  // ghost cursor for point placement
  if (!REVIEW && tool === 'point' && mouseImg.x >= 0) {
    const p = pts[selectedId];
    if (p.visible !== VIS_OUTSIDE && !hasXY(p)) {
      const [gx, gy] = i2c(mouseImg.x, mouseImg.y);
      ctx.beginPath(); ctx.arc(gx, gy, R_POINT, 0, 7);
      ctx.strokeStyle = DEF[selectedId].color; ctx.lineWidth = 2; ctx.setLineDash([4, 4]); ctx.stroke(); ctx.setLineDash([]);
    }
  }
}

// ── panel / list ──────────────────────────────────────────────────────────────
function buildList() {
  const wrap = document.getElementById('kp-list');
  if (!wrap) return;
  let html = '', lastGroup = null;
  POINT_DEFS.forEach((d, i) => {
    if (d.group !== lastGroup) {
      lastGroup = d.group;
      html += `<div class="kp-group-label">${GROUP_LABELS[d.group] || d.group}</div>`;
    }
    html += `<div class="kp-item" id="it-${d.id}" onclick="selectPoint(${d.id})">
      <span class="kp-dot" style="background:${d.color}"></span>
      <span class="kp-name">${i + 1 <= 9 ? (i + 1) + '. ' : ''}${d.label}</span>
      <span class="kp-chip" id="chip-${d.id}">unset</span></div>`;
  });
  wrap.innerHTML = html;
}
function selectPoint(id) { selectedId = id; if (tool !== 'point') setTool('point'); redraw(); refreshPanel(); }

function refreshPanel() {
  POINT_DEFS.forEach(d => {
    const it = document.getElementById(`it-${d.id}`);
    const chip = document.getElementById(`chip-${d.id}`);
    if (!it || !chip) return;
    it.classList.toggle('sel', d.id === selectedId && tool === 'point');
    const v = pts[d.id].visible;
    chip.textContent = VIS_NAME[v];
    chip.style.color = v === VIS_VISIBLE ? d.color : v === VIS_OCCLUDED ? '#fcd34d'
                     : v === VIS_OUTSIDE ? '#f472b6' : '#666';
  });
  const d = DEF[selectedId];
  if (d) {
    const set = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
    set('sc-name', d.label); set('sc-sub', d.name); set('sc-desc', d.desc);
    const dot = document.getElementById('sc-dot'); if (dot) dot.style.background = d.color;
    const vb = document.getElementById('sc-vis');
    if (vb) { vb.textContent = VIS_NAME[pts[d.id].visible]; vb.className = 'vis-badge vis-' + pts[d.id].visible; }
  }
  const bc = document.getElementById('bow-count'); if (bc) bc.textContent = bowFinished ? BOW_SAMPLES : bowDraft.length;
  updateHint();
}

function updateMetrics() {
  const core = CORE_IDS.filter(addressed).length;
  const mc = document.getElementById('m-core'); if (mc) mc.textContent = core;
  const bar = document.getElementById('m-bar'); if (bar) bar.style.width = Math.round(core / CORE_N * 100) + '%';
  const mb = document.getElementById('m-bbox');
  if (mb) { const ok = bbox && bbox.w > 0; mb.textContent = ok ? 'auto bbox ready' : 'auto bbox pending';
    mb.style.color = ok ? 'var(--green)' : 'var(--warn)'; }
}

function updateHint() {
  const el = document.getElementById('canvas-hint'); if (!el) return;
  if (REVIEW) { el.innerHTML = 'Read-only <b>review</b> · pan Space/Alt+drag · zoom wheel'; return; }
  if (tool === 'point') {
    const d = DEF[selectedId];
    el.innerHTML = `Place <b>${d ? d.label : ''}</b> — click the image. Right-click = occluded.`;
  } else if (tool === 'bow') {
    el.innerHTML = bowFinished
      ? 'Bow set. Drag a point to nudge, or <b>Redraw</b> to trace again.'
      : 'Trace the <b>visible stick</b> (frog→tip): click points, then <b>Enter</b>.';
  }
}

// ── tools ─────────────────────────────────────────────────────────────────────
function setTool(t, silent) {
  tool = t;
  document.querySelectorAll('.an-tab').forEach(b => b.classList.toggle('active', b.dataset.tool === t));
  const pp = document.getElementById('pane-point'), pb = document.getElementById('pane-bow');
  if (pp) pp.style.display = (t === 'bow') ? 'none' : '';
  if (pb) pb.style.display = (t === 'bow') ? '' : 'none';
  if (!silent) { redraw(); refreshPanel(); }
  updateHint();
}

function setVis(v) {
  if (REVIEW) return;
  const p = pts[selectedId];
  if (v === VIS_VISIBLE && !hasXY(p)) { setStatus('warn', 'click image to place point'); return; }
  if (v === VIS_OCCLUDED && !hasXY(p)) {
    setStatus('warn', 'right-click the estimated image position for occluded');
    return;
  }
  p.visible = v;
  if (v === VIS_OUTSIDE) { p.x = null; p.y = null; }
  refreshAutoBBox();
  markDirty(); advancePoint(); redraw(); refreshPanel(); updateMetrics();
}
function selectFirstUnaddressed() {
  const first = POINT_DEFS.find(d => !addressed(d.id) && !d.optional);
  selectedId = first ? first.id : POINT_DEFS[0].id;
}
function advancePoint() {
  const order = POINT_DEFS.filter(d => !d.optional);
  const idx = order.findIndex(d => d.id === selectedId);
  const next = order.slice(idx + 1).find(d => !addressed(d.id));
  if (next) selectedId = next.id;
}

// bow actions
function finishBow() { if (applyBowResample()) { redraw(); refreshPanel(); updateMetrics(); setStatus('ok', 'bow set'); }
  else setStatus('warn', 'place at least 2 points'); }
function undoBowVertex() { if (!bowFinished && bowDraft.length) { bowDraft.pop(); redraw(); refreshPanel(); } }
function redrawBow() { bowFinished = false; bowDraft = []; BOW_IDS.forEach(id => pts[id] = {x: null, y: null, visible: VIS_UNSET});
  refreshAutoBBox(); markDirty(); redraw(); refreshPanel(); updateMetrics(); }
function clearBow() {
  bowFinished = false; bowDraft = [];
  BOW_IDS.forEach(id => pts[id] = {x: null, y: null, visible: VIS_OUTSIDE});
  refreshAutoBBox(); markDirty(); redraw(); refreshPanel(); updateMetrics();
  setStatus('warn', 'bow marked outside / not localizable');
}

function getHitPoint(cx, cy) {
  const ids = SCHEMA.map(d => d.id).filter(drawable);
  for (let i = ids.length - 1; i >= 0; i--) {
    const [x, y] = i2c(pts[ids[i]].x, pts[ids[i]].y);
    if (Math.hypot(cx - x, cy - y) < HIT) return ids[i];
  }
  return -1;
}

// ── mouse ─────────────────────────────────────────────────────────────────────
canvas.addEventListener('mousemove', e => {
  const [cx, cy] = cpos(e);
  [mouseImg.x, mouseImg.y] = c2i(cx, cy);

  if (panning) { offX = panning.ox + (cx - panning.cx); offY = panning.oy + (cy - panning.cy); redraw(); return; }
  if (dragging) {
    const [ix, iy] = c2i(cx, cy);
    if (dragging.type === 'pt') { const [nx, ny] = clampImg(ix, iy); const p = pts[dragging.id];
      p.x = nx; p.y = ny; if (p.visible === VIS_UNSET || p.visible === VIS_OUTSIDE) p.visible = VIS_VISIBLE;
      refreshAutoBBox(); markDirty(); redraw(); refreshPanel(); updateMetrics(); return; }
  }

  if (spaceHeld) canvas.style.cursor = 'grab';
  else canvas.style.cursor = getHitPoint(cx, cy) >= 0 ? 'move' : 'crosshair';

  if (tool === 'point' || tool === 'bow') redraw();
});

canvas.addEventListener('mousedown', e => {
  const [cx, cy] = cpos(e);
  const [ix, iy] = c2i(cx, cy);

  if (e.button === 1 || (e.button === 0 && (spaceHeld || e.altKey))) {
    panning = {cx, cy, ox: offX, oy: offY}; canvas.style.cursor = 'grabbing'; e.preventDefault(); return;
  }
  if (REVIEW || e.button !== 0) return;

  // point/bow: dragging an existing point
  const hit = getHitPoint(cx, cy);
  if (hit >= 0) {
    if (POINT_DEFS.some(d => d.id === hit)) { selectedId = hit; refreshPanel(); }
    dragging = {type: 'pt', id: hit}; return;
  }

  if (tool === 'bow') {
    if (bowFinished) return;                 // redraw to edit
    const [nx, ny] = clampImg(ix, iy);
    bowDraft.push([nx, ny]); markDirty(); redraw(); refreshPanel(); return;
  }

  // point tool: place selected
  const p = pts[selectedId];
  if (p.visible === VIS_OUTSIDE) return;
  const [nx, ny] = clampImg(ix, iy);
  p.x = nx; p.y = ny; p.visible = VIS_VISIBLE;
  refreshAutoBBox(); markDirty(); redraw(); refreshPanel(); updateMetrics(); advancePoint();
});

canvas.addEventListener('mouseup', () => {
  dragging = null;
  if (panning) { panning = null; }
  redraw(); updateMetrics();
});

canvas.addEventListener('contextmenu', e => {
  e.preventDefault();
  if (REVIEW) return;
  const [cx, cy] = cpos(e);
  const hit = getHitPoint(cx, cy);
  if (hit >= 0) {
    const p = pts[hit];
    p.visible = p.visible === VIS_OCCLUDED ? VIS_VISIBLE : VIS_OCCLUDED;
    if (POINT_DEFS.some(d => d.id === hit)) { selectedId = hit; }
    refreshAutoBBox();
    markDirty(); redraw(); refreshPanel(); updateMetrics();
  } else if (tool === 'point') {
    const [ix, iy] = c2i(cx, cy);
    const [nx, ny] = clampImg(ix, iy);
    pts[selectedId] = {x: nx, y: ny, visible: VIS_OCCLUDED};
    refreshAutoBBox();
    markDirty(); advancePoint(); redraw(); refreshPanel(); updateMetrics();
  }
});

canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const [cx, cy] = cpos(e);
  if (e.shiftKey) { offX -= e.deltaY * 0.5; redraw(); return; }
  zoomAt(cx, cy, Math.exp(-e.deltaY * WHEEL_SENS));
}, {passive: false});

window.addEventListener('resize', () => { fitToCanvas(); redraw(); });

// ── keyboard ──────────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.code === 'Space' && !/^(TEXTAREA|INPUT|SELECT)$/.test(e.target.tagName)) { spaceHeld = true; e.preventDefault(); }
});
document.addEventListener('keyup', e => { if (e.code === 'Space') { spaceHeld = false; redraw(); } });

document.addEventListener('keydown', e => {
  if (/^(TEXTAREA|INPUT|SELECT)$/.test(e.target.tagName)) return;
  if (REVIEW) {
    if (e.key === 'ArrowLeft' && PREV_ID) location.href = `/annotate/${PREV_ID}?as_user=${new URLSearchParams(location.search).get('as_user')||''}`;
    if (e.key === 'ArrowRight' && NEXT_ID) location.href = `/annotate/${NEXT_ID}?as_user=${new URLSearchParams(location.search).get('as_user')||''}`;
    return;
  }
  switch (e.key) {
    case 'Enter': e.preventDefault(); tool === 'bow' && !bowFinished ? finishBow() : goNext(); return;
    case 'Backspace': if (tool === 'bow') { e.preventDefault(); undoBowVertex(); } return;
    case 'ArrowLeft': if (PREV_ID) location.href = `/annotate/${PREV_ID}`; return;
    case 's': case 'S': e.preventDefault(); saveAnnotation(false); return;
    case 'c': case 'C': copyFromPrev(); return;
    case 'b': case 'B': setTool('bow'); return;
    case 'v': case 'V': setVis(VIS_VISIBLE); return;
    case 'o': case 'O': setVis(VIS_OCCLUDED); return;
    case 'x': case 'X': setVis(VIS_OUTSIDE); return;
    case 'f': case 'F': fitToCanvas(); redraw(); return;
    case '+': case '=': zoomBtn(1); return;
    case '-': case '_': zoomBtn(-1); return;
  }
  if (e.key >= '1' && e.key <= '9') {
    const idx = parseInt(e.key) - 1;
    if (idx < POINT_DEFS.length) selectPoint(POINT_DEFS[idx].id);
  }
});

// ── save / status ─────────────────────────────────────────────────────────────
function markDirty() { dirty = true; setStatus('', 'unsaved'); }
function setStatus(kind, msg) { const el = document.getElementById('save-status'); if (el) { el.textContent = msg; el.className = 'save-status ' + kind; } }

function getMissing() {
  const miss = [];
  CORE_IDS.forEach(id => {
    if (!addressed(id)) miss.push(DEF[id].label);
    else if (pts[id].visible === VIS_VISIBLE && !hasXY(pts[id])) miss.push(DEF[id].label + ' (no coords)');
  });
  if (!bbox || bbox.w <= 0) miss.push('automatic bbox (no core coordinates)');
  return miss;
}

function buildPayload() {
  return {
    frame_id: FRAME_ID,
    keypoints: SCHEMA.map(d => ({
      kp_id: d.id,
      x: hasXY(pts[d.id]) ? pts[d.id].x : null,
      y: hasXY(pts[d.id]) ? pts[d.id].y : null,
      visible: pts[d.id].visible,
    })),
    bbox: calculateAutoBBox(),
    meta: {bow_polyline: bowFinished && bowDraft.length >= 2 ? bowDraft : null},
    notes: document.getElementById('notes')?.value || '',
    quality: document.getElementById('quality')?.value || null,
  };
}

let saving = null;
async function saveToServer() {
  if (saving) return saving;
  saving = (async () => {
    const r = await fetch('/api/annotations', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(buildPayload())});
    let d = {}; try { d = await r.json(); } catch (_) {}
    if (!r.ok) throw new Error(d.error || `save failed (${r.status})`);
    bbox = d.bbox || calculateAutoBBox();
    dirty = false;
    return d;
  })();
  try { return await saving; } finally { saving = null; }
}

async function saveAnnotation(advance) {
  try {
    const d = await saveToServer();
    if (d.complete) { setStatus('ok', 'saved'); if (advance && NEXT_ID) setTimeout(() => location.href = `/annotate/${NEXT_ID}`, 250); }
    else { setStatus('warn', 'draft'); if (advance) maybeAdvance(d.missing || []); }
  } catch (e) { setStatus('error', e.message); }
}
function maybeAdvance(missing) {
  if (!NEXT_ID) return;
  if (confirm('Incomplete:\n• ' + missing.join('\n• ') + '\n\nGo to next frame anyway?')) location.href = `/annotate/${NEXT_ID}`;
}
async function goNext() {
  const miss = getMissing();
  if (miss.length && !confirm('Incomplete:\n• ' + miss.join('\n• ') + '\n\nSave draft and go to next?')) return;
  try { await saveToServer(); if (NEXT_ID) location.href = `/annotate/${NEXT_ID}`; else setStatus('ok', 'saved (last frame)'); }
  catch (e) { setStatus('error', e.message); }
}

async function copyFromPrev() {
  try {
    const d = await (await fetch(`/api/annotations/prev/${FRAME_ID}`)).json();
    if (!d || !d.keypoints) { setStatus('warn', 'no previous annotation'); return; }
    d.keypoints.forEach(e => { const p = pts[e.kp_id]; if (!p || e.visible === VIS_UNSET) return;
      p.visible = e.visible; if (e.visible === VIS_OUTSIDE) { p.x = null; p.y = null; } else { p.x = e.x; p.y = e.y; } });
    if (d.meta && d.meta.bow_polyline) { bowDraft = d.meta.bow_polyline.map(p => [p[0], p[1]]); bowFinished = true; }
    else bowFinished = BOW_IDS.some(id => hasXY(pts[id]));
    refreshAutoBBox();
    markDirty(); selectFirstUnaddressed(); redraw(); refreshPanel(); updateMetrics();
    setStatus('ok', 'copied previous');
  } catch (e) { setStatus('error', e.message); }
}
