# 🎻 BowLabel

바이올린 활쓰기(bow technique) 교정을 위한 **키포인트 어노테이션 웹 서비스**

Roboflow 대비 장점: 동시 접속 라벨러 **무제한** / 데이터 완전 로컬 보관 / 바이올린 특화 19-kp 스키마

---

## 빠른 시작

```bash
cd keypont_organizer

# 의존성 (처음 한 번)
pip install flask flask-cors flask-login flask-socketio werkzeug opencv-python tqdm

# 실행
python3 app.py          # conda 환경 있으면 그냥 python3
# 또는 conda base 사용 시:
/opt/anaconda3/bin/python3 app.py
```

브라우저에서 → http://localhost:5050
기본 계정: **admin / admin1234**

---

## ngrok으로 팀원과 공유

```bash
# 1) ngrok 계정: https://dashboard.ngrok.com 가입 후 authtoken 복사
ngrok config add-authtoken <YOUR_TOKEN>

# 2) 앱 켜놓은 채로 새 터미널에서
ngrok http 5050

# 3) 출력된 URL 팀원에게 공유
#    예: https://abc123.ngrok-free.app
```

팀원은 저 URL로 접속 → 관리자가 만들어준 ID/PW로 로그인하면 바로 라벨링 가능

---

## 사용 흐름 (전체)

```
Admin                              Labeler
─────────────────────────────      ─────────────────────────
1. 프로젝트 생성                    (URL 받음)
   - 키포인트 스키마 설정
   - 기본 제공: 바이올린 19-kp

2. 영상 업로드 (드래그&드롭)
   - MP4/MOV/AVI/MKV, 최대 4GB
   - 연주자 ID, 세션 라벨 기록

3. 프레임 추출                     
   - FPS 지정 (예: 2fps)           
   - 구간 지정 (시작/종료 초)

4. 라벨러 계정 생성                5. 로그인
   - 아이디/비밀번호 설정           6. 내 작업 목록 확인
                                   7. 프레임 클릭 → 어노테이션
5. 프레임 배분 (자동 균등)           - 키포인트 순서대로 클릭
   - 여러 명 선택 → 배분              - 드래그로 위치 수정
                                     - 우클릭 = occluded
6. 진행률 모니터링                   - Enter = 저장+다음
   - 프레임 갤러리로 확인
   - 필터: 상태별 / 영상별

7. 검수 (Admin이 직접 확인)

8. Export
   - COCO JSON  → MMPose/ViTPose
   - YOLO Pose  → YOLOv8-pose
   - CSV        → Python 분석
```

---

## 어노테이션 UI 단축키

| 키 | 동작 |
|----|------|
| Enter | 저장 + 다음 프레임 |
| Tab | 다음 키포인트 선택 |
| ← → | 이전/다음 프레임 |
| 1~9 | 키포인트 직접 선택 |
| R | 현재 키포인트 초기화 |
| F | 화면 맞춤 (fit) |
| B | BBox 모드 토글 |
| 우클릭 | Occluded(가림) 토글 |
| 휠 | 줌 인/아웃 |
| 중간버튼 드래그 | 패닝 |

---

## 키포인트 스키마 (기본 19개)

| 그룹 | 키포인트 |
|------|---------|
| bow | tip, mid_upper, mid, mid_lower, frog |
| right_hand | thumb, index, middle, ring, pinky |
| right_arm | wrist, elbow, shoulder |
| left_arm | wrist, elbow, shoulder |
| violin | scroll, bridge, chin_rest |

프로젝트 생성 시 JSON으로 커스텀 가능

---

## 필요한 외부 계정

| 서비스 | 용도 | 비용 |
|--------|------|------|
| ngrok | 팀원 접속 URL 공유 | 무료 (고정 URL $10/월) |
| GitHub | 코드 아카이빙 | 무료 |
| W&B | 학습 실험 로그 | 무료 |
| HuggingFace | 모델 공유 | 무료 |

> 핵심 기능은 외부 서비스 없이 완전 로컬 동작

---

## GPU 서버 배포 (나중에)

```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5050 \
  --timeout 120 \
  "app:app"
```

---

## 기술 스택

- Backend: Python / Flask / Flask-SocketIO / SQLite
- Frontend: Vanilla JS / HTML5 Canvas (외부 의존성 0)
- 영상 처리: OpenCV
- DB: SQLite (WAL 모드, 동시 읽기 안전)
- Export: COCO JSON / YOLO Pose / CSV

## GitHub

https://github.com/yk7244/bowlabel
