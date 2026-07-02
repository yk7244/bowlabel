"""
workflows.py - pilot/main 배분 로직
"""
from database import get_db, DEFAULT_PILOT_COUNT


def setup_project_workflow(project_id: int, user_ids: list, pilot_count: int = None):
    """
    1) 앞쪽 pilot_count 프레임 → batch_type='pilot', 전원 배정
    2) 나머지 → batch_type='main', 균등 배정 (프레임당 1명)
  """
    if not user_ids:
        raise ValueError("라벨러를 1명 이상 선택해")

    pilot_count = pilot_count if pilot_count is not None else DEFAULT_PILOT_COUNT
    conn = get_db()

    frames = conn.execute(
        "SELECT id FROM frames WHERE project_id=? ORDER BY video_id, frame_index",
        (project_id,)
    ).fetchall()
    if not frames:
        conn.close()
        raise ValueError("프레임이 없어. 먼저 영상 추출해")

    conn.execute("UPDATE projects SET pilot_count=? WHERE id=?", (pilot_count, project_id))

    # 기존 배정 초기화
    fids = [f["id"] for f in frames]
    placeholders = ",".join("?" * len(fids))
    conn.execute(f"DELETE FROM frame_assignments WHERE frame_id IN ({placeholders})", fids)

    pilot_n = min(pilot_count, len(frames))
    pilot_ids = [f["id"] for f in frames[:pilot_n]]
    main_ids  = [f["id"] for f in frames[pilot_n:]]

    for fid in pilot_ids:
        conn.execute("UPDATE frames SET batch_type='pilot' WHERE id=?", (fid,))
        for uid in user_ids:
            conn.execute(
                "INSERT INTO frame_assignments (frame_id, user_id, status) VALUES (?,?, 'unlabeled')",
                (fid, uid)
            )

    for i, fid in enumerate(main_ids):
        conn.execute("UPDATE frames SET batch_type='main' WHERE id=?", (fid,))
        uid = user_ids[i % len(user_ids)]
        conn.execute(
            "INSERT INTO frame_assignments (frame_id, user_id, status) VALUES (?,?, 'unlabeled')",
            (fid, uid)
        )

    conn.commit()
    conn.close()
    return {
        "total": len(frames),
        "pilot": len(pilot_ids),
        "main": len(main_ids),
        "labelers": len(user_ids),
        "main_per_user": len(main_ids) // len(user_ids) if user_ids else 0,
    }
