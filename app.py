"""
app.py - BowLabel main Flask application
"""
import json, os, uuid
from pathlib import Path
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, send_from_directory, send_file, abort, flash)
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from database import (get_db, init_db, seed_admin, DEFAULT_VIOLIN_SCHEMA,
                      SKELETON_CONNECTIONS, GROUP_LABELS, REQUIRED_KEYPOINTS)
from extractor import allowed_video, get_video_info, extract_frames_async, UPLOADS_DIR, FRAMES_DIR
from exporter  import export_coco, export_yolo_pose, export_csv, EXPORTS_DIR

BASE_DIR = Path(__file__).parent
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bowlabel-secret-2025")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4GB
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

PW_METHOD = "pbkdf2:sha256"

# ── auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "user_id" not in session: return redirect(url_for("login"))
        return f(*a, **kw)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        if session.get("role") != "admin": abort(403)
        return f(*a, **kw)
    return w

# ── auth ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if "user_id" not in session: return redirect(url_for("login"))
    return redirect(url_for("admin_dashboard" if session.get("role") == "admin" else "labeler_dashboard"))

@app.route("/login", methods=["GET","POST"])
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
    session.clear(); return redirect(url_for("login"))

# ── admin: dashboard ──────────────────────────────────────────────────────────

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    conn = get_db()
    projects = conn.execute("""
        SELECT p.*,
               COUNT(DISTINCT v.id)  AS video_count,
               COUNT(DISTINCT f.id)  AS frame_count,
               SUM(CASE WHEN f.status IN ('labeled','reviewed') THEN 1 ELSE 0 END) AS labeled_count,
               SUM(CASE WHEN f.status='reviewed' THEN 1 ELSE 0 END) AS reviewed_count
        FROM projects p
        LEFT JOIN videos v ON v.project_id=p.id
        LEFT JOIN frames f ON f.project_id=p.id
        WHERE p.status='active'
        GROUP BY p.id ORDER BY p.id DESC
    """).fetchall()
    users = conn.execute("""
        SELECT u.*, COUNT(DISTINCT a.frame_id) AS labeled_count
        FROM users u LEFT JOIN annotations a ON a.labeled_by=u.id
        GROUP BY u.id ORDER BY labeled_count DESC, u.id
    """).fetchall()
    total_frames   = conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
    labeled_frames = conn.execute("SELECT COUNT(*) FROM frames WHERE status IN ('labeled','reviewed')").fetchone()[0]
    conn.close()
    return render_template("admin_dashboard.html",
                           projects=projects, users=users,
                           total_frames=total_frames, labeled_frames=labeled_frames)

# ── admin: users ──────────────────────────────────────────────────────────────

@app.route("/admin/users/add", methods=["POST"])
@login_required
@admin_required
def add_user():
    u, p, role = request.form["username"].strip(), request.form["password"], request.form.get("role","labeler")
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                     (u, generate_password_hash(p, method=PW_METHOD), role))
        conn.commit()
    except Exception as e:
        conn.close(); return jsonify({"error": str(e)}), 400
    conn.close(); return redirect(url_for("admin_dashboard"))

@app.route("/admin/users/<int:uid>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_user(uid):
    conn = get_db()
    conn.execute("UPDATE users SET is_active=1-is_active WHERE id=?", (uid,))
    conn.commit(); conn.close()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/users/<int:uid>/reset_password", methods=["POST"])
@login_required
@admin_required
def reset_password(uid):
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(request.form["new_password"], method=PW_METHOD), uid))
    conn.commit(); conn.close()
    return redirect(url_for("admin_dashboard"))

# ── admin: projects ───────────────────────────────────────────────────────────

@app.route("/admin/projects/create", methods=["GET","POST"])
@login_required
@admin_required
def create_project():
    if request.method == "POST":
        name = request.form["name"].strip()
        desc = request.form.get("description","")
        try:
            schema = json.loads(request.form.get("keypoint_schema",""))
        except Exception:
            schema = DEFAULT_VIOLIN_SCHEMA
        conn = get_db()
        conn.execute("INSERT INTO projects (name,description,keypoint_schema,created_by) VALUES (?,?,?,?)",
                     (name, desc, json.dumps(schema, ensure_ascii=False), session["user_id"]))
        conn.commit(); conn.close()
        return redirect(url_for("admin_dashboard"))
    return render_template("create_project.html",
                           default_schema=json.dumps(DEFAULT_VIOLIN_SCHEMA, indent=2, ensure_ascii=False),
                           schema_preview=DEFAULT_VIOLIN_SCHEMA,
                           required_kps=REQUIRED_KEYPOINTS)

@app.route("/admin/projects/<int:pid>")
@login_required
@admin_required
def project_detail(pid):
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj: abort(404)
    videos = conn.execute("""
        SELECT v.*, COUNT(f.id) AS frame_count,
               SUM(CASE WHEN f.status IN ('labeled','reviewed') THEN 1 ELSE 0 END) AS done_count
        FROM videos v LEFT JOIN frames f ON f.video_id=v.id
        WHERE v.project_id=? GROUP BY v.id ORDER BY v.id DESC
    """, (pid,)).fetchall()
    users = conn.execute("SELECT * FROM users WHERE role='labeler' AND is_active=1").fetchall()
    conn.close()
    return render_template("project_detail.html", proj=proj, videos=videos, users=users)

@app.route("/admin/projects/<int:pid>/archive", methods=["POST"])
@login_required
@admin_required
def archive_project(pid):
    conn = get_db()
    conn.execute("UPDATE projects SET status='archived' WHERE id=?", (pid,))
    conn.commit(); conn.close()
    return redirect(url_for("admin_dashboard"))

# ── admin: video upload / manage ──────────────────────────────────────────────

@app.route("/admin/projects/<int:pid>/upload", methods=["POST"])
@login_required
@admin_required
def upload_video(pid):
    f = request.files.get("video")
    if not f or not allowed_video(f.filename):
        return jsonify({"error": "Invalid file type"}), 400
    ext = Path(f.filename).suffix.lower()
    stored = f"{uuid.uuid4().hex}{ext}"
    save_path = UPLOADS_DIR / stored
    f.save(str(save_path))
    info = get_video_info(str(save_path))
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO videos (project_id,filename,original_name,player_id,session_label,notes,
                            fps,total_frames,width,height,duration_sec,uploaded_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (pid, stored, secure_filename(f.filename),
          request.form.get("player_id","unknown"),
          request.form.get("session_label",""),
          request.form.get("notes",""),
          info.get("fps"), info.get("total_frames"),
          info.get("width"), info.get("height"),
          info.get("duration_sec"), session["user_id"]))
    conn.commit(); vid = cur.lastrowid; conn.close()
    return jsonify({"video_id": vid, "info": info})

@app.route("/admin/videos/<int:vid>/delete", methods=["POST"])
@login_required
@admin_required
def delete_video(vid):
    import shutil
    conn = get_db()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not row: abort(404)
    pid = row["project_id"]
    # 파일 삭제
    vp = UPLOADS_DIR / row["filename"]
    if vp.exists(): vp.unlink()
    fp = FRAMES_DIR / str(vid)
    if fp.exists(): shutil.rmtree(fp)
    # DB 삭제
    conn.execute("DELETE FROM annotations WHERE frame_id IN (SELECT id FROM frames WHERE video_id=?)", (vid,))
    conn.execute("DELETE FROM frames WHERE video_id=?", (vid,))
    conn.execute("DELETE FROM videos WHERE id=?", (vid,))
    conn.commit(); conn.close()
    return redirect(url_for("project_detail", pid=pid))

@app.route("/admin/videos/<int:vid>/extract", methods=["POST"])
@login_required
@admin_required
def trigger_extract(vid):
    # 기존 프레임 초기화 후 재추출
    conn = get_db()
    conn.execute("DELETE FROM annotations WHERE frame_id IN (SELECT id FROM frames WHERE video_id=?)", (vid,))
    conn.execute("DELETE FROM frames WHERE video_id=?", (vid,))
    conn.commit(); conn.close()
    import shutil
    fp = FRAMES_DIR / str(vid)
    if fp.exists(): shutil.rmtree(fp)
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
        SELECT v.status, COUNT(f.id) AS frame_count,
               SUM(CASE WHEN f.status IN ('labeled','reviewed') THEN 1 ELSE 0 END) AS done_count
        FROM videos v LEFT JOIN frames f ON f.video_id=v.id
        WHERE v.id=? GROUP BY v.id
    """, (vid,)).fetchone()
    conn.close()
    return jsonify(dict(row)) if row else abort(404)

# ── admin: frames gallery ─────────────────────────────────────────────────────

@app.route("/admin/projects/<int:pid>/frames")
@login_required
@admin_required
def frame_gallery(pid):
    conn = get_db()
    proj  = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj: abort(404)
    status_filter = request.args.get("status","all")
    video_filter  = request.args.get("video_id","all")
    page = max(1, int(request.args.get("page", 1)))
    per  = 60

    where, params = ["f.project_id=?"], [pid]
    if status_filter != "all": where.append("f.status=?"); params.append(status_filter)
    if video_filter  != "all": where.append("f.video_id=?"); params.append(int(video_filter))
    where_sql = " AND ".join(where)

    total = conn.execute(f"SELECT COUNT(*) FROM frames f WHERE {where_sql}", params).fetchone()[0]
    frames = conn.execute(f"""
        SELECT f.*, v.original_name,
               u.username AS labeled_by_name
        FROM frames f
        JOIN videos v ON v.id=f.video_id
        LEFT JOIN users u ON u.id=f.labeled_by
        WHERE {where_sql}
        ORDER BY f.video_id, f.frame_index
        LIMIT ? OFFSET ?
    """, params + [per, (page-1)*per]).fetchall()
    videos = conn.execute("SELECT id, original_name FROM videos WHERE project_id=?", (pid,)).fetchall()
    conn.close()
    return render_template("frame_gallery.html",
                           proj=proj, frames=frames, videos=videos,
                           status_filter=status_filter, video_filter=video_filter,
                           page=page, per=per, total=total,
                           pages=(total + per - 1) // per)

# ── admin: assign ─────────────────────────────────────────────────────────────

@app.route("/admin/projects/<int:pid>/assign", methods=["POST"])
@login_required
@admin_required
def assign_frames(pid):
    data = request.get_json()
    uids = data.get("user_ids", [])
    if not uids: return jsonify({"error": "No users"}), 400
    conn = get_db()
    frames = conn.execute(
        "SELECT id FROM frames WHERE project_id=? AND status='unlabeled' ORDER BY id", (pid,)
    ).fetchall()
    for i, fr in enumerate(frames):
        conn.execute("UPDATE frames SET assigned_to=? WHERE id=?", (uids[i % len(uids)], fr["id"]))
    conn.commit(); conn.close()
    return jsonify({"assigned": len(frames), "to": len(uids)})

# ── admin: export ─────────────────────────────────────────────────────────────

@app.route("/admin/projects/<int:pid>/export/<fmt>")
@login_required
@admin_required
def export_project(pid, fmt):
    paths = {"coco": export_coco, "yolo": export_yolo_pose, "csv": export_csv}
    if fmt not in paths: abort(400)
    path = paths[fmt](pid)
    return send_file(path, as_attachment=True)

# ── api: stats ────────────────────────────────────────────────────────────────

@app.route("/api/projects/<int:pid>/stats")
@login_required
def project_stats(pid):
    conn = get_db()
    stats = conn.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status='unlabeled'   THEN 1 ELSE 0 END) AS unlabeled,
               SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) AS in_progress,
               SUM(CASE WHEN status='labeled'     THEN 1 ELSE 0 END) AS labeled,
               SUM(CASE WHEN status='reviewed'    THEN 1 ELSE 0 END) AS reviewed
        FROM frames WHERE project_id=?
    """, (pid,)).fetchone()
    per_user = conn.execute("""
        SELECT u.username, COUNT(a.id) AS count
        FROM annotations a JOIN users u ON u.id=a.labeled_by
        JOIN frames f ON f.id=a.frame_id
        WHERE f.project_id=?
        GROUP BY a.labeled_by ORDER BY count DESC
    """, (pid,)).fetchall()
    conn.close()
    return jsonify({"frames": dict(stats), "per_user": [dict(r) for r in per_user]})

# ── api: review ───────────────────────────────────────────────────────────────

@app.route("/api/frames/<int:frame_id>/review", methods=["POST"])
@login_required
@admin_required
def review_frame(frame_id):
    action = request.get_json().get("action","approve")  # approve | reject
    status = "reviewed" if action == "approve" else "labeled"
    conn = get_db()
    conn.execute("UPDATE frames SET status=?, reviewed_by=?, reviewed_at=datetime('now') WHERE id=?",
                 (status, session["user_id"], frame_id))
    conn.commit(); conn.close()
    return jsonify({"status": status})

# ── labeler ───────────────────────────────────────────────────────────────────

@app.route("/labeler/claim", methods=["POST"])
@login_required
def claim_next_frame():
    """배정된 작업 중 다음 미완료 프레임 하나를 가져와 바로 어노테이션으로 이동"""
    uid = session["user_id"]
    conn = get_db()
    row = conn.execute("""
        SELECT id FROM frames
        WHERE assigned_to=? AND status IN ('unlabeled','in_progress')
        ORDER BY
          CASE status WHEN 'in_progress' THEN 0 ELSE 1 END,
          id ASC
        LIMIT 1
    """, (uid,)).fetchone()
    if row:
        conn.execute("UPDATE frames SET status='in_progress' WHERE id=? AND status='unlabeled'",
                     (row["id"],))
        conn.commit()
    conn.close()
    if not row:
        return jsonify({"error": "배정된 작업이 없습니다."}), 404
    return jsonify({"frame_id": row["id"], "url": url_for("annotate", frame_id=row["id"])})

@app.route("/labeler")
@login_required
def labeler_dashboard():
    uid = session["user_id"]
    conn = get_db()
    tasks = conn.execute("""
        SELECT f.id, f.filename, f.status, f.frame_index, f.timestamp_sec,
               v.original_name, v.id AS video_id,
               p.name AS project_name, p.id AS project_id
        FROM frames f
        JOIN videos v ON v.id=f.video_id
        JOIN projects p ON p.id=f.project_id
        WHERE f.assigned_to=? AND f.status IN ('unlabeled','in_progress')
        ORDER BY f.id LIMIT 200
    """, (uid,)).fetchall()
    done = conn.execute(
        "SELECT COUNT(*) FROM frames WHERE assigned_to=? AND status IN ('labeled','reviewed')", (uid,)
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM frames WHERE assigned_to=?", (uid,)
    ).fetchone()[0]
    conn.close()
    in_progress = sum(1 for t in tasks if t["status"] == "in_progress")
    return render_template("labeler_dashboard.html",
                           tasks=tasks, done_count=done, total_count=total,
                           in_progress_count=in_progress,
                           username=session["username"])

# ── annotate ──────────────────────────────────────────────────────────────────

@app.route("/annotate/<int:frame_id>")
@login_required
def annotate(frame_id):
    conn = get_db()
    frame = conn.execute("""
        SELECT f.*, v.width, v.height, v.original_name,
               p.keypoint_schema, p.name AS project_name, p.id AS project_id
        FROM frames f
        JOIN videos v ON v.id=f.video_id
        JOIN projects p ON p.id=f.project_id
        WHERE f.id=?
    """, (frame_id,)).fetchone()
    if not frame: abort(404)
    if session.get("role") != "admin" and frame["assigned_to"] != session["user_id"]:
        abort(403)

    existing = conn.execute(
        "SELECT * FROM annotations WHERE frame_id=? ORDER BY id DESC LIMIT 1", (frame_id,)
    ).fetchone()
    prev_f = conn.execute("""
        SELECT id FROM frames WHERE video_id=? AND frame_index<?
        AND (assigned_to=? OR 1=?) ORDER BY frame_index DESC LIMIT 1
    """, (frame["video_id"], frame["frame_index"],
          session["user_id"], 1 if session.get("role")=="admin" else 0)).fetchone()
    next_f = conn.execute("""
        SELECT id FROM frames WHERE video_id=? AND frame_index>?
        AND (assigned_to=? OR 1=?) ORDER BY frame_index ASC LIMIT 1
    """, (frame["video_id"], frame["frame_index"],
          session["user_id"], 1 if session.get("role")=="admin" else 0)).fetchone()
    conn.close()

    return render_template("annotate.html",
                           frame=frame,
                           schema=json.loads(frame["keypoint_schema"]),
                           connections=SKELETON_CONNECTIONS,
                           group_labels=GROUP_LABELS,
                           required_kps=REQUIRED_KEYPOINTS,
                           existing=dict(existing) if existing else None,
                           prev_id=prev_f["id"] if prev_f else None,
                           next_id=next_f["id"] if next_f else None)

# ── annotation api ────────────────────────────────────────────────────────────

@app.route("/api/annotations", methods=["POST"])
@login_required
def save_annotation():
    d = request.get_json()
    frame_id, kps = d["frame_id"], d["keypoints"]
    bbox, notes   = d.get("bbox"), d.get("notes","")
    uid = session["user_id"]
    conn = get_db()

    frame = conn.execute("""
        SELECT f.*, p.keypoint_schema
        FROM frames f JOIN projects p ON p.id=f.project_id
        WHERE f.id=?
    """, (frame_id,)).fetchone()
    if not frame:
        conn.close(); return jsonify({"error": "Frame not found"}), 404
    if session.get("role") != "admin" and frame["assigned_to"] != uid:
        conn.close(); return jsonify({"error": "Forbidden"}), 403

    schema = json.loads(frame["keypoint_schema"])
    missing = []
    for req_name in REQUIRED_KEYPOINTS:
        req_id = next((s["id"] for s in schema if s["name"] == req_name), None)
        if req_id is None:
            continue
        kp = next((k for k in kps if k.get("kp_id") == req_id), None)
        if not kp or kp.get("visible", 0) == 0:
            missing.append(req_name)

    pid = frame["project_id"]
    ex  = conn.execute("SELECT id FROM annotations WHERE frame_id=? AND labeled_by=?", (frame_id, uid)).fetchone()
    payload = (json.dumps(kps), json.dumps(bbox) if bbox else None, notes)
    if ex:
        conn.execute("UPDATE annotations SET keypoints=?,bbox=?,notes=?,updated_at=datetime('now') WHERE id=?",
                     (*payload, ex["id"]))
    else:
        conn.execute("INSERT INTO annotations (frame_id,project_id,labeled_by,keypoints,bbox,notes) VALUES (?,?,?,?,?,?)",
                     (frame_id, pid, uid, *payload))

    new_status = "labeled" if not missing else "in_progress"
    conn.execute("UPDATE frames SET status=?,labeled_by=?,labeled_at=datetime('now') WHERE id=?",
                 (new_status, uid, frame_id))
    conn.commit(); conn.close()
    return jsonify({"status": new_status, "missing": missing})

@app.route("/api/annotations/prev/<int:frame_id>")
@login_required
def get_prev_annotation(frame_id):
    """이전 프레임 어노테이션 — 복사용"""
    conn = get_db()
    cur = conn.execute("SELECT video_id, frame_index FROM frames WHERE id=?", (frame_id,)).fetchone()
    if not cur:
        conn.close(); return jsonify(None)
    prev = conn.execute("""
        SELECT a.keypoints, a.bbox, a.notes
        FROM frames f
        JOIN annotations a ON a.frame_id=f.id
        WHERE f.video_id=? AND f.frame_index < ?
        ORDER BY f.frame_index DESC LIMIT 1
    """, (cur["video_id"], cur["frame_index"])).fetchone()
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
    """어노테이션 시작 시 in_progress로 표시"""
    uid = session["user_id"]
    conn = get_db()
    frame = conn.execute("SELECT assigned_to, status FROM frames WHERE id=?", (frame_id,)).fetchone()
    if not frame:
        conn.close(); return jsonify({"error": "not found"}), 404
    if session.get("role") != "admin" and frame["assigned_to"] != uid:
        conn.close(); return jsonify({"error": "forbidden"}), 403
    if frame["status"] == "unlabeled":
        conn.execute("UPDATE frames SET status='in_progress' WHERE id=?", (frame_id,))
        conn.commit()
    conn.close()
    return jsonify({"status": "in_progress"})

@app.route("/api/annotations/<int:frame_id>")
@login_required
def get_annotation(frame_id):
    conn = get_db()
    ann = conn.execute("SELECT * FROM annotations WHERE frame_id=? ORDER BY id DESC LIMIT 1", (frame_id,)).fetchone()
    conn.close()
    if not ann: return jsonify(None)
    d = dict(ann)
    d["keypoints"] = json.loads(d["keypoints"])
    if d.get("bbox"): d["bbox"] = json.loads(d["bbox"])
    return jsonify(d)

# ── static frame serving ──────────────────────────────────────────────────────

@app.route("/frames/<int:video_id>/<path:filename>")
@login_required
def serve_frame(video_id, filename):
    return send_from_directory(str(FRAMES_DIR / str(video_id)), filename)

# ── socketio ──────────────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect(): emit("connected", {"msg": "ok"})

# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    seed_admin("admin", "admin1234")
    print("\n" + "="*45)
    print("  🎻 BowLabel - Violin Annotation Platform")
    print("  http://localhost:5050")
    print("  Admin: admin / admin1234")
    print("="*45 + "\n")
    socketio.run(app, host="0.0.0.0", port=5050, debug=True, allow_unsafe_werkzeug=True)
