"""
app.py - BowLabel v5: 9-keypoint annotation with automatic interaction bbox
"""
import json, os, sys, uuid, sqlite3, time
from pathlib import Path
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, send_from_directory, send_file, abort)
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from database import (get_db, init_db, reset_db, seed_admin,
                      DEFAULT_VIOLIN_SCHEMA, SKELETON_CONNECTIONS, GROUP_LABELS,
                      CLASS_NAME, QUALITY_OPTIONS, VIS_UNSET, VIS_VISIBLE,
                      BOW_SAMPLES, annotation_complete, coco_visibility,
                      generate_bbox)
from workflows import setup_project_workflow
from extractor import allowed_video, get_video_info, extract_frames_async, UPLOADS_DIR, FRAMES_DIR
from exporter import export_coco, export_yolo_pose, export_csv, export_agreement

BASE_DIR = Path(__file__).parent
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bowlabel-secret-2025")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
PW_METHOD = "pbkdf2:sha256"


def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        if session.get("role") != "admin":
            abort(403)
        return f(*a, **kw)
    return w


def _assignment_for(uid, frame_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM frame_assignments WHERE frame_id=? AND user_id=?",
        (frame_id, uid)
    ).fetchone()
    conn.close()
    return row


def _can_access_frame(uid, role, frame_id):
    if role == "admin":
        return True
    return _assignment_for(uid, frame_id) is not None


# ── auth ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("admin_dashboard" if session.get("role") == "admin" else "labeler_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u, p = request.form["username"].strip(), request.form["password"]
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE username=? AND is_active=1", (u,)).fetchone()
        conn.close()
        if row and check_password_hash(row["password_hash"], p):
            session.update(user_id=row["id"], username=row["username"], role=row["role"])
            return redirect(url_for("index"))
        error = "아이디 또는 비밀번호가 올바르지 않습니다."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── admin ─────────────────────────────────────────────────────────────────────

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    conn = get_db()
    projects = conn.execute("""
        SELECT p.*,
               COUNT(DISTINCT v.id) AS video_count,
               COUNT(DISTINCT f.id) AS frame_count
        FROM projects p
        LEFT JOIN videos v ON v.project_id=p.id
        LEFT JOIN frames f ON f.project_id=p.id
        WHERE p.status='active'
        GROUP BY p.id ORDER BY p.id DESC
    """).fetchall()
    users = conn.execute("""
        SELECT u.*, COUNT(DISTINCT a.id) AS labeled_count
        FROM users u LEFT JOIN annotations a ON a.labeled_by=u.id
        GROUP BY u.id ORDER BY labeled_count DESC
    """).fetchall()
    total_assign = conn.execute("SELECT COUNT(*) FROM frame_assignments").fetchone()[0]
    done_assign  = conn.execute(
        "SELECT COUNT(*) FROM frame_assignments WHERE status IN ('labeled','reviewed')"
    ).fetchone()[0]
    conn.close()
    return render_template("admin_dashboard.html",
                           projects=projects, users=users,
                           total_assign=total_assign, done_assign=done_assign)


@app.route("/admin/users/add", methods=["POST"])
@login_required
@admin_required
def add_user():
    u, p, role = request.form["username"].strip(), request.form["password"], request.form.get("role", "labeler")
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                     (u, generate_password_hash(p, method=PW_METHOD), role))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400
    conn.close()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:uid>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_user(uid):
    conn = get_db()
    conn.execute("UPDATE users SET is_active=1-is_active WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:uid>/reset_password", methods=["POST"])
@login_required
@admin_required
def reset_password(uid):
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(request.form["new_password"], method=PW_METHOD), uid))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/projects/create", methods=["GET", "POST"])
@login_required
@admin_required
def create_project():
    if request.method == "POST":
        name = request.form["name"].strip()
        desc = request.form.get("description", "")
        try:
            schema = json.loads(request.form.get("keypoint_schema", ""))
        except Exception:
            schema = DEFAULT_VIOLIN_SCHEMA
        conn = get_db()
        conn.execute(
            "INSERT INTO projects (name,description,class_name,keypoint_schema,created_by) VALUES (?,?,?,?,?)",
            (name, desc, CLASS_NAME, json.dumps(schema, ensure_ascii=False), session["user_id"])
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_dashboard"))
    return render_template("create_project.html",
                           default_schema=json.dumps(DEFAULT_VIOLIN_SCHEMA, indent=2, ensure_ascii=False),
                           schema_preview=DEFAULT_VIOLIN_SCHEMA,
                           class_name=CLASS_NAME)


@app.route("/admin/projects/<int:pid>")
@login_required
@admin_required
def project_detail(pid):
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj:
        abort(404)
    videos = conn.execute("""
        SELECT v.*, COUNT(f.id) AS frame_count
        FROM videos v LEFT JOIN frames f ON f.video_id=v.id
        WHERE v.project_id=? GROUP BY v.id ORDER BY v.id DESC
    """, (pid,)).fetchall()
    users = conn.execute("SELECT * FROM users WHERE role='labeler' AND is_active=1").fetchall()
    conn.close()
    return render_template("project_detail.html", proj=proj, videos=videos, users=users,
                           class_name=CLASS_NAME, quality_options=QUALITY_OPTIONS)


@app.route("/admin/projects/<int:pid>/agreement")
@login_required
@admin_required
def agreement_view(pid):
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj:
        abort(404)
    pilot_frames = conn.execute("""
        SELECT f.id, f.filename, f.frame_index, f.video_id,
               COUNT(DISTINCT fa.user_id) AS assignees,
               COUNT(DISTINCT a.id) AS annotations,
               SUM(CASE WHEN fa.status IN ('labeled','reviewed') THEN 1 ELSE 0 END) AS done
        FROM frames f
        JOIN frame_assignments fa ON fa.frame_id=f.id
        LEFT JOIN annotations a ON a.frame_id=f.id
        WHERE f.project_id=? AND f.batch_type='pilot'
        GROUP BY f.id ORDER BY f.video_id, f.frame_index
    """, (pid,)).fetchall()
    conn.close()
    return render_template("agreement.html", proj=proj, pilot_frames=pilot_frames)


@app.route("/admin/projects/<int:pid>/archive", methods=["POST"])
@login_required
@admin_required
def archive_project(pid):
    conn = get_db()
    conn.execute("UPDATE projects SET status='archived' WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))


# ── video ─────────────────────────────────────────────────────────────────────

@app.route("/admin/projects/<int:pid>/upload", methods=["POST"])
@login_required
@admin_required
def upload_video(pid):
    f = request.files.get("video")
    if not f or not allowed_video(f.filename):
        return jsonify({"error": "Invalid file type"}), 400
    ext = Path(f.filename).suffix.lower()
    stored = f"{uuid.uuid4().hex}{ext}"
    f.save(str(UPLOADS_DIR / stored))
    info = get_video_info(str(UPLOADS_DIR / stored))
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO videos (project_id,filename,original_name,player_id,session_label,notes,
                            fps,total_frames,width,height,duration_sec,uploaded_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (pid, stored, secure_filename(f.filename),
          request.form.get("player_id", "unknown"),
          request.form.get("session_label", ""),
          request.form.get("notes", ""),
          info.get("fps"), info.get("total_frames"),
          info.get("width"), info.get("height"),
          info.get("duration_sec"), session["user_id"]))
    conn.commit()
    vid = cur.lastrowid
    conn.close()
    return jsonify({"video_id": vid, "info": info})


@app.route("/admin/videos/<int:vid>/delete", methods=["POST"])
@login_required
@admin_required
def delete_video(vid):
    import shutil
    conn = get_db()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not row:
        abort(404)
    pid = row["project_id"]
    fids = [r["id"] for r in conn.execute("SELECT id FROM frames WHERE video_id=?", (vid,)).fetchall()]
    if fids:
        ph = ",".join("?" * len(fids))
        conn.execute(f"DELETE FROM annotations WHERE frame_id IN ({ph})", fids)
        conn.execute(f"DELETE FROM frame_assignments WHERE frame_id IN ({ph})", fids)
    conn.execute("DELETE FROM frames WHERE video_id=?", (vid,))
    conn.execute("DELETE FROM videos WHERE id=?", (vid,))
    conn.commit()
    conn.close()
    vp = UPLOADS_DIR / row["filename"]
    if vp.exists():
        vp.unlink()
    fp = FRAMES_DIR / str(vid)
    if fp.exists():
        shutil.rmtree(fp)
    return redirect(url_for("project_detail", pid=pid))


@app.route("/admin/videos/<int:vid>/extract", methods=["POST"])
@login_required
@admin_required
def trigger_extract(vid):
    conn = get_db()
    fids = [r["id"] for r in conn.execute("SELECT id FROM frames WHERE video_id=?", (vid,)).fetchall()]
    if fids:
        ph = ",".join("?" * len(fids))
        conn.execute(f"DELETE FROM annotations WHERE frame_id IN ({ph})", fids)
        conn.execute(f"DELETE FROM frame_assignments WHERE frame_id IN ({ph})", fids)
    conn.execute("DELETE FROM frames WHERE video_id=?", (vid,))
    conn.commit()
    conn.close()
    import shutil
    fp = FRAMES_DIR / str(vid)
    if fp.exists():
        shutil.rmtree(fp)
    extract_frames_async(vid,
                         float(request.form.get("fps_target", 2.0)),
                         float(request.form.get("start_sec", 0)),
                         float(request.form.get("end_sec", 0)),
                         socketio)
    return jsonify({"status": "extracting", "video_id": vid})


@app.route("/admin/videos/<int:vid>/status")
@login_required
def video_status(vid):
    conn = get_db()
    row = conn.execute("""
        SELECT v.status, COUNT(f.id) AS frame_count
        FROM videos v LEFT JOIN frames f ON f.video_id=v.id
        WHERE v.id=? GROUP BY v.id
    """, (vid,)).fetchone()
    conn.close()
    return jsonify(dict(row)) if row else abort(404)


# ── frames / assign ───────────────────────────────────────────────────────────

@app.route("/admin/projects/<int:pid>/frames")
@login_required
@admin_required
def frame_gallery(pid):
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj:
        abort(404)
    batch_filter = request.args.get("batch", "all")
    video_filter = request.args.get("video", "all")
    labeler_filter = request.args.get("labeler", "all")   # user_id or 'all'
    status_filter = request.args.get("status", "all")     # all | done | todo
    page = max(1, int(request.args.get("page", 1)))
    per = 60

    where, params = ["f.project_id=?"], [pid]
    if batch_filter != "all":
        where.append("f.batch_type=?")
        params.append(batch_filter)
    if video_filter != "all":
        where.append("f.video_id=?")
        params.append(int(video_filter))

    # When a specific labeler is selected we scope frames to that labeler's
    # assignment and expose *their* status, so admin can pick done vs. to-do.
    join_sql = "JOIN videos v ON v.id=f.video_id"
    my_status_sql = "NULL AS my_status"
    if labeler_filter != "all":
        join_sql += " JOIN frame_assignments mfa ON mfa.frame_id=f.id AND mfa.user_id=?"
        params.insert(0, int(labeler_filter))  # goes with the JOIN, before WHERE params
        my_status_sql = "mfa.status AS my_status"
        if status_filter == "done":
            where.append("mfa.status IN ('labeled','reviewed')")
        elif status_filter == "todo":
            where.append("mfa.status IN ('unlabeled','in_progress')")
    else:
        if status_filter == "done":
            where.append("""(SELECT COUNT(*) FROM frame_assignments fa WHERE fa.frame_id=f.id
                             AND fa.status NOT IN ('labeled','reviewed'))=0
                            AND (SELECT COUNT(*) FROM frame_assignments fa WHERE fa.frame_id=f.id)>0""")
        elif status_filter == "todo":
            where.append("""(SELECT COUNT(*) FROM frame_assignments fa WHERE fa.frame_id=f.id
                             AND fa.status IN ('unlabeled','in_progress'))>0""")

    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM frames f {join_sql} WHERE {where_sql}", params
    ).fetchone()[0]
    frames = conn.execute(f"""
        SELECT f.*, v.original_name, {my_status_sql},
               (SELECT COUNT(*) FROM frame_assignments fa WHERE fa.frame_id=f.id
                AND fa.status IN ('labeled','reviewed')) AS done_assignments,
               (SELECT COUNT(*) FROM frame_assignments fa WHERE fa.frame_id=f.id) AS total_assignments,
               (SELECT GROUP_CONCAT(u.username, ', ') FROM frame_assignments fa
                JOIN users u ON u.id=fa.user_id WHERE fa.frame_id=f.id) AS assignees
        FROM frames f {join_sql}
        WHERE {where_sql}
        ORDER BY f.video_id, f.frame_index
        LIMIT ? OFFSET ?
    """, params + [per, (page - 1) * per]).fetchall()
    videos = conn.execute("SELECT id, original_name FROM videos WHERE project_id=?", (pid,)).fetchall()
    labelers = conn.execute("SELECT id, username FROM users WHERE role='labeler' AND is_active=1").fetchall()
    conn.close()
    return render_template("frame_gallery.html",
                           proj=proj, frames=frames, videos=videos, labelers=labelers,
                           batch_filter=batch_filter, video_filter=video_filter,
                           labeler_filter=labeler_filter, status_filter=status_filter,
                           page=page, per=per,
                           total=total, pages=(total + per - 1) // per)


@app.route("/admin/projects/<int:pid>/setup-workflow", methods=["POST"])
@login_required
@admin_required
def setup_workflow(pid):
    data = request.get_json() or {}
    uids = data.get("user_ids", [])
    pilot_count = int(data.get("pilot_count", 20))
    mode = data.get("mode", "reset")
    try:
        result = setup_project_workflow(pid, uids, pilot_count, mode=mode)
        return jsonify({"ok": True, **result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/projects/<int:pid>/assignments")
@login_required
@admin_required
def assignment_overview(pid):
    """Who has what: per-labeler and per-video breakdown."""
    conn = get_db()
    by_user = conn.execute("""
        SELECT u.id AS user_id, u.username, f.batch_type,
               COUNT(*) AS total,
               SUM(CASE WHEN fa.status IN ('labeled','reviewed') THEN 1 ELSE 0 END) AS done
        FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        JOIN users u ON u.id=fa.user_id
        WHERE f.project_id=?
        GROUP BY u.id, f.batch_type
        ORDER BY u.username, f.batch_type
    """, (pid,)).fetchall()
    by_video = conn.execute("""
        SELECT v.id AS video_id, v.original_name, v.player_id,
               COUNT(DISTINCT f.id) AS frames,
               SUM(CASE WHEN f.batch_type='pilot' THEN 1 ELSE 0 END) AS pilot,
               COUNT(DISTINCT CASE WHEN fa.frame_id IS NULL THEN f.id END) AS unassigned
        FROM videos v
        LEFT JOIN frames f ON f.video_id=v.id
        LEFT JOIN frame_assignments fa ON fa.frame_id=f.id
        WHERE v.project_id=?
        GROUP BY v.id
        ORDER BY v.id
    """, (pid,)).fetchall()
    video_user = conn.execute("""
        SELECT f.video_id, u.username, f.batch_type, COUNT(*) AS n
        FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        JOIN users u ON u.id=fa.user_id
        WHERE f.project_id=?
        GROUP BY f.video_id, u.id, f.batch_type
        ORDER BY f.video_id, u.username
    """, (pid,)).fetchall()
    conn.close()
    return jsonify({
        "by_user": [dict(r) for r in by_user],
        "by_video": [dict(r) for r in by_video],
        "video_user": [dict(r) for r in video_user],
    })


@app.route("/admin/videos/<int:vid>/reassign", methods=["POST"])
@login_required
@admin_required
def reassign_video(vid):
    """Move all MAIN frames of a video to a single labeler (keeps annotations)."""
    data = request.get_json() or {}
    new_uid = data.get("user_id")
    if not new_uid:
        return jsonify({"error": "user_id required"}), 400
    conn = get_db()
    frames = conn.execute(
        "SELECT id FROM frames WHERE video_id=? AND batch_type='main'", (vid,)
    ).fetchall()
    moved = 0
    for f in frames:
        cur = conn.execute(
            "SELECT status FROM frame_assignments WHERE frame_id=? AND user_id=?",
            (f["id"], new_uid)
        ).fetchone()
        keep = cur["status"] if cur else "unlabeled"
        conn.execute("DELETE FROM frame_assignments WHERE frame_id=?", (f["id"],))
        conn.execute(
            "INSERT INTO frame_assignments (frame_id, user_id, status) VALUES (?,?,?)",
            (f["id"], new_uid, keep))
        moved += 1
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "moved": moved})


@app.route("/admin/frames/<int:fid>/reassign", methods=["POST"])
@login_required
@admin_required
def reassign_frame(fid):
    """Reassign a single MAIN frame to a labeler."""
    data = request.get_json() or {}
    new_uid = data.get("user_id")
    if not new_uid:
        return jsonify({"error": "user_id required"}), 400
    conn = get_db()
    fr = conn.execute("SELECT batch_type FROM frames WHERE id=?", (fid,)).fetchone()
    if not fr:
        conn.close()
        return jsonify({"error": "frame not found"}), 404
    if fr["batch_type"] == "pilot":
        conn.close()
        return jsonify({"error": "pilot frames are labeled by everyone; skipped", "skipped": True}), 200
    cur = conn.execute(
        "SELECT status FROM frame_assignments WHERE frame_id=? AND user_id=?",
        (fid, new_uid)
    ).fetchone()
    keep = cur["status"] if cur else "unlabeled"
    conn.execute("DELETE FROM frame_assignments WHERE frame_id=?", (fid,))
    conn.execute(
        "INSERT INTO frame_assignments (frame_id, user_id, status) VALUES (?,?,?)",
        (fid, new_uid, keep))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def _delete_frames(frame_ids):
    """Delete frames + their annotations/assignments + image files on disk."""
    if not frame_ids:
        return 0
    conn = get_db()
    ph = ",".join("?" * len(frame_ids))
    rows = conn.execute(
        f"SELECT id, video_id, filename FROM frames WHERE id IN ({ph})", frame_ids
    ).fetchall()
    conn.execute(f"DELETE FROM annotations WHERE frame_id IN ({ph})", frame_ids)
    conn.execute(f"DELETE FROM frame_assignments WHERE frame_id IN ({ph})", frame_ids)
    conn.execute(f"DELETE FROM frames WHERE id IN ({ph})", frame_ids)
    conn.commit()
    conn.close()
    for r in rows:
        fp = FRAMES_DIR / str(r["video_id"]) / r["filename"]
        if fp.exists():
            try:
                fp.unlink()
            except OSError:
                pass
    return len(rows)


@app.route("/admin/frames/<int:fid>/delete", methods=["POST"])
@login_required
@admin_required
def delete_frame(fid):
    n = _delete_frames([fid])
    return jsonify({"ok": True, "deleted": n})


@app.route("/admin/frames/delete", methods=["POST"])
@login_required
@admin_required
def delete_frames_bulk():
    data = request.get_json() or {}
    ids = [int(i) for i in data.get("frame_ids", [])]
    n = _delete_frames(ids)
    return jsonify({"ok": True, "deleted": n})


def _export_error_page(pid, message):
    back = url_for("project_detail", pid=pid)
    html = f"""<!doctype html><meta charset="utf-8">
    <div style="max-width:640px;margin:80px auto;font-family:sans-serif;color:#222">
      <h2>내보내기를 완료할 수 없습니다</h2>
      <p style="color:#b00">{message}</p>
      <p>라벨링이 완료된(<code>labeled</code>) 프레임이 있는지 확인하세요.
         진행 중(<code>in_progress</code>)이거나 미완료 프레임은 학습 export에 포함되지 않습니다.</p>
      <p><a href="{back}">← 프로젝트로 돌아가기</a></p>
    </div>"""
    return html, 400


@app.route("/admin/projects/<int:pid>/export/<fmt>")
@login_required
@admin_required
def export_project(pid, fmt):
    paths = {
        "coco": export_coco,
        "yolo": export_yolo_pose,
        "csv": export_csv,
        "agreement": export_agreement,
    }
    if fmt not in paths:
        abort(400)
    conn = get_db()
    proj = conn.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not proj:
        return _export_error_page(pid, "프로젝트를 찾을 수 없습니다. (DB가 초기화되었을 수 있습니다.)")
    try:
        path = paths[fmt](pid)
    except Exception as e:  # noqa: BLE001 - surface any exporter failure nicely
        app.logger.exception("export failed")
        return _export_error_page(pid, f"내보내기 중 오류: {e}")
    if not path or not Path(path).exists():
        return _export_error_page(pid, "내보낼 데이터가 없습니다.")
    return send_file(path, as_attachment=True)


# ── api stats ─────────────────────────────────────────────────────────────────

@app.route("/api/projects/<int:pid>/stats")
@login_required
def project_stats(pid):
    conn = get_db()
    frames = conn.execute("""
        SELECT batch_type, COUNT(*) AS n FROM frames WHERE project_id=? GROUP BY batch_type
    """, (pid,)).fetchall()
    assign_rows = conn.execute("""
        SELECT fa.status, COUNT(*) AS n
        FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        WHERE f.project_id=?
        GROUP BY fa.status
    """, (pid,)).fetchall()
    by_user = conn.execute("""
        SELECT u.username, f.batch_type,
               COUNT(*) AS total,
               SUM(CASE WHEN fa.status IN ('labeled','reviewed') THEN 1 ELSE 0 END) AS done
        FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        JOIN users u ON u.id=fa.user_id
        WHERE f.project_id=?
        GROUP BY u.id, f.batch_type
        ORDER BY u.username, f.batch_type
    """, (pid,)).fetchall()
    quality = conn.execute("""
        SELECT a.quality, COUNT(*) AS n
        FROM annotations a JOIN frames f ON f.id=a.frame_id
        WHERE f.project_id=? AND a.quality IS NOT NULL
        GROUP BY a.quality
    """, (pid,)).fetchall()
    conn.close()
    return jsonify({
        "frames_by_batch": {r["batch_type"]: r["n"] for r in frames},
        "assignments": {r["status"]: r["n"] for r in assign_rows},
        "by_user": [dict(r) for r in by_user],
        "quality": {r["quality"]: r["n"] for r in quality},
    })


@app.route("/api/assignments/<int:frame_id>/review", methods=["POST"])
@login_required
@admin_required
def review_assignment(frame_id):
    data = request.get_json() or {}
    uid = data.get("user_id")
    action = data.get("action", "approve")
    status = "reviewed" if action == "approve" else "labeled"
    conn = get_db()
    conn.execute("""
        UPDATE frame_assignments SET status=?, reviewed_by=?, reviewed_at=datetime('now')
        WHERE frame_id=? AND user_id=?
    """, (status, session["user_id"], frame_id, uid))
    conn.commit()
    conn.close()
    return jsonify({"status": status})


# ── labeler ───────────────────────────────────────────────────────────────────

@app.route("/labeler/claim", methods=["POST"])
@login_required
def claim_next_frame():
    uid = session["user_id"]
    batch_pref = request.get_json(silent=True) or {}
    prefer = batch_pref.get("batch")  # 'pilot' | 'main' | None

    conn = get_db()
    order = """
        ORDER BY
          CASE f.batch_type WHEN 'pilot' THEN 0 ELSE 1 END,
          CASE fa.status WHEN 'in_progress' THEN 0 WHEN 'unlabeled' THEN 1 ELSE 2 END,
          f.id ASC
    """
    if prefer == "main":
        order = """
        ORDER BY
          CASE fa.status WHEN 'in_progress' THEN 0 WHEN 'unlabeled' THEN 1 ELSE 2 END,
          f.id ASC
        """
    batch_clause = ""
    if prefer == "main":
        batch_clause = "AND f.batch_type='main'"
    elif prefer == "pilot":
        batch_clause = "AND f.batch_type='pilot'"
    row = conn.execute(f"""
        SELECT fa.frame_id, f.batch_type FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        WHERE fa.user_id=? AND fa.status IN ('unlabeled','in_progress')
        {batch_clause}
        {order} LIMIT 1
    """, (uid,)).fetchone()
    if row and row["frame_id"]:
        conn.execute(
            "UPDATE frame_assignments SET status='in_progress' WHERE frame_id=? AND user_id=? AND status='unlabeled'",
            (row["frame_id"], uid)
        )
        conn.commit()
    conn.close()
    if not row:
        return jsonify({"error": "남은 작업 없음"}), 404
    return jsonify({
        "frame_id": row["frame_id"],
        "batch": row["batch_type"],
        "url": url_for("annotate", frame_id=row["frame_id"])
    })


@app.route("/labeler")
@login_required
def labeler_dashboard():
    uid = session["user_id"]
    status_filter = request.args.get("status", "todo")   # todo | done | all
    page = max(1, int(request.args.get("page", 1)))
    per = 60

    status_sql = {
        "todo": "AND fa.status IN ('unlabeled','in_progress')",
        "done": "AND fa.status IN ('labeled','reviewed')",
        "all": "",
    }.get(status_filter, "")

    conn = get_db()
    total = conn.execute(f"""
        SELECT COUNT(*) FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        WHERE fa.user_id=? {status_sql}
    """, (uid,)).fetchone()[0]

    tasks = conn.execute(f"""
        SELECT fa.status, f.id, f.filename, f.frame_index, f.video_id, f.batch_type,
               v.original_name, p.name AS project_name, p.id AS project_id
        FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        JOIN videos v ON v.id=f.video_id
        JOIN projects p ON p.id=f.project_id
        WHERE fa.user_id=? {status_sql}
        ORDER BY CASE f.batch_type WHEN 'pilot' THEN 0 ELSE 1 END, f.video_id, f.frame_index
        LIMIT ? OFFSET ?
    """, (uid, per, (page - 1) * per)).fetchall()

    stats = conn.execute("""
        SELECT f.batch_type,
               COUNT(*) AS total,
               SUM(CASE WHEN fa.status IN ('labeled','reviewed') THEN 1 ELSE 0 END) AS done
        FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        WHERE fa.user_id=?
        GROUP BY f.batch_type
    """, (uid,)).fetchall()
    conn.close()
    return render_template("labeler_dashboard.html",
                           tasks=tasks, stats=stats, username=session["username"],
                           status_filter=status_filter, page=page,
                           total=total, pages=(total + per - 1) // per, per=per)


@app.route("/api/labeler/next-todo")
@login_required
def labeler_next_todo():
    """Next unlabeled/in-progress frame for the current user (Roboflow-style flow)."""
    uid = session["user_id"]
    exclude = request.args.get("exclude", type=int)
    conn = get_db()
    row = conn.execute("""
        SELECT fa.frame_id FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        WHERE fa.user_id=? AND fa.status IN ('unlabeled','in_progress')
          AND (? IS NULL OR fa.frame_id != ?)
        ORDER BY CASE f.batch_type WHEN 'pilot' THEN 0 ELSE 1 END, f.video_id, f.frame_index
        LIMIT 1
    """, (uid, exclude, exclude)).fetchone()
    conn.close()
    if not row:
        return jsonify({"frame_id": None})
    return jsonify({"frame_id": row["frame_id"], "url": url_for("annotate", frame_id=row["frame_id"])})


# ── annotate ──────────────────────────────────────────────────────────────────

def _neighbor_frame(conn, video_id, frame_index, uid, role, direction):
    op = "<" if direction == "prev" else ">"
    order = "DESC" if direction == "prev" else "ASC"
    if role == "admin":
        row = conn.execute(f"""
            SELECT f.id FROM frames f
            WHERE f.video_id=? AND f.frame_index {op} ?
            ORDER BY f.frame_index {order} LIMIT 1
        """, (video_id, frame_index)).fetchone()
    else:
        row = conn.execute(f"""
            SELECT f.id FROM frames f
            JOIN frame_assignments fa ON fa.frame_id=f.id AND fa.user_id=?
            WHERE f.video_id=? AND f.frame_index {op} ?
            ORDER BY f.frame_index {order} LIMIT 1
        """, (uid, video_id, frame_index)).fetchone()
    return row["id"] if row else None


def _parse_annotation(row):
    if not row:
        return None
    d = dict(row)
    d["keypoints"] = json.loads(d["keypoints"]) if d.get("keypoints") else []
    d["bbox"] = json.loads(d["bbox"]) if d.get("bbox") else None
    d["meta"] = json.loads(d["meta"]) if d.get("meta") else {}
    return d


@app.route("/annotate/<int:frame_id>")
@login_required
def annotate(frame_id):
    uid = session["user_id"]
    role = session.get("role")
    is_admin = role == "admin"
    if not _can_access_frame(uid, role, frame_id):
        abort(403)

    conn = get_db()
    frame = conn.execute("""
        SELECT f.*, v.width, v.height, v.original_name,
               p.keypoint_schema, p.class_name, p.name AS project_name, p.id AS project_id
        FROM frames f
        JOIN videos v ON v.id=f.video_id
        JOIN projects p ON p.id=f.project_id
        WHERE f.id=?
    """, (frame_id,)).fetchone()
    if not frame:
        abort(404)

    # Admin views are read-only reviews of a chosen labeler's work.
    annotators = conn.execute("""
        SELECT a.labeled_by AS user_id, u.username,
               fa.status AS status
        FROM annotations a
        JOIN users u ON u.id=a.labeled_by
        LEFT JOIN frame_assignments fa
               ON fa.frame_id=a.frame_id AND fa.user_id=a.labeled_by
        WHERE a.frame_id=?
        ORDER BY u.username
    """, (frame_id,)).fetchall()
    annotators = [dict(r) for r in annotators]

    review_mode = is_admin
    target_uid = uid
    if is_admin:
        requested = request.args.get("as_user", type=int)
        if requested and any(a["user_id"] == requested for a in annotators):
            target_uid = requested
        elif annotators:
            target_uid = annotators[0]["user_id"]
        else:
            target_uid = None

    existing = None
    if target_uid is not None:
        existing = conn.execute(
            "SELECT * FROM annotations WHERE frame_id=? AND labeled_by=? ORDER BY id DESC LIMIT 1",
            (frame_id, target_uid)
        ).fetchone()

    assignment = conn.execute(
        "SELECT * FROM frame_assignments WHERE frame_id=? AND user_id=?",
        (frame_id, uid)
    ).fetchone()

    prev_id = _neighbor_frame(conn, frame["video_id"], frame["frame_index"], uid, role, "prev")
    next_id = _neighbor_frame(conn, frame["video_id"], frame["frame_index"], uid, role, "next")
    conn.close()

    existing_parsed = _parse_annotation(existing)

    return render_template("annotate.html",
                           frame=frame,
                           assignment=dict(assignment) if assignment else None,
                           schema=json.loads(frame["keypoint_schema"]),
                           connections=SKELETON_CONNECTIONS,
                           group_labels=GROUP_LABELS,
                           class_name=frame["class_name"],
                           quality_options=QUALITY_OPTIONS,
                           existing=existing_parsed,
                           bow_samples=BOW_SAMPLES,
                           review_mode=review_mode,
                           annotators=annotators,
                           target_uid=target_uid,
                           prev_id=prev_id, next_id=next_id)


@app.route("/api/annotations", methods=["POST"])
@login_required
def save_annotation():
    d = request.get_json()
    frame_id = d["frame_id"]
    kps = d["keypoints"]
    meta = d.get("meta")
    notes = d.get("notes", "")
    quality = d.get("quality")
    uid = session["user_id"]
    role = session.get("role")

    if role == "admin":
        return jsonify({"error": "Admin view is read-only. Log in as a labeler to edit."}), 403
    if not _can_access_frame(uid, role, frame_id):
        return jsonify({"error": "Forbidden"}), 403

    conn = get_db()
    try:
        frame = conn.execute("""
            SELECT f.project_id, p.keypoint_schema, v.width, v.height
            FROM frames f
            JOIN videos v ON v.id=f.video_id
            JOIN projects p ON p.id=f.project_id
            WHERE f.id=?
        """, (frame_id,)).fetchone()
        if not frame:
            return jsonify({"error": "Frame not found"}), 404
        schema = json.loads(frame["keypoint_schema"])
        project_id = frame["project_id"]
    finally:
        conn.close()

    kps_sorted = sorted(kps, key=lambda k: k.get("kp_id", 0))
    bbox = generate_bbox(
        kps_sorted, schema,
        frame["width"] or 1920,
        frame["height"] or 1080,
    )
    complete, missing = annotation_complete(kps_sorted, bbox, schema)

    payload_kps = json.dumps(kps_sorted)
    payload_bbox = json.dumps(bbox) if bbox else None
    payload_meta = json.dumps(meta) if meta else None
    new_status = "labeled" if complete else "in_progress"

    last_err = None
    for attempt in range(5):
        conn = None
        try:
            conn = get_db()
            conn.execute("""
                INSERT INTO annotations (frame_id, project_id, labeled_by, keypoints, bbox, meta, quality, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(frame_id, labeled_by) DO UPDATE SET
                    keypoints=excluded.keypoints,
                    bbox=excluded.bbox,
                    meta=excluded.meta,
                    quality=excluded.quality,
                    notes=excluded.notes,
                    updated_at=datetime('now')
            """, (frame_id, project_id, uid, payload_kps, payload_bbox, payload_meta, quality, notes))
            conn.execute("""
                UPDATE frame_assignments SET status=?, labeled_at=datetime('now')
                WHERE frame_id=? AND user_id=?
            """, (new_status, frame_id, uid))
            conn.commit()
            last_err = None
            break
        except sqlite3.OperationalError as e:
            last_err = e
            if conn:
                conn.rollback()
            if "locked" in str(e).lower() and attempt < 4:
                time.sleep(0.05 * (2 ** attempt))
                continue
            raise
        finally:
            if conn:
                conn.close()

    if last_err:
        return jsonify({"error": str(last_err)}), 500

    miss_names = []
    id_to_name = {s["id"]: s.get("label", s["name"]) for s in schema}
    for m in missing:
        if m == "__bbox__":
            miss_names.append("automatic bbox unavailable (no core coordinates)")
        elif m == "__keypoints__":
            miss_names.append("keypoints")
        else:
            miss_names.append(id_to_name.get(int(m), m) if str(m).isdigit() else m)

    return jsonify({
        "saved": True,
        "status": new_status,
        "complete": complete,
        "missing": miss_names,
        "bbox": bbox,
    })


@app.route("/api/annotations/prev/<int:frame_id>")
@login_required
def get_prev_annotation(frame_id):
    uid = session["user_id"]
    conn = get_db()
    cur = conn.execute("SELECT video_id, frame_index FROM frames WHERE id=?", (frame_id,)).fetchone()
    if not cur:
        conn.close()
        return jsonify(None)
    prev = conn.execute("""
        SELECT a.keypoints, a.bbox, a.meta, a.notes, a.quality
        FROM frames f
        JOIN annotations a ON a.frame_id=f.id AND a.labeled_by=?
        WHERE f.video_id=? AND f.frame_index < ?
        ORDER BY f.frame_index DESC LIMIT 1
    """, (uid, cur["video_id"], cur["frame_index"])).fetchone()
    conn.close()
    if not prev:
        return jsonify(None)
    return jsonify(_parse_annotation(prev))


@app.route("/api/frames/<int:frame_id>/start", methods=["POST"])
@login_required
def start_frame(frame_id):
    uid = session["user_id"]
    if not _can_access_frame(uid, session.get("role"), frame_id):
        return jsonify({"error": "forbidden"}), 403
    conn = get_db()
    conn.execute("""
        UPDATE frame_assignments SET status='in_progress'
        WHERE frame_id=? AND user_id=? AND status='unlabeled'
    """, (frame_id, uid))
    conn.commit()
    conn.close()
    return jsonify({"status": "in_progress"})


@app.route("/api/annotations/<int:frame_id>")
@login_required
def get_annotation(frame_id):
    uid = session["user_id"]
    conn = get_db()
    ann = conn.execute(
        "SELECT * FROM annotations WHERE frame_id=? AND labeled_by=? ORDER BY id DESC LIMIT 1",
        (frame_id, uid)
    ).fetchone()
    conn.close()
    if not ann:
        return jsonify(None)
    return jsonify(_parse_annotation(ann))


@app.route("/frames/<int:video_id>/<path:filename>")
@login_required
def serve_frame(video_id, filename):
    return send_from_directory(str(FRAMES_DIR / str(video_id)), filename)


@socketio.on("connect")
def on_connect():
    emit("connected", {"msg": "ok"})


if __name__ == "__main__":
    # IMPORTANT: the reloader restarts this process on any file change (including
    # writes to exports/ during an export). If --reset were re-applied on every
    # restart it would wipe the database. We therefore (1) never enable the
    # reloader, and (2) only honour --reset in the initial launch process.
    is_reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if "--reset" in sys.argv and not is_reloader_child:
        confirm = "--yes" in sys.argv
        if not confirm:
            try:
                ans = input("[!] --reset will DELETE all labeling data in data.db. "
                            "Type 'reset' to confirm: ").strip().lower()
                confirm = ans == "reset"
            except (EOFError, KeyboardInterrupt):
                confirm = False
        if confirm:
            reset_db()
        else:
            print("[DB] reset aborted; keeping existing data.")
            init_db()
    else:
        init_db()
    seed_admin("admin", "admin1234")
    print("\n" + "=" * 52)
    print("  BowLabel v5 — 9 core keypoints · automatic interaction bbox")
    print("  http://localhost:5050   (admin / admin1234)")
    print("  Reset DB:  python3 app.py --reset   (asks for confirmation)")
    print("=" * 52 + "\n")
    # debug/reloader disabled on purpose: protects data.db from restart loops.
    socketio.run(app, host="0.0.0.0", port=5050,
                 debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
