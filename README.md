# 🎻 BowLabel

바이올린 **비침습 활쓰기 피드백**을 위한 키포인트 라벨링 웹 서비스

Roboflow 대신 자체 호스팅 — 라벨러 5명+ 동시 작업, 데이터 로컬 보관, YOLO Pose / COCO Export

---

## 설계 철학: 무엇을 라벨링할까?

활쓰기 피드백에 필요한 정보를 기준으로 **8개 키포인트**만 수동 라벨링합니다.
관절·손가락은 **MediaPipe Pose + Hands**로 별도 추론합니다 (라벨 부담 19개→8개).

| 그룹 | 키포인트 | 용도 |
|------|---------|------|
| **활 (bow)** | tip, frog, mid, contact | 활 각도·방향(업/다운보우)·속도·sounding point |
| **바이올린** | bridge, nut | 현 평면, 활 위치(브릿지~지판 비율) |
| **현** | string_g, string_e | 4현 위치 (G~E 보간으로 A/D현 추정) |

**필수 4개** (저장 시 검증): `bow_tip`, `bow_frog`, `bow_contact`, `violin_bridge`

### MediaPipe가 담당하는 것 (라벨링 불필요)
- 어깨·팔꿈치·손목 (자세, 활 높이)
- 손가락 21개 (활 잡는 그립, 왼손 운지)
- 머리/턱 위치 (악기 고정)

### 학습 파이프라인 (권장)
```
[카메라 영상]
    ├─ BowLabel YOLO Pose  → 활 + 바이올린 + 현 (8 kp)
    └─ MediaPipe           → 관절 + 손 (33+21 kp)
         └─ 융합 모델       → 활 각도, sounding point, 그립 오차 보정
```

---

## 빠른 시작

```bash
cd bowlabel
pip install -r requirements.txt
python3 app.py
```

브라우저 → http://localhost:5050  
기본 계정: **admin / admin1234**

---

## 팀 협업 (ngrok)

```bash
ngrok http 5050
# 출력 URL을 라벨러 5명에게 공유
```

1. Admin: 라벨러 계정 생성 (대시보드)
2. 영상 업로드 → 프레임 추출 (2fps 권장)
3. **작업 배분** 탭에서 라벨러 선택 → 자동 균등 배분
4. 라벨러: 로그인 → **다음 작업 시작** → 라벨링

---

## 라벨링 워크플로 (효율 팁)

```
1. C키 — 이전 프레임 키포인트 복사 (연속 프레임에서 가장 빠름)
2. 그룹 필터 — "활"만 먼저 라벨링 후 "바이올린" → "현"
3. Enter — 저장 + 자동 다음 프레임
4. 필수 4개 완료 시에만 'labeled' 상태로 전환
```

### 단축키

| 키 | 동작 |
|----|------|
| **C** | 이전 프레임 키포인트 복사 |
| Enter | 저장 + 다음 프레임 |
| Tab | 다음 키포인트 |
| X | 건너뛰기 (occluded) |
| ← → | 이전/다음 프레임 |
| 1~8 | 키포인트 직접 선택 |
| R | 현재 키포인트 삭제 |
| F | 화면 맞춤 |
| 우클릭 | 가림(occluded) 토글 |
| 휠 | 줌 / 중간버튼 = 패닝 |

---

## Export

| 포맷 | 용도 |
|------|------|
| COCO JSON | MMPose, ViTPose |
| YOLO Pose ZIP | YOLOv8-pose, RTX A6000 로컬 학습 |
| CSV | Python 분석 |

---

## 기술 스택

- Backend: Python / Flask / Flask-SocketIO / SQLite (WAL)
- Frontend: Vanilla JS / HTML5 Canvas
- 영상: OpenCV
- Export: COCO / YOLO Pose / CSV

## GitHub

https://github.com/yk7244/bowlabel
