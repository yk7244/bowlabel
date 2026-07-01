# 🎻 BowLabel

바이올린 활쓰기(bow technique) 교정을 위한 **키포인트 어노테이션 웹 서비스**

Roboflow 대비 장점:
- 동시 접속 라벨러 **무제한**
- 바이올린 활쓰기 특화 키포인트 스키마 (활 tip→frog, 손가락, 팔, 악기)
- 영상 업로드 → 프레임 자동 추출 → 배분 → 어노테이션 → Export 원스톱
- 데이터 완전 로컬 보관 (외부 SaaS 의존 없음)
- COCO / YOLO Pose / CSV 포맷 Export

---

## 시스템 구조

```
Admin (관리자)
  ├── 프로젝트 생성 + 키포인트 스키마 커스텀
  ├── 영상 업로드 (드래그&드롭, 최대 4GB)
  ├── 프레임 추출 (FPS 커스텀, 구간 지정)
  ├── 라벨러 계정 생성/관리
  ├── 프레임 자동 균등 배분
  ├── 진행률 실시간 모니터링
  └── COCO / YOLO / CSV Export

Labeler (라벨러)
  ├── 브라우저 접속 → 로그인
  ├── 배정된 프레임 목록 확인
  └── Canvas 어노테이션 UI
        ├── 키포인트 클릭 배치
        ├── 드래그로 위치 수정
        ├── 우클릭 = occluded 토글
        ├── 마우스 휠 = 줌
        ├── BBox 드로잉 모드
        └── Enter = 저장 + 자동 다음 프레임
```

## 기본 키포인트 스키마 (19개)

| ID | 이름 | 그룹 |
|----|------|------|
| 0 | bow_tip | bow |
| 1 | bow_mid_upper | bow |
| 2 | bow_mid | bow |
| 3 | bow_mid_lower | bow |
| 4 | bow_frog | bow |
| 5~9 | r_thumb ~ r_pinky | right_hand |
| 10 | r_wrist | right_arm |
| 11 | r_elbow | right_arm |
| 12 | r_shoulder | right_arm |
| 13~15 | l_wrist ~ l_shoulder | left_arm |
| 16 | violin_scroll | violin |
| 17 | violin_bridge | violin |
| 18 | chin_rest | violin |

---

## 설치 및 실행

### 1. 의존성 설치

```bash
pip3 install flask flask-cors flask-login flask-socketio opencv-python werkzeug tqdm
```

### 2. 실행

```bash
cd keypont_organizer
python3 app.py
```

기본 접속: http://localhost:5050
기본 계정: `admin` / `admin1234` (최초 실행 시 자동 생성)

### 3. ngrok으로 외부 공유 (팀원 접속)

```bash
# ngrok 설치 (처음 한 번)
brew install ngrok
ngrok config add-authtoken <YOUR_TOKEN>  # https://dashboard.ngrok.com

# 터널 열기
ngrok http 5050
```

ngrok이 출력하는 `https://xxxx.ngrok-free.app` URL을 팀원에게 공유하면 됩니다.

### 4. GPU 리눅스 서버 배포 (나중에)

```bash
# Gunicorn + Nginx 사용
pip install gunicorn
gunicorn -w 4 -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
  -b 0.0.0.0:5050 app:app
```

---

## 사용 흐름

```
1. Admin 로그인 → 프로젝트 생성
2. 영상 업로드 (드래그&드롭)
3. 프레임 추출 (예: 2fps)
4. 라벨러 계정 추가
5. 프레임 배분 (자동 균등 배분)
6. 라벨러에게 URL 공유 → 각자 로그인
7. 어노테이션 진행
8. Admin에서 진행률 모니터링
9. 완료 후 COCO/YOLO 포맷으로 Export
10. GPU 서버에서 학습
```

---

## Export 포맷

| 포맷 | 용도 |
|------|------|
| COCO JSON | MMPose, ViTPose, RTMPose 학습 |
| YOLO Pose ZIP | YOLOv8-pose, YOLOv11-pose 학습 |
| CSV | Python 분석, 논문 결과 테이블 |

---

## 기술 스택

- Backend: Python 3.9 / Flask / Flask-SocketIO / SQLite
- Frontend: Vanilla JS / HTML5 Canvas (의존성 0)
- 영상 처리: OpenCV
- 배포: ngrok (단기) → Gunicorn+Nginx (장기)

---

## 향후 계획

- [ ] 광학 모션캡처 GT 데이터 import (C3D/BVH → keypoint 자동 변환)
- [ ] 2D → 3D lifting (MotionBERT 연동)
- [ ] 모델 학습 파이프라인 연동 (RTMPose / YOLOv8-pose)
- [ ] Inter-annotator agreement 측정
- [ ] 어노테이션 리뷰 워크플로우 (Admin이 라벨러 작업 검수)

---

## 필요한 외부 계정 (선택사항)

| 서비스 | 용도 | 비용 |
|--------|------|------|
| ngrok | 외부 팀원 접속 URL | 무료 (고정 URL은 $10/월) |
| GitHub | 코드 아카이빙 | 무료 |
| Weights & Biases | 학습 로그 시각화 | 무료 |
| HuggingFace | 학습된 모델 공유 | 무료 |

> 핵심 어노테이션 기능은 **외부 서비스 없이** 완전 로컬로 동작합니다.
