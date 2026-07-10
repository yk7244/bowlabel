"""
workflows.py - pilot/main frame distribution

Two modes:
  reset       : re-derive batch_type + assignments for ALL frames.
                Pilot frames are sampled *evenly across the whole ordered frame
                list* (so multiple videos are all represented), assigned to every
                selected labeler. The rest become main, round-robin balanced.
                Existing per-(frame,user) status is preserved so labeling progress
                is not lost when re-running.
  incremental : keep everything; only assign frames that currently have NO
                assignment (e.g. a newly uploaded/extracted video) as main,
                balanced onto the least-loaded selected labelers.
"""
from database import get_db, DEFAULT_PILOT_COUNT


def _even_sample(n, k):
    """Return k indices in [0, n) spread as evenly as possible (endpoints incl.)."""
    if k <= 0 or n <= 0:
        return []
    if k >= n:
        return list(range(n))
    if k == 1:
        return [n // 2]
    picks = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
    # fill in case rounding produced duplicates
    i = 0
    while len(picks) < k and i < n:
        if i not in picks:
            picks.append(i)
        i += 1
    return sorted(picks)[:k]


def _main_loads(conn, user_ids):
    """Current number of *main* frames assigned to each selected user."""
    loads = {uid: 0 for uid in user_ids}
    rows = conn.execute("""
        SELECT fa.user_id, COUNT(*) AS n
        FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        WHERE f.batch_type='main' AND fa.user_id IN ({})
        GROUP BY fa.user_id
    """.format(",".join("?" * len(user_ids))), user_ids).fetchall()
    for r in rows:
        loads[r["user_id"]] = r["n"]
    return loads


def setup_project_workflow(project_id, user_ids, pilot_count=None, mode="reset"):
    if not user_ids:
        raise ValueError("라벨러를 1명 이상 선택하세요.")

    conn = get_db()
    frames = conn.execute(
        "SELECT id FROM frames WHERE project_id=? ORDER BY video_id, frame_index",
        (project_id,)
    ).fetchall()
    if not frames:
        conn.close()
        raise ValueError("프레임이 없습니다. 먼저 영상을 추출하세요.")
    fids = [f["id"] for f in frames]

    if mode == "incremental":
        result = _incremental(conn, fids, user_ids)
    else:
        pilot_count = pilot_count if pilot_count is not None else DEFAULT_PILOT_COUNT
        conn.execute("UPDATE projects SET pilot_count=? WHERE id=?", (pilot_count, project_id))
        result = _reset(conn, fids, user_ids, pilot_count)

    conn.commit()
    conn.close()
    return result


def _reset(conn, fids, user_ids, pilot_count):
    n = len(fids)

    # preserve existing per-(frame,user) status so progress survives re-runs
    prev = {}
    for r in conn.execute(
        "SELECT frame_id, user_id, status FROM frame_assignments WHERE frame_id IN ({})".format(
            ",".join("?" * len(fids))), fids).fetchall():
        prev[(r["frame_id"], r["user_id"])] = r["status"]

    conn.execute("DELETE FROM frame_assignments WHERE frame_id IN ({})".format(
        ",".join("?" * len(fids))), fids)

    pilot_positions = set(_even_sample(n, min(pilot_count, n)))
    pilot_ids, main_ids = [], []
    for i, fid in enumerate(fids):
        if i in pilot_positions:
            pilot_ids.append(fid)
            conn.execute("UPDATE frames SET batch_type='pilot' WHERE id=?", (fid,))
        else:
            main_ids.append(fid)
            conn.execute("UPDATE frames SET batch_type='main' WHERE id=?", (fid,))

    def status_for(fid, uid):
        return prev.get((fid, uid), "unlabeled")

    # pilot: everyone labels every pilot frame
    for fid in pilot_ids:
        for uid in user_ids:
            conn.execute(
                "INSERT INTO frame_assignments (frame_id, user_id, status) VALUES (?,?,?)",
                (fid, uid, status_for(fid, uid)))

    # main: round-robin (ordered → each labeler gets a spread across videos)
    for i, fid in enumerate(main_ids):
        uid = user_ids[i % len(user_ids)]
        conn.execute(
            "INSERT INTO frame_assignments (frame_id, user_id, status) VALUES (?,?,?)",
            (fid, uid, status_for(fid, uid)))

    return {
        "mode": "reset",
        "total": len(fids),
        "pilot": len(pilot_ids),
        "main": len(main_ids),
        "labelers": len(user_ids),
        "main_per_user": len(main_ids) // len(user_ids) if user_ids else 0,
    }


def _incremental(conn, fids, user_ids):
    assigned = {r["frame_id"] for r in conn.execute(
        "SELECT DISTINCT frame_id FROM frame_assignments WHERE frame_id IN ({})".format(
            ",".join("?" * len(fids))), fids).fetchall()}
    new_ids = [fid for fid in fids if fid not in assigned]
    if not new_ids:
        return {"mode": "incremental", "added": 0, "labelers": len(user_ids)}

    loads = _main_loads(conn, user_ids)
    for fid in new_ids:
        conn.execute("UPDATE frames SET batch_type='main' WHERE id=?", (fid,))
        uid = min(user_ids, key=lambda u: loads[u])   # least-loaded first
        loads[uid] += 1
        conn.execute(
            "INSERT INTO frame_assignments (frame_id, user_id, status) VALUES (?,?, 'unlabeled')",
            (fid, uid))

    return {"mode": "incremental", "added": len(new_ids), "labelers": len(user_ids)}
