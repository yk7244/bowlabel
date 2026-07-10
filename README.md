# BowLabel v5

바이올린 활쓰기(bowing) **master annotation** 도구 — 웹 기반 협업 라벨링.
Flask + SQLite + HTML Canvas. 여러 라벨러가 ngrok으로 접속해 같은 서버에서 작업.

> 자세한 변경 이력은 [CHANGELOG.md](CHANGELOG.md) 참고.

---

## 1. 무엇을 라벨링하나 — 스키마 (v5)

프레임 1장 = `violin_bowing_scene` 객체 1개 = **bbox 1개 + core keypoint 9개** (+ optional 3개).

키포인트는 "예쁜 부품"이 아니라 **실제 촬영 영상에서 반복적으로 보이는 지점**으로 골랐다.
(nut·scroll·bridge는 왼손/화각에 자주 가려져서 필수에서 제외.)

### Core 9점 (필수, YOLO `kpt_shape: [9, 3]`)

| id | name | group | 설명 |
|----|------|-------|------|
| 0 | `fingerboard_body_g_corner` | instrument | body 쪽 지판 끝, G현 측 모서리 |
| 1 | `fingerboard_body_e_corner` | instrument | body 쪽 지판 끝, E현 측 모서리 |
| 2 | `tailpiece_upper_center` | instrument | 테일피스 위쪽(브릿지 방향) 중심 |
| 3 | `tailpiece_lower_center` | instrument | 테일피스 아래쪽(엔드핀 방향) 중심 |
| 4 | `bow_axis_visible_start` | bow | **보이는** 활축의 frog 쪽 끝 |
| 5 | `bow_axis_visible_25` | bow | 자동: 보이는 활축 구간 25% 지점 |
| 6 | `bow_axis_visible_50` | bow | 자동: 50% 지점 |
| 7 | `bow_axis_visible_75` | bow | 자동: 75% 지점 |
| 8 | `bow_axis_visible_end` | bow | **보이는** 활축의 tip 쪽 끝 |

**활은 곡선(camber)이라 tip–frog 직선을 쓰지 않는다.** 라벨러가 보이는 활대 중심선을
따라 여러 점을 클릭하면, 도구가 **호(arc-length) 기준 5점으로 자동 리샘플**한다.
25/50/75는 물리적 활 전체가 아니라 **현재 프레임에서 보이는 구간**의 비율이다.
tip/frog가 가려지거나 프레임 밖이면 추측하지 말고 보이는 끝까지만 그린다.

### Optional 3점 (보일 때만, 검증용 — 학습 kpt_shape에서 제외)

| id | name | 용도 |
|----|------|------|
| 9  | `bridge_g_foot_center` | 브릿지가 보이는 subset에서 기준선 검증 |
| 10 | `bridge_e_foot_center` | 〃 |
| 11 | `bow_string_contact_center` | sounding point 분석 |

### visibility (BowLabel 내부 코드 → COCO/YOLO)

| 값 | 의미 | COCO v |
|----|------|--------|
| 2 | **visible** — 명확히 보임, 클릭함 | 2 |
| 1 | **occluded** — 화면 안이지만 손/악기에 가림 (위치 추정 가능) | 1 |
| 3 | **outside** — 프레임 밖 / 위치 특정 불가 | 0 |
| 0 | **unset** — 아직 처리 안 함 (저장은 draft) | 0 |

한 프레임이 `labeled`(완료)로 바뀌려면 **core 9점 모두 처리 + 자동 bbox 생성 가능**해야 한다.
덜 된 상태로 저장하면 `in_progress` draft로 남고, 다음 프레임으로 넘어갈 때 경고한다.

### Bounding box — 수동 라벨링하지 않음

`bbox`는 물리적 물체 경계가 아니라 `violin_bowing_scene`의 **visible interaction region**이다.
라벨러는 bbox를 그리지 않고 점/활축만 라벨링하며, 브라우저에서 미리 보여주고 서버가 저장 시
동일 규칙으로 최종 생성한다.

1. optional을 제외한 **core 9점** 중 좌표가 있는 `visible`/`occluded` 점만 수집
2. 점들의 최소 외접 사각형 계산
3. `max(20px, 긴 변의 8%)` margin 추가
4. 최소 `96×96px`이 되도록 중심 기준 확장
5. 이미지 경계로 clamp

optional bridge/contact 점은 프레임마다 존재 여부가 달라 bbox를 흔들 수 있으므로 계산에서 제외한다.
사람 얼굴·상반신·팔 전체도 포함 대상으로 삼지 않는다. 서버가 클라이언트의 수동 bbox 값을 신뢰하지
않고 keypoint에서 다시 계산하므로 라벨러별 object extent 차이가 생기지 않는다.

---

## 2. 서버는 어떻게 동작하고 데이터는 어디에 저장되나

```
┌──────────┐   HTTP/WebSocket   ┌──────────────────┐
│ 브라우저 │ ◀───(ngrok)──────▶ │  Flask (app.py)  │
│ 라벨러들 │                    │  localhost:5050  │
└──────────┘                    └────────┬─────────┘
                                         │
              ┌──────────────────────────┼───────────────────────────┐
              ▼                           ▼                           ▼
      data.db (SQLite)            frames/<video_id>/*.jpg      exports/*, uploads/*
      메타·라벨 전부              추출된 프레임 이미지          내보내기·원본영상
```

- **`app.py`** — Flask 웹서버. 모든 페이지(로그인/대시보드/갤러리/라벨링)와 API를 담당.
  `python3 app.py`로 `0.0.0.0:5050`에 뜬다. 라벨러는 같은 서버에 접속하므로
  **모든 작업 결과가 한 곳(`data.db`)에 모인다.**
- **`data.db`** — SQLite 파일 하나. 사용자·프로젝트·영상·프레임·배정·**어노테이션(JSON)**이
  전부 여기 저장된다. WAL 모드 + busy timeout으로 다중 라벨러 동시 저장을 견딘다.
  라벨 좌표는 `annotations.keypoints`에 JSON 배열로, bbox/메타도 JSON으로 들어간다.
- **`frames/<video_id>/frame_XXXXXXXX.jpg`** — 영상에서 추출한 프레임 이미지(디스크).
  DB에는 파일명만, 실제 이미지는 파일시스템에 있다. `/frames/<vid>/<name>`으로 서빙.
- **`uploads/`** — 업로드된 원본 영상. **`exports/`** — 내보낸 COCO/YOLO/CSV 결과물.

즉 "다른 컴퓨터에서 라벨러가 작업한 결과"는 **네 로컬 서버의 `data.db`에 즉시 저장**된다.
관리자가 admin으로 로그인하면 그 `data.db`를 그대로 읽으므로 모두의 작업을 볼 수 있다.
(관리자 화면은 아래 "리뷰 모드" 참고.)

### 저장 흐름 (한 프레임)
1. 라벨러가 점/활축을 그리면 브라우저가 `POST /api/annotations` 로 JSON 전송
2. 서버가 core keypoint에서 bbox를 자동 생성한 뒤 `annotations`에 **upsert**
3. 완료 판정 후 `frame_assignments.status`를 `labeled`/`in_progress`로 갱신
4. 응답에 완료 여부·미완성 항목 목록을 돌려줌 → 화면 상단에 표시

---

## 3. 실행

```bash
pip install -r requirements.txt

python3 app.py --reset     # DB 초기화 (스키마 바뀌었을 때 / 처음)
python3 app.py             # 이후 일반 실행
# http://localhost:5050   ·   admin / admin1234
```

> **주의:** v3 → v4는 키포인트가 완전히 바뀌어 기존 어노테이션과 호환되지 않는다.
> v4 → v5는 keypoint id를 유지한 이름 변경이라 `python3 app.py` 시작 시 자동 마이그레이션된다.

### 외부 라벨러 접속 (ngrok)
```bash
python3 app.py           # 먼저 로컬 서버 실행
ngrok http 5050          # 다른 터미널
# 출력된 https Forwarding URL 을 라벨러에게 공유 (127.0.0.1:4040 아님)
```

---

## 4. 관리자 워크플로

1. **대시보드** → 라벨러 계정 추가, 프로젝트 생성
2. **프로젝트 → 영상 업로드** → 여러 파일을 한 번에 선택/드롭하면 **순차 업로드**됨 → 프레임 추출(fps 지정)
3. **작업 배분** (아래 두 모드)
4. 라벨러들이 라벨링
5. **갤러리에서 리뷰/관리** → 완료 프레임 클릭(리뷰), 이상한 프레임 삭제, 담당자 재배정
6. **Export** → YOLO / COCO / CSV, pilot은 Agreement CSV

### 배분 모드
- **전체 재배분(reset)**: 전체 프레임을 pilot/main으로 다시 나눔. Pilot은 **모든 영상에서 고르게 샘플**한
  N장을 전원이 라벨링. 재실행해도 기존 **진행상태는 보존**됨.
- **새 프레임만 추가(incremental)**: 나중에 영상을 더 올렸을 때, 기존 배분/진행을 그대로 두고
  **미배정 프레임만** 선택 라벨러에게 균등 추가.

### 배분 현황·수정
- 배분 탭 하단에 **라벨러별 / 영상별 분포**가 표로 보이고, 영상별로 **main 재배정** 가능.
- 갤러리에서 프레임을 **체크박스로 다중 선택 → 선택 삭제 / 선택 재배정**, 카드별 개별 삭제도 가능.
  (프레임 삭제 시 어노테이션·배정·디스크 이미지까지 함께 삭제)

### 리뷰 모드 (관리자가 다른 라벨러 결과 보기)
- admin으로 **갤러리에서 프레임을 클릭**하면 **읽기 전용 리뷰**로 열린다.
- 상단 드롭다운에서 **어떤 라벨러의 어노테이션**을 볼지 선택 (오버레이가 그 사람 것으로 바뀜).
- admin은 편집/저장이 막혀 있다(실수로 라벨러 작업을 덮어쓰지 않도록). 편집하려면 라벨러 계정으로 로그인.

---

## 5. 라벨링 화면 사용법

오른쪽 탭은 **Instrument · Bow** 두 도구다. bbox는 자동 생성된다.

### Instrument / Optional 점
- 리스트에서 점 선택(또는 숫자 `1`~`7`) → **이미지 클릭**으로 배치(visible)
- **우클릭** = occluded · `V`/`O`/`X` = visible/occluded/outside
- 배치된 점 **드래그**로 미세 조정

### Bow (활)
- 보이는 활대 중심선을 **frog → tip** 순서로 여러 번 클릭
- **Enter** 또는 Finish → 5개 점으로 자동 리샘플
- `⌫`(Backspace) 마지막 점 취소 · **Redraw** 다시 그리기 · **Not visible** 활 라벨 생략
- tip/frog가 안 보이면 추측 금지, 보이는 끝까지만

### 자동 bbox
- 별도 조작 없음. core 점/활축을 수정할 때마다 청록색 박스가 자동으로 갱신된다.
- optional 점은 bbox에 영향을 주지 않는다.

### 공통
- 팬: **Space / Alt + 드래그** (또는 휠 클릭) · 가로: **Shift + 휠**
- 줌: 휠 / `+` `−` / `F`(Fit)
- 저장: `S` · 저장+다음: **Enter** (미완성이면 확인 후 이동)

### 단축키 요약

| 키 | 동작 |
|----|------|
| 1–7 | instrument/optional 점 선택 |
| 클릭 / 드래그 | 배치 / 이동 |
| 우클릭 | occluded 토글 |
| V / O / X | visible / occluded / outside |
| B | Bow 도구 |
| Enter | (Bow 그리는 중) Finish · 아니면 저장+다음 |
| ⌫ | (Bow) 마지막 점 취소 |
| C | 이전 프레임 복사 |
| Space·Alt+드래그 | 팬 · Shift+휠 가로 |
| + / − / F | 줌 / Fit |
| ← / → | 이전 / (저장 후) 다음 프레임 |

---

## 6. 파일 구조

| 파일 | 역할 |
|------|------|
| `app.py` | Flask 라우트·API·인증·리뷰 모드 |
| `database.py` | 스키마 정의(키포인트/visibility), DB 초기화, 완료 판정 |
| `workflows.py` | pilot/main 배분 |
| `extractor.py` | 영상 → 프레임 추출 |
| `exporter.py` | COCO / YOLO / CSV / pilot agreement 내보내기 |
| `templates/annotate.html` | 라벨링 화면 |
| `static/js/annotate.js` | 라벨링 캔버스 로직 |
| `data.db` · `frames/` · `uploads/` · `exports/` | 데이터(§2) |
