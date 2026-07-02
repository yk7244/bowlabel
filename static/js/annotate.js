/**
 * annotate.js - BowLabel canvas annotation
 *
 * 핵심 UX:
 * - C: 이전 프레임 키포인트 복사 (연속 프레임 라벨링 가속)
 * - G: 그룹 모드 (bow / violin / strings 만 라벨링)
 * - X: 현재 키포인트 건너뛰기 (occluded)
 * - 필수 4개 검증 후 저장
 */

const POINT_RADIUS   = 8;
const POINT_SELECTED = 12;
const HIT_RADIUS     = 16;
const MIN_SCALE      = 0.1;
const MAX_SCALE      = 12;

const GROUP_LABELS_KO = {
  bow: '활', violin: '바이올린', strings: '현',
  right_hand: '오른손', right_arm: '오른팔', left_arm: '왼팔',
};

const canvas = document.getElementById('canvas');
const ctx    = canvas.getContext('2d');
const img    = new Image();

let keypoints = SCHEMA.map(kp => ({
  kp_id: kp.id, name: kp.name, color: kp.color, group: kp.group,
  desc: kp.desc || '', x: null, y: null, visible: 0
}));

let bbox         = null;
let selectedKP   = 0;
let mode         = 'keypoint';
let groupFilter  = null;   // null = 전체, 'bow' = 활만 등
let isDragging   = false;
let dragTarget   = null;
let bboxStart    = null;
let isPanning    = false;
let panStart     = null;
let scale        = 1;
let offsetX      = 0;
let offsetY      = 0;
let mouseImgPos  = {x: 0, y: 0};

// ── 초기화 ────────────────────────────────────────────────
fetch(`/api/frames/${FRAME_ID}/start`, {method: 'POST'}).catch(() => {});

img.onload = () => {
  fitToCanvas();
  loadExisting();
  const first = activeIndices().find(i => keypoints[i].visible === 0);
  selectedKP = first !== undefined ? first : 0;
  redraw();
  updatePanel();
  updateProgress();
};
img.onerror = () => {
  document.getElementById('canvas-hint').textContent = '이미지 로드 실패';
};
img.src = IMG_URL;

function loadExisting() {
  if (EXISTING && Array.isArray(EXISTING)) {
    EXISTING.forEach(e => {
      const kp = keypoints.find(k => k.kp_id === e.kp_id);
      if (kp && e.x != null) { kp.x = e.x; kp.y = e.y; kp.visible = e.visible ?? 2; }
    });
  }
  if (EXISTING_BBOX)  bbox = {...EXISTING_BBOX};
  if (EXISTING_NOTES) document.getElementById('notes').value = EXISTING_NOTES;
}

function activeIndices() {
  if (!groupFilter) return keypoints.map((_, i) => i);
  return keypoints.map((k, i) => i).filter(i => keypoints[i].group === groupFilter);
}

function isRequired(kp) {
  return (REQUIRED_KPS || []).includes(kp.name);
}

// ── 좌표 변환 ──────────────────────────────────────────────
function imgToCanvas(x, y) {
  return [x * scale + offsetX, y * scale + offsetY];
}
function canvasToImg(cx, cy) {
  return [(cx - offsetX) / scale, (cy - offsetY) / scale];
}
function clampImg(x, y) {
  return [Math.max(0, Math.min(IMG_W, x)), Math.max(0, Math.min(IMG_H, y))];
}
function fitToCanvas() {
  const wrap = document.getElementById('canvas-wrap');
  canvas.width  = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
  const s = Math.min(canvas.width / IMG_W, canvas.height / IMG_H) * 0.92;
  scale   = s;
  offsetX = (canvas.width  - IMG_W * s) / 2;
  offsetY = (canvas.height - IMG_H * s) / 2;
}

// ── 드로우 ────────────────────────────────────────────────
function redraw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#111827';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (img.complete && img.naturalWidth) {
    ctx.drawImage(img, offsetX, offsetY, IMG_W * scale, IMG_H * scale);
  }

  if (bbox && bbox.w > 0 && bbox.h > 0) {
    const [bx, by] = imgToCanvas(bbox.x, bbox.y);
    ctx.save();
    ctx.strokeStyle = '#00e5ff';
    ctx.lineWidth   = 1.5;
    ctx.setLineDash([5, 3]);
    ctx.strokeRect(bx, by, bbox.w * scale, bbox.h * scale);
    ctx.restore();
  }

  // 스켈레톤
  ctx.save();
  ctx.lineWidth = 2;
  CONNECTIONS.forEach(([a, b]) => {
    const A = keypoints[a], B = keypoints[b];
    if (!A || !B || A.visible < 1 || B.visible < 1 || A.x == null || B.x == null) return;
    const [ax, ay] = imgToCanvas(A.x, A.y);
    const [bx, by2] = imgToCanvas(B.x, B.y);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by2);
    const isBow = A.group === 'bow' || B.group === 'bow';
    ctx.strokeStyle = A.visible === 1 || B.visible === 1
      ? 'rgba(255,255,255,0.15)'
      : isBow ? 'rgba(255,100,50,0.7)' : 'rgba(255,255,255,0.4)';
    ctx.stroke();
  });
  ctx.restore();

  keypoints.forEach((kp, idx) => {
    if (groupFilter && kp.group !== groupFilter) return;
    if (kp.visible === 0 || kp.x == null) return;
    const [cx, cy] = imgToCanvas(kp.x, kp.y);
    const isSel   = idx === selectedKP;
    const isOcc   = kp.visible === 1;
    const r       = isSel ? POINT_SELECTED : POINT_RADIUS;

    if (isSel) {
      ctx.beginPath();
      ctx.arc(cx, cy, r + 6, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(255,255,255,0.9)';
      ctx.lineWidth   = 2;
      ctx.stroke();
    }

    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle   = isOcc ? 'rgba(30,30,30,0.85)' : kp.color;
    ctx.fill();
    ctx.strokeStyle = kp.color;
    ctx.lineWidth   = isOcc ? 2 : 0;
    ctx.stroke();

    if (isOcc) {
      ctx.save();
      ctx.strokeStyle = kp.color;
      ctx.lineWidth   = 2;
      ctx.beginPath();
      ctx.moveTo(cx - 5, cy - 5); ctx.lineTo(cx + 5, cy + 5);
      ctx.moveTo(cx + 5, cy - 5); ctx.lineTo(cx - 5, cy + 5);
      ctx.stroke();
      ctx.restore();
    }

    if (scale > 0.35) {
      const label = kp.name + (isRequired(kp) ? '*' : '');
      ctx.save();
      ctx.font = `bold ${Math.min(13, Math.max(9, 10 * scale))}px monospace`;
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = 'rgba(0,0,0,0.7)';
      ctx.fillRect(cx + r + 2, cy - 11, tw + 6, 15);
      ctx.fillStyle = isRequired(kp) ? '#ffcc00' : 'white';
      ctx.fillText(label, cx + r + 5, cy + 2);
      ctx.restore();
    }
  });

  // 미배치 가이드
  const cur = keypoints[selectedKP];
  if (mode === 'keypoint' && cur?.visible === 0 && mouseImgPos.x > 0) {
    const [gx, gy] = imgToCanvas(mouseImgPos.x, mouseImgPos.y);
    if (gx > 0 && gx < canvas.width && gy > 0 && gy < canvas.height) {
      ctx.beginPath();
      ctx.arc(gx, gy, POINT_RADIUS, 0, Math.PI * 2);
      ctx.strokeStyle = cur.color || 'white';
      ctx.lineWidth   = 2;
      ctx.setLineDash([4, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // 현재 kp 안내
  if (mode === 'keypoint' && cur) {
    const grp = GROUP_LABELS_KO[cur.group] || cur.group;
    const desc = cur.desc ? ` — ${cur.desc}` : '';
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,0.75)';
    ctx.fillRect(10, 10, 320, 36);
    ctx.fillStyle = cur.color;
    ctx.font = 'bold 13px sans-serif';
    ctx.fillText(`▶ [${grp}] ${cur.name}${desc}`, 18, 33);
    ctx.restore();
  }
}

// ── 패널 ──────────────────────────────────────────────────
function updatePanel() {
  keypoints.forEach((kp, idx) => {
    const item = document.getElementById(`kp-item-${kp.kp_id}`);
    const stat = document.getElementById(`kp-status-${kp.kp_id}`);
    if (!item || !stat) return;

    const dimmed = groupFilter && kp.group !== groupFilter;
    item.classList.toggle('kp-selected', idx === selectedKP);
    item.classList.toggle('kp-dimmed', dimmed);

    if (kp.visible === 0 || kp.x == null) {
      stat.textContent = '—';
      stat.style.color = '#555';
    } else if (kp.visible === 1) {
      stat.textContent = '가림';
      stat.style.color = '#aaa';
    } else {
      stat.textContent = `${Math.round(kp.x)},${Math.round(kp.y)}`;
      stat.style.color = kp.color;
    }
  });

  const el = document.getElementById(`kp-item-${SCHEMA[selectedKP]?.id}`);
  if (el) el.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

function updateProgress() {
  const active = groupFilter
    ? keypoints.filter(k => k.group === groupFilter)
    : keypoints;
  const total  = active.length;
  const done   = active.filter(k => k.visible > 0 && k.x != null).length;
  const pct    = total ? Math.round(done / total * 100) : 0;
  const bar    = document.getElementById('kp-progress-bar');
  const label  = document.getElementById('kp-progress-label');
  if (bar)   bar.style.width = pct + '%';
  if (label) label.textContent = groupFilter
    ? `${done}/${total} (${GROUP_LABELS_KO[groupFilter] || groupFilter})`
    : `${done} / ${total}`;
}

// ── 이벤트 ────────────────────────────────────────────────
canvas.addEventListener('mousemove', e => {
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  const [ix, iy] = canvasToImg(cx, cy);
  mouseImgPos = {x: ix, y: iy};

  if (isPanning && panStart) {
    offsetX = panStart.ox + (cx - panStart.cx);
    offsetY = panStart.oy + (cy - panStart.cy);
    redraw(); return;
  }
  if (isDragging && dragTarget) {
    if (dragTarget.type === 'kp') {
      let [nx, ny] = clampImg(ix, iy);
      keypoints[dragTarget.idx].x = nx;
      keypoints[dragTarget.idx].y = ny;
      redraw(); updatePanel(); updateProgress();
    } else if (dragTarget.type === 'bbox' && bboxStart) {
      let [nx, ny] = clampImg(ix, iy);
      bbox = {
        x: Math.min(bboxStart[0], nx), y: Math.min(bboxStart[1], ny),
        w: Math.abs(nx - bboxStart[0]),  h: Math.abs(ny - bboxStart[1])
      };
      redraw();
    }
    return;
  }

  if (mode === 'keypoint') {
    const hit = getHitKP(cx, cy);
    canvas.style.cursor = hit >= 0 ? 'move' : 'crosshair';
  }
  if (mode === 'keypoint' && keypoints[selectedKP]?.visible === 0) redraw();
});

canvas.addEventListener('mousedown', e => {
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  const [ix, iy] = canvasToImg(cx, cy);

  if (e.button === 1) {
    isPanning = true;
    panStart  = {cx, cy, ox: offsetX, oy: offsetY};
    e.preventDefault(); return;
  }

  if (e.button === 2) {
    const hit = getHitKP(cx, cy);
    const idx = hit >= 0 ? hit : selectedKP;
    if (keypoints[idx].x != null) {
      keypoints[idx].visible = keypoints[idx].visible === 2 ? 1 : 2;
      redraw(); updatePanel();
    }
    return;
  }

  if (mode === 'keypoint') {
    const hit = getHitKP(cx, cy);
    if (hit >= 0) {
      isDragging = true;
      dragTarget = {type: 'kp', idx: hit};
      selectedKP = hit;
      updatePanel();
    } else {
      let [nx, ny] = clampImg(ix, iy);
      keypoints[selectedKP].x = nx;
      keypoints[selectedKP].y = ny;
      keypoints[selectedKP].visible = 2;
      redraw(); updatePanel(); updateProgress();
      advanceToNext();
    }
  } else if (mode === 'bbox') {
    let [nx, ny] = clampImg(ix, iy);
    bboxStart  = [nx, ny];
    bbox       = {x: nx, y: ny, w: 0, h: 0};
    isDragging = true;
    dragTarget = {type: 'bbox'};
  }
});

canvas.addEventListener('mouseup', () => {
  isDragging = false; dragTarget = null;
  isPanning  = false; panStart   = null;
  redraw();
});
canvas.addEventListener('contextmenu', e => e.preventDefault());

canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const rect   = canvas.getBoundingClientRect();
  const cx     = e.clientX - rect.left;
  const cy     = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.12 : 0.89;
  const ns     = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * factor));
  offsetX = cx - (cx - offsetX) * (ns / scale);
  offsetY = cy - (cy - offsetY) * (ns / scale);
  scale   = ns;
  redraw();
}, {passive: false});

window.addEventListener('resize', () => { fitToCanvas(); redraw(); });

function getHitKP(cx, cy) {
  for (let i = keypoints.length - 1; i >= 0; i--) {
    const kp = keypoints[i];
    if (kp.x == null) continue;
    const [kx, ky] = imgToCanvas(kp.x, kp.y);
    if (Math.hypot(cx - kx, cy - ky) < HIT_RADIUS) return i;
  }
  return -1;
}

function advanceToNext() {
  const indices = activeIndices();
  const pos = indices.indexOf(selectedKP);
  const next = indices.slice(pos + 1).find(i => keypoints[i].visible === 0);
  if (next !== undefined) { selectedKP = next; updatePanel(); }
}

// ── 컨트롤 ────────────────────────────────────────────────
function selectKP(idx) {
  if (idx < 0 || idx >= keypoints.length) return;
  selectedKP = idx;
  redraw(); updatePanel();
}

function setMode(m) {
  mode = m;
  document.getElementById('mode-kp').classList.toggle('active', m === 'keypoint');
  document.getElementById('mode-bbox').classList.toggle('active', m === 'bbox');
  const hints = {
    keypoint: '클릭=배치  드래그=이동  우클릭=가림  C=이전복사',
    bbox:     '드래그로 바운딩박스 그리기'
  };
  document.getElementById('mode-hint').textContent = hints[m] || '';
}

function setGroupFilter(grp) {
  groupFilter = grp === 'all' ? null : grp;
  document.querySelectorAll('.group-filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.group === (grp || 'all'));
  });
  const first = activeIndices().find(i => keypoints[i].visible === 0);
  if (first !== undefined) selectedKP = first;
  redraw(); updatePanel(); updateProgress();
}

function resetAll() {
  if (!confirm('이 프레임의 모든 키포인트를 초기화할까요?')) return;
  keypoints.forEach(k => { k.x = null; k.y = null; k.visible = 0; });
  bbox = null;
  selectedKP = activeIndices()[0] || 0;
  redraw(); updatePanel(); updateProgress();
}

function resetSelectedKP() {
  keypoints[selectedKP].x = null;
  keypoints[selectedKP].y = null;
  keypoints[selectedKP].visible = 0;
  redraw(); updatePanel(); updateProgress();
}

function skipKP() {
  keypoints[selectedKP].visible = 1;
  keypoints[selectedKP].x = keypoints[selectedKP].x ?? 0;
  keypoints[selectedKP].y = keypoints[selectedKP].y ?? 0;
  advanceToNext();
  redraw(); updatePanel(); updateProgress();
}

async function copyFromPrev() {
  const status = document.getElementById('save-status');
  status.textContent = '이전 프레임 불러오는 중...';
  status.style.color = '#aaa';
  try {
    const r = await fetch(`/api/annotations/prev/${FRAME_ID}`);
    const d = await r.json();
    if (!d || !d.keypoints) {
      status.textContent = '이전 프레임 어노테이션이 없습니다';
      status.style.color = '#ffaa00';
      return;
    }
    d.keypoints.forEach(e => {
      const kp = keypoints.find(k => k.kp_id === e.kp_id);
      if (kp && e.visible > 0) {
        kp.x = e.x; kp.y = e.y; kp.visible = e.visible;
      }
    });
    if (d.bbox) bbox = {...d.bbox};
    const first = activeIndices().find(i => keypoints[i].visible === 0);
    selectedKP = first !== undefined ? first : 0;
    redraw(); updatePanel(); updateProgress();
    status.textContent = '✅ 이전 프레임에서 복사됨 — 위치만 미세 조정하세요';
    status.style.color = '#44ff88';
  } catch (err) {
    status.textContent = '❌ ' + err.message;
    status.style.color = '#ff4444';
  }
}

// ── 저장 ──────────────────────────────────────────────────
async function saveAnnotation(autoAdvance = true) {
  const btn    = document.getElementById('btn-save');
  const status = document.getElementById('save-status');
  btn.disabled = true;
  btn.textContent = '저장중...';

  const payload = {
    frame_id:  FRAME_ID,
    keypoints: keypoints.map(k => ({
      kp_id: k.kp_id, x: k.x ?? 0, y: k.y ?? 0, visible: k.visible
    })),
    bbox:  bbox,
    notes: document.getElementById('notes').value
  };

  try {
    const r = await fetch('/api/annotations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);

    if (d.status === 'labeled') {
      status.textContent = '✅ 저장 완료!';
      status.style.color = '#44ff88';
      btn.textContent = '💾 저장 (Enter)';
      btn.disabled = false;
      if (autoAdvance && NEXT_ID) {
        setTimeout(() => { window.location.href = `/annotate/${NEXT_ID}`; }, 400);
      }
    } else if (d.missing && d.missing.length) {
      status.textContent = `⚠️ 필수 미완료: ${d.missing.join(', ')}`;
      status.style.color = '#ffaa00';
      btn.textContent = '💾 저장 (Enter)';
      btn.disabled = false;
      const missId = keypoints.findIndex(k => d.missing.includes(k.name));
      if (missId >= 0) selectKP(missId);
    }
  } catch (err) {
    status.textContent = '❌ 오류: ' + err.message;
    btn.textContent = '💾 저장 (Enter)';
    btn.disabled = false;
  }
}

// ── 키보드 ────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;

  switch(e.key) {
    case 'Enter':     e.preventDefault(); saveAnnotation(); break;
    case 'ArrowRight':
      const btnNext = document.getElementById('btn-next');
      if (btnNext?.href) window.location.href = btnNext.href;
      break;
    case 'ArrowLeft':
      if (PREV_ID) window.location.href = `/annotate/${PREV_ID}`;
      else history.back();
      break;
    case 'Escape':    selectKP(-1); break;
    case 'r': case 'R': resetSelectedKP(); break;
    case 'f': case 'F': fitToCanvas(); redraw(); break;
    case 'b': case 'B': setMode(mode === 'bbox' ? 'keypoint' : 'bbox'); break;
    case 'c': case 'C': copyFromPrev(); break;
    case 'x': case 'X': skipKP(); break;
    case 'Tab':
      e.preventDefault();
      const indices = activeIndices();
      const pos = indices.indexOf(selectedKP);
      selectKP(indices[(pos + 1) % indices.length]);
      break;
  }

  if (e.key >= '1' && e.key <= '9') {
    const i = parseInt(e.key) - 1;
    if (i < keypoints.length) selectKP(i);
  }
});
