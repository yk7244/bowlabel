# BowLabel 변경 이력

프로젝트의 주요 변경 사항을 **언제 · 무엇을 · 왜 · 어떻게** 수정했는지 기록합니다.

---

## 2026-07-02 — v3.2 저장 버그·bbox 핸들·줌 조정

**커밋:** `저장 race condition 수정, bbox 꼭지점 조정, 줌 속도 상향`

### 배경 (문제)

- 다른 PC에서 라벨러 저장 시 `UNIQUE constraint failed: annotations.frame_id, labeled_by` → 연쇄 `database is locked`
- bbox 그린 뒤 꼭지점으로 미세 조정 불가
- 줌이 v3.1 기준으로 다소 느림

### 원인

1. **저장 race condition** — 동시에 두 번 저장 요청이 오면 둘 다 `SELECT`에서 row 없음 → 둘 다 `INSERT` 시도 → UNIQUE 실패
2. 실패 후 프론트가 연속 재시도 → SQLite lock 폭주
3. bbox는 드래그로만 생성, 이후 resize 없음

### 수정

#### `app.py`

- `INSERT ... ON CONFLICT(frame_id, labeled_by) DO UPDATE` — atomic upsert
- `database is locked` 시 최대 5회 exponential backoff 재시도

#### `database.py`

- `timeout=30`, `PRAGMA busy_timeout=30000` — 다중 라벨러 동시 접속 완화

#### `static/js/annotate.js` (v3.2)

- `saveInFlight` mutex — 동시 저장 요청 1개로 합침
- HTTP非200 응답 시 명확한 에러 메시지
- bbox **4꼭지점 핸들** 드래그로 resize
- bbox **내부 드래그**로 이동
- 줌: `WHEEL_ZOOM_SENS` 0.0018 → 0.0026, 버튼 줌 1.06x / 0.94x

### bbox 사용법

```
B → bbox 모드
드래그        → 새 bbox
꼭지점 드래그  → 크기 조정
내부 드래그    → 위치 이동
```

---

## 2026-07-02 — v3.1 라벨링 UX 버그 수정

**커밋:** `라벨링 UX 버그 수정 — 저장/줌/팬/좌표 클러스터`

### 배경 (문제)

라벨러 피드백:
- 확대(줌)가 너무 빠름
- 프레임 위치 이동(팬)이 어려움
- 점·bbox가 제대로 저장·복원되지 않음
- `occluded` / `outside` 지정 시 점이 **왼쪽 상단 (0,0)** 에 몰림
- 다음 프레임으로 넘어갈 때 미완성 항목 안내 없음
- visibility 한글 표기(`가림`, `프레임밖`)가 오히려 읽기 어려움

### 수정 내용

#### `static/js/annotate.js` (v3.1)

| 항목 | 이전 | 이후 |
|------|------|------|
| 줌 속도 | 고정 1.12x / 0.89x per wheel tick | delta 기반 지수 줌 (`WHEEL_ZOOM_SENS=0.0018`), 버튼 줌 1.04x / 0.96x |
| 팬 | 없음 / 제한적 | **Space+드래그**, **Alt+드래그**, 휠클릭 드래그 |
| 가로 스크롤 | 없음 | **Shift+휠** |
| 좌표 저장 | `x??0` → occluded 시 (0,0) 저장 | `hasCoords()` — 좌표 없으면 `null` 전송 |
| 화면 렌더 | visible/occluded 모두 (0,0)에 그림 | `isDrawable()` — 좌표 있는 점만 그림 |
| 기존 데이터 로드 | JSON 문자열 미파싱, (0,0) 그대로 사용 | `normalizeLoadedKp()` — legacy (0,0) 정리 |
| UI 용어 | 한글 (가시/가림/프레임밖) | **visible / occluded / outside / unset** |
| 다음 프레임 | 무조건 이동 | `goNext()` / Enter 시 `confirm()`으로 미완성 목록 표시 후 이동 허용 |
| 저장 | 완료만 의미 있음 | 항상 draft 저장 (`in_progress`), 미완성 시 `missing` 목록 반환 |
| bbox | 클릭만으로 0×0 저장 가능 | 최소 8×8px 미만이면 저장·표시 안 함 |
| 캔버스 툴바 | 없음 | `−` `+` `Fit` 버튼 추가 |

#### `app.py`

- `annotate()` — DB에서 읽은 `keypoints` / `bbox` JSON 문자열을 **템플릿 전에 파싱** (`existing_parsed`)
- `save_annotation()` API 응답에 `saved: true` 추가, `__keypoints__` missing 처리

#### `database.py`

- `annotation_complete()` — `visible`은 좌표 필수, `occluded`/`outside`는 좌표 없이 완료 인정
- missing 목록을 kp **이름 대신 kp_id**로 반환 → 프론트에서 schema 이름으로 매핑

#### `templates/annotate.html`

- 영문 UI, 캔버스 툴바, `goNext()` 연결, 캐시 버스트 `?v=32`

### 사용법 (v3.1)

```
클릭          → visible 점 배치
우클릭 / O    → occluded
U             → outside
R             → 선택 keypoint 리셋
B             → bbox 모드
Space/Alt+드래그 → 팬
Shift+휠      → 가로 이동
+/− / F       → 줌 / 화면 맞춤
Enter / ▶     → 저장 (+ 미완성 시 확인 후 다음)
```

---

## 2026-07-02 — v3 스키마·워크플로 전면 개편

**커밋:** `DB 구조 변경 UI 개선` (`a16968b`)

### 배경

- 8kp 분할 라벨링(bow-only / violin-only) 폐기
- **Master annotation**: 프레임당 9 keypoints + 1 bbox, 클래스 `violin_bowing_scene`
- Pilot(전원 동일 20장) → Agreement → Main(균등 배분) 워크플로

### 주요 변경

| 파일 | 내용 |
|------|------|
| `database.py` | SCHEMA_VERSION=3, 9kp 스키마, `frame_assignments` 테이블, pilot/main 배치 |
| `workflows.py` | **신규** — pilot 전원 배분 + main round-robin |
| `app.py` | assignment 기반 접근, 워크플로 API, visibility 완료 검증 |
| `exporter.py` | YOLO `violin_bowing_scene`, COCO visibility 매핑, pilot agreement CSV |
| `static/js/annotate.js` | v3 캔버스 — bbox, 3종 visibility, 그룹 필터 |
| `templates/*` | admin/labeler 대시보드, agreement 페이지, annotate UI 개편 |
| `README.md` | v3 스키마·워크플로 문서화 |

### 9 keypoints

```
bridge:       bridge_g_foot, bridge_e_foot
fingerboard:  nut_g/e, body_g/e corners
bow:          frog_endpoint, tip_endpoint, midpoint_visible
```

---

## 2026-07-02 — v2 8kp 협업 UX

**커밋:** `활쓰기 라벨링 시스템 개편 — 8kp 스키마, 협업 UX, UI 리뉴얼` (`5138374`)

- 8 keypoints 스키마 (bow / violin / strings 그룹)
- 다중 라벨러 협업 UX, UI 리뉴얼
- *(이후 v3에서 9kp master annotation으로 대체됨)*

---

## 2026-07-01 — 데이터 관리·플랫폼 완성

**커밋:** `feat: complete data management + annotation platform` (`d5d4688`)

- 프로젝트/영상/프레임 CRUD
- COCO/YOLO/CSV export 기반

---

## 2026-07-01 — 초기 플랫폼 + 호환성

**커밋:** `fix: resolve conda Python compatibility issues + improve annotation UX` (`17528b1`)

- conda Python 호환성 수정
- 초기 annotation UX 개선

---

## 2026-07-01 — 프로젝트 시작

**커밋:** `feat: initial BowLabel annotation platform` (`a83f91e`)

- Flask + SQLite + Canvas 기반 바이올린 활쓰기 라벨링 도구 최초 구현

---

## DB 초기화가 필요한 경우

스키마 v3 이전 데이터 또는 (0,0) 좌표가 섞인 annotation이 있으면:

```bash
python3 app.py --reset
python3 app.py
```

기존 DB를 유지하면서 특정 프레임만 고치려면 라벨링 화면에서 `R`(keypoint 리셋) 후 다시 찍으면 됩니다.
