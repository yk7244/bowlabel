# BowLabel v3

내부용 바이올린 활쓰기 **master annotation** 도구

## 한 프레임 = 한 객체

```
class: violin_bowing_scene
bbox:  9점 + 보이는 활 전체
kps:   9개 전부 (안 보이면 occluded/outside, 추측 금지)
```

### 9 keypoints

1. bridge_g_foot
2. bridge_e_foot
3. fingerboard_nut_g_corner
4. fingerboard_nut_e_corner
5. fingerboard_body_g_corner
6. fingerboard_body_e_corner
7. bow_frog_endpoint
8. bow_tip_endpoint
9. bow_midpoint_visible

### visibility

| 값 | 의미 |
|----|------|
| 2 | visible — 실제 클릭 |
| 1 | occluded — 가림 |
| 3 | outside — 프레임 밖 |
| 0 | 미처리 (저장 불완료) |

## 워크플로 (100장 예시)

1. 영상 업로드 → 프레임 추출
2. **워크플로 배분**: Pilot 20장(5명 전원) + Main 80장(16장씩)
3. Pilot 20장으로 라벨러 agreement 확인
4. Main 라벨링
5. Export (YOLO/COCO/CSV) + Pilot Agreement CSV

## 실행

```bash
pip install -r requirements.txt
python3 app.py --reset    # DB 초기화 (기존 데이터 삭제)
python3 app.py
# http://localhost:5050  admin / admin1234
```

## 단축키 (라벨링)

| 키 | 동작 |
|----|------|
| B | bbox 모드 |
| 클릭 | visible |
| 우클릭 / O | occluded |
| U | outside |
| C | 이전 프레임 복사 |
| Enter | 저장 |
