/**
 * annotate.js - Canvas keypoint annotation (v2 - improved UX)
 *
 * 생각 흐름:
 * - 이미지 로드 후 canvas에 fit
 * - keypoints[] 상태 배열로 관리: {kp_id, x, y, visible: 0=미배치 1=occluded 2=visible}
 * - 클릭 → 현재 선택 kp에 좌표 저장 → 자동 다음 kp 이동
 * - 드래그 → 이미 배치된 kp 위치 이동
 * - 우클릭 → occluded 토글
 * - 휠 줌 / 중간버튼 패닝
 * - Enter = 저장 + 자동 다음 프레임
 * - 오른쪽 패널: kp 리스트 (클릭시 선택), 그룹별 색상, 좌표 표시
 * - 하단 미니맵: 전체 프레임에서 현재 위치 표시
 */

// ── 상수 ──────────────────────────────────────────────────
const POINT_RADIUS   = 7;
const POINT_SELECTED = 11;
const HIT_RADIUS     = 14;   // 클릭 감지 반경 (px)
const MIN_SCALE      = 0.1;
const MAX_SCALE      = 12;

// ── 상태 ──────────────────────────────────────────────────
const canvas = document.getElementById('canvas');
const ctx    = canvas.getContext('2d');
const img    = new Image();

let keypoints = SCHEMA.map(kp => ({
  kp_id: kp.id, name: kp.name, color: kp.color, group: kp.group,
  x: null, y: null, visible: 0
}));

let bbox         = null;
let selectedKP   = 0;
let mode         = 'keypoint';
let isDragging   = false;
let dragTarget   = null;
let bboxStart    = null;
let isPanning    = false;
let panStart     = null;
let scale        = 1;
let offsetX      = 0;
let offsetY      = 0;
let lastSaved    = false;
let mouseImgPos  = {x: 0, y: 0};

// ── 초기화 ────────────────────────────────────────────────
img.onload = () => {
  fitToCanvas();

  if (EXISTING && Array.isArray(EXISTING)) {
    EXISTING.forEach(e => {
      const kp = keypoints.find(k => k.kp_id === e.kp_id);
      if (kp && e.x != null) { kp.x = e.x; kp.y = e.y; kp.visible = e.visible ?? 2; }
    });
    lastSaved = true;
  }
  if (EXISTING_BBOX)  bbox = {...EXISTING_BBOX};
  if (EXISTING_NOTES) document.getElementById('notes').value = EXISTING_NOTES;

  // 첫 번째 미배치 kp 선택
  const first = keypoints.findIndex(k => k.visible === 0);
  selectedKP = first >= 0 ? first : 0;

  redraw();
  updatePanel();
  updateProgress();
};
img.onerror = () => {
  document.getElementById('canvas-hint').textContent = '이미지 로드 실패';
};
img.src = IMG_URL;

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

  // 배경
  ctx.fillStyle = '#111827';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // 이미지
  if (img.complete && img.naturalWidth) {
    ctx.drawImage(img, offsetX, offsetY, IMG_W * scale, IMG_H * scale);
  }

  // BBox
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
  ctx.lineWidth = 1.5;
  CONNECTIONS.forEach(([a, b]) => {
    const A = keypoints[a], B = keypoints[b];
    if (!A || !B || A.visible < 1 || B.visible < 1 || A.x == null || B.x == null) return;
    const [ax, ay] = imgToCanvas(A.x, A.y);
    const [bx, by2] = imgToCanvas(B.x, B.y);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by2);
    ctx.strokeStyle = A.visible === 1 || B.visible === 1
      ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.45)';
    ctx.stroke();
  });
  ctx.restore();

  // 키포인트
  keypoints.forEach((kp, idx) => {
    if (kp.visible === 0 || kp.x == null) return;
    const [cx, cy] = imgToCanvas(kp.x, kp.y);
    const isSel   = idx === selectedKP;
    const isOcc   = kp.visible === 1;
    const r       = isSel ? POINT_SELECTED : POINT_RADIUS;

    // 선택 링
    if (isSel) {
      ctx.beginPath();
      ctx.arc(cx, cy, r + 5, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(255,255,255,0.8)';
      ctx.lineWidth   = 2;
      ctx.stroke();
    }

    // 원
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle   = isOcc ? 'rgba(30,30,30,0.85)' : kp.color;
    ctx.fill();
    ctx.strokeStyle = kp.color;
    ctx.lineWidth   = isOcc ? 2 : 0;
    ctx.stroke();

    // occluded X
    if (isOcc) {
      ctx.save();
      ctx.strokeStyle = kp.color;
      ctx.lineWidth   = 2;
      ctx.beginPath();
      ctx.moveTo(cx - 4, cy - 4); ctx.lineTo(cx + 4, cy + 4);
      ctx.moveTo(cx + 4, cy - 4); ctx.lineTo(cx - 4, cy + 4);
      ctx.stroke();
      ctx.restore();
    }

    // 라벨 (일정 줌 이상일 때만)
    if (scale > 0.4) {
      ctx.save();
      ctx.font        = `bold ${Math.min(13, Math.max(9, 10 * scale))}px monospace`;
      ctx.fillStyle   = 'rgba(0,0,0,0.6)';
      ctx.fillRect(cx + r + 2, cy - 10, ctx.measureText(kp.name).width + 4, 14);
      ctx.fillStyle   = 'white';
      ctx.fillText(kp.name, cx + r + 4, cy + 1);
      ctx.restore();
    }
  });

  // 현재 선택 kp가 미배치면 마우스 커서에 가이드 점 표시
  if (mode === 'keypoint' && keypoints[selectedKP]?.visible === 0 && mouseImgPos.x > 0) {
    const [gx, gy] = imgToCanvas(mouseImgPos.x, mouseImgPos.y);
    if (gx > 0 && gx < canvas.width && gy > 0 && gy < canvas.height) {
      ctx.beginPath();
      ctx.arc(gx, gy, POINT_RADIUS, 0, Math.PI * 2);
      ctx.strokeStyle = SCHEMA[selectedKP]?.color || 'white';
      ctx.lineWidth   = 2;
      ctx.setLineDash([3, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // 좌상단 현재 kp 안내
  if (mode === 'keypoint') {
    const kp = SCHEMA[selectedKP];
    if (kp) {
      ctx.save();
      ctx.fillStyle   = 'rgba(0,0,0,0.65)';
      ctx.fillRect(10, 10, 200, 28);
      ctx.fillStyle   = kp.color;
      ctx.font        = 'bold 13px sans-serif';
      ctx.fillText(`▶  ${kp.name}  (${selectedKP + 1}/${SCHEMA.length})`, 18, 29);
      ctx.restore();
    }
  }
}

// ── 패널 업데이트 ──────────────────────────────────────────
function updatePanel() {
  keypoints.forEach((kp, idx) => {
    const item = document.getElementById(`kp-item-${kp.kp_id}`);
    const stat = document.getElementById(`kp-status-${kp.kp_id}`);
    if (!item || !stat) return;

    item.classList.toggle('kp-selected', idx === selectedKP);

    if (kp.visible === 0 || kp.x == null) {
      stat.textContent = '—';
      stat.style.color = '#555';
    } else if (kp.visible === 1) {
      stat.textContent = 'occ';
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
  const total  = keypoints.length;
  const done   = keypoints.filter(k => k.visible > 0 && k.x != null).length;
  const pct    = Math.round(done / total * 100);
  const bar    = document.getElementById('kp-progress-bar');
  const label  = document.getElementById('kp-progress-label');
  if (bar)   bar.style.width = pct + '%';
  if (label) label.textContent = `${done} / ${total}`;
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

  // 커서 변경: 기존 kp 위에 올라가면 move
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

  // 중간 버튼 패닝
  if (e.button === 1) {
    isPanning = true;
    panStart  = {cx, cy, ox: offsetX, oy: offsetY};
    e.preventDefault(); return;
  }

  // 우클릭: occluded 토글
  if (e.button === 2) {
    const hit = getHitKP(cx, cy);
    const idx = hit >= 0 ? hit : selectedKP;
    if (keypoints[idx].x != null) {
      keypoints[idx].visible = keypoints[idx].visible === 2 ? 1 : 2;
      redraw(); updatePanel();
    }
    return;
  }

  // 좌클릭
  if (mode === 'keypoint') {
    const hit = getHitKP(cx, cy);
    if (hit >= 0) {
      // 기존 kp 드래그 시작
      isDragging = true;
      dragTarget = {type: 'kp', idx: hit};
      selectedKP = hit;
      updatePanel();
    } else {
      // 새 위치 배치
      let [nx, ny] = clampImg(ix, iy);
      keypoints[selectedKP].x = nx;
      keypoints[selectedKP].y = ny;
      keypoints[selectedKP].visible = 2;
      redraw(); updatePanel(); updateProgress();

      // 다음 미배치 kp로 자동 이동
      const next = keypoints.findIndex((k, i) => i > selectedKP && k.visible === 0);
      if (next >= 0) { selectedKP = next; updatePanel(); }
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

// ── 컨트롤 함수 ────────────────────────────────────────────
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
    keypoint: '클릭=배치  드래그=이동  우클릭=occluded  휠=줌',
    bbox:     '드래그로 바운딩박스 그리기'
  };
  document.getElementById('mode-hint').textContent = hints[m] || '';
  canvas.style.cursor = m === 'bbox' ? 'crosshair' : 'crosshair';
}

function resetAll() {
  if (!confirm('이 프레임의 모든 키포인트를 초기화할까요?')) return;
  keypoints.forEach(k => { k.x = null; k.y = null; k.visible = 0; });
  bbox = null;
  selectedKP = 0;
  redraw(); updatePanel(); updateProgress();
}

function resetSelectedKP() {
  keypoints[selectedKP].x = null;
  keypoints[selectedKP].y = null;
  keypoints[selectedKP].visible = 0;
  redraw(); updatePanel(); updateProgress();
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
    if (d.status === 'saved') {
      status.textContent = '✅ 저장됨!';
      status.style.color = '#44ff88';
      lastSaved = true;
      btn.textContent = '💾 저장 (Enter)';
      btn.disabled    = false;
      if (autoAdvance && NEXT_ID) {
        setTimeout(() => { window.location.href = `/annotate/${NEXT_ID}`; }, 500);
      }
    }
  } catch (err) {
    status.textContent = '❌ 오류: ' + err.message;
    btn.textContent    = '💾 저장 (Enter)';
    btn.disabled       = false;
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
    case 'ArrowLeft': history.back(); break;
    case 'Escape':    selectKP(-1); break;
    case 'r': case 'R': resetSelectedKP(); break;
    case 'f': case 'F': fitToCanvas(); redraw(); break;
    case 'b': case 'B': setMode(mode === 'bbox' ? 'keypoint' : 'bbox'); break;
    case 'Tab':
      e.preventDefault();
      selectKP((selectedKP + 1) % keypoints.length);
      break;
  }

  if (e.key >= '1' && e.key <= '9') selectKP(parseInt(e.key) - 1);
  if (e.key === '0') selectKP(9);
});
