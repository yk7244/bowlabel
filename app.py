"""
app.py - BowLabel v3: master 9-keypoint annotation
"""
import json, os, sys, uuid
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
                      annotation_complete, coco_visibility)
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
    page = max(1, int(request.args.get("page", 1)))
    per = 60
    where, params = ["f.project_id=?"], [pid]
    if batch_filter != "all":
        where.append("f.batch_type=?")
        params.append(batch_filter)
    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM frames f WHERE {where_sql}", params).fetchone()[0]
    frames = conn.execute(f"""
        SELECT f.*, v.original_name,
               (SELECT COUNT(*) FROM frame_assignments fa WHERE fa.frame_id=f.id
                AND fa.status IN ('labeled','reviewed')) AS done_assignments,
               (SELECT COUNT(*) FROM frame_assignments fa WHERE fa.frame_id=f.id) AS total_assignments
        FROM frames f JOIN videos v ON v.id=f.video_id
        WHERE {where_sql}
        ORDER BY f.video_id, f.frame_index
        LIMIT ? OFFSET ?
    """, params + [per, (page - 1) * per]).fetchall()
    videos = conn.execute("SELECT id, original_name FROM videos WHERE project_id=?", (pid,)).fetchall()
    conn.close()
    return render_template("frame_gallery.html",
                           proj=proj, frames=frames, videos=videos,
                           batch_filter=batch_filter, page=page, per=per,
                           total=total, pages=(total + per - 1) // per)


@app.route("/admin/projects/<int:pid>/setup-workflow", methods=["POST"])
@login_required
@admin_required
def setup_workflow(pid):
    data = request.get_json() or {}
    uids = data.get("user_ids", [])
    pilot_count = int(data.get("pilot_count", 20))
    try:
        result = setup_project_workflow(pid, uids, pilot_count)
        return jsonify({"ok": True, **result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


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
    path = paths[fmt](pid)
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
    conn = get_db()
    tasks = conn.execute("""
        SELECT fa.status, f.id, f.filename, f.frame_index, f.timestamp_sec, f.batch_type,
               v.original_name, p.name AS project_name, p.id AS project_id
        FROM frame_assignments fa
        JOIN frames f ON f.id=fa.frame_id
        JOIN videos v ON v.id=f.video_id
        JOIN projects p ON p.id=f.project_id
        WHERE fa.user_id=? AND fa.status IN ('unlabeled','in_progress')
        ORDER BY CASE f.batch_type WHEN 'pilot' THEN 0 ELSE 1 END, f.id
        LIMIT 300
    """, (uid,)).fetchall()
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
                           tasks=tasks, stats=stats, username=session["username"])


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


@app.route("/annotate/<int:frame_id>")
@login_required
def annotate(frame_id):
    uid = session["user_id"]
    role = session.get("role")
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

    existing = conn.execute(
        "SELECT * FROM annotations WHERE frame_id=? AND labeled_by=? ORDER BY id DESC LIMIT 1",
        (frame_id, uid)
    ).fetchone()
    assignment = conn.execute(
        "SELECT * FROM frame_assignments WHERE frame_id=? AND user_id=?",
        (frame_id, uid)
    ).fetchone()

    prev_id = _neighbor_frame(conn, frame["video_id"], frame["frame_index"], uid, role, "prev")
    next_id = _neighbor_frame(conn, frame["video_id"], frame["frame_index"], uid, role, "next")
    conn.close()

    existing_parsed = None
    if existing:
        existing_parsed = dict(existing)
        existing_parsed["keypoints"] = json.loads(existing["keypoints"])
        if existing_parsed.get("bbox"):
            existing_parsed["bbox"] = json.loads(existing_parsed["bbox"])

    return render_template("annotate.html",
                           frame=frame,
                           assignment=dict(assignment) if assignment else None,
                           schema=json.loads(frame["keypoint_schema"]),
                           connections=SKELETON_CONNECTIONS,
                           group_labels=GROUP_LABELS,
                           class_name=frame["class_name"],
                           quality_options=QUALITY_OPTIONS,
                           existing=existing_parsed,
                           prev_id=prev_id, next_id=next_id)


@app.route("/api/annotations", methods=["POST"])
@login_required
def save_annotation():
    d = request.get_json()
    frame_id = d["frame_id"]
    kps = d["keypoints"]
    bbox = d.get("bbox")
    notes = d.get("notes", "")
    quality = d.get("quality")
    uid = session["user_id"]
    role = session.get("role")

    if not _can_access_frame(uid, role, frame_id):
        return jsonify({"error": "Forbidden"}), 403

    conn = get_db()
    frame = conn.execute("""
        SELECT f.project_id, p.keypoint_schema FROM frames f
        JOIN projects p ON p.id=f.project_id WHERE f.id=?
    """, (frame_id,)).fetchone()
    schema = json.loads(frame["keypoint_schema"])
    n_kp = len(schema)

    # kp_id 순서 정렬 보장
    kps_sorted = sorted(kps, key=lambda k: k.get("kp_id", 0))
    complete, missing = annotation_complete(kps_sorted, bbox, n_kp)

    payload_kps = json.dumps(kps_sorted)
    payload_bbox = json.dumps(bbox) if bbox else None

    ex = conn.execute(
        "SELECT id FROM annotations WHERE frame_id=? AND labeled_by=?", (frame_id, uid)
    ).fetchone()
    if ex:
        conn.execute("""
            UPDATE annotations SET keypoints=?,bbox=?,quality=?,notes=?,updated_at=datetime('now')
            WHERE id=?
        """, (payload_kps, payload_bbox, quality, notes, ex["id"]))
    else:
        conn.execute("""
            INSERT INTO annotations (frame_id,project_id,labeled_by,keypoints,bbox,quality,notes)
            VALUES (?,?,?,?,?,?,?)
        """, (frame_id, frame["project_id"], uid, payload_kps, payload_bbox, quality, notes))

    new_status = "labeled" if complete else "in_progress"
    conn.execute("""
        UPDATE frame_assignments SET status=?, labeled_at=datetime('now')
        WHERE frame_id=? AND user_id=?
    """, (new_status, frame_id, uid))

    conn.commit()
    conn.close()

    miss_names = []
    id_to_name = {s["id"]: s.get("label_short", s["name"]) for s in schema}
    for m in missing:
        if m == "__bbox__":
            miss_names.append("bbox")
        elif m == "__keypoints__":
            miss_names.append("keypoints")
        else:
            miss_names.append(id_to_name.get(int(m), m) if str(m).isdigit() else m)

    return jsonify({
        "saved": True,
        "status": new_status,
        "complete": complete,
        "missing": miss_names,
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
        SELECT a.keypoints, a.bbox, a.notes, a.quality
        FROM frames f
        JOIN annotations a ON a.frame_id=f.id AND a.labeled_by=?
        WHERE f.video_id=? AND f.frame_index < ?
        ORDER BY f.frame_index DESC LIMIT 1
    """, (uid, cur["video_id"], cur["frame_index"])).fetchone()
    conn.close()
    if not prev:
        return jsonify(None)
    d = dict(prev)
    d["keypoints"] = json.loads(d["keypoints"])
    if d.get("bbox"):
        d["bbox"] = json.loads(d["bbox"])
    return jsonify(d)


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
    d = dict(ann)
    d["keypoints"] = json.loads(d["keypoints"])
    if d.get("bbox"):
        d["bbox"] = json.loads(d["bbox"])
    return jsonify(d)


@app.route("/frames/<int:video_id>/<path:filename>")
@login_required
def serve_frame(video_id, filename):
    return send_from_directory(str(FRAMES_DIR / str(video_id)), filename)


@socketio.on("connect")
def on_connect():
    emit("connected", {"msg": "ok"})


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset_db()
    else:
        init_db()
    seed_admin("admin", "admin1234")
    print("\n" + "=" * 50)
    print("  BowLabel v3 — violin_bowing_scene · 9 keypoints")
    print("  http://localhost:5050")
    print("  admin / admin1234")
    print("  DB 초기화: python3 app.py --reset")
    print("=" * 50 + "\n")
    socketio.run(app, host="0.0.0.0", port=5050, debug=True, allow_unsafe_werkzeug=True)
