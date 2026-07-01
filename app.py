"""
app.py - Main Flask application
Run: python3 app.py
"""
import json
import os
import uuid
from pathlib import Path
from functools import wraps
from datetime import datetime

from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, send_from_directory, send_file, abort)
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from database import get_db, init_db, seed_admin, DEFAULT_VIOLIN_SCHEMA, SKELETON_CONNECTIONS
from extractor import allowed_video, get_video_info, extract_frames_async, UPLOADS_DIR, FRAMES_DIR
from exporter import export_coco, export_yolo_pose, export_csv, EXPORTS_DIR

BASE_DIR = Path(__file__).parent

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "violin-bow-secret-2025-change-me")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4GB

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


# ────────────────────────── Auth helpers ──────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated


def current_user():
    if "user_id" not in session:
        return None
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    return u


# ────────────────────────── Auth routes ──────────────────────────

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("labeler_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND is_active=1", (username,)
        ).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("index"))
        error = "아이디 또는 비밀번호가 올바르지 않습니다."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ────────────────────────── Admin: Dashboard ──────────────────────────

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    conn = get_db()
    projects = conn.execute("""
        SELECT p.*, COUNT(DISTINCT v.id) as video_count,
               COUNT(DISTINCT f.id) as frame_count,
               SUM(CASE WHEN f.status='labeled' OR f.status='reviewed' THEN 1 ELSE 0 END) as labeled_count
        FROM projects p
        LEFT JOIN videos v ON v.project_id=p.id
        LEFT JOIN frames f ON f.project_id=p.id
        WHERE p.status='active'
        GROUP BY p.id
        ORDER BY p.id DESC
    """).fetchall()

    users = conn.execute("""
        SELECT u.*, COUNT(DISTINCT a.frame_id) as labeled_count
        FROM users u
        LEFT JOIN annotations a ON a.labeled_by=u.id
        GROUP BY u.id ORDER BY u.id
    """).fetchall()
    conn.close()
    return render_template("admin_dashboard.html",
                           projects=projects, users=users,
                           current_user=dict(session))


# ────────────────────────── Admin: User management ──────────────────────────

@app.route("/admin/users/add", methods=["POST"])
@login_required
@admin_required
def add_user():
    username = request.form["username"].strip()
    password = request.form["password"]
    role     = request.form.get("role", "labeler")
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            (username, generate_password_hash(password, method="pbkdf2:sha256"), role)
        )
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
    conn.execute("UPDATE users SET is_active = 1 - is_active WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:uid>/reset_password", methods=["POST"])
@login_required
@admin_required
def reset_password(uid):
    new_pw = request.form["new_password"]
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(new_pw, method="pbkdf2:sha256"), uid))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_dashboard"))


# ────────────────────────── Admin: Projects ──────────────────────────

@app.route("/admin/projects/create", methods=["GET", "POST"])
@login_required
@admin_required
def create_project():
    if request.method == "POST":
        name   = request.form["name"].strip()
        desc   = request.form.get("description", "")
        schema_str = request.form.get("keypoint_schema", "")
        try:
            schema = json.loads(schema_str) if schema_str else DEFAULT_VIOLIN_SCHEMA
        except Exception:
            schema = DEFAULT_VIOLIN_SCHEMA
        conn = get_db()
        conn.execute(
            "INSERT INTO projects (name, description, keypoint_schema, created_by) VALUES (?,?,?,?)",
            (name, desc, json.dumps(schema, ensure_ascii=False), session["user_id"])
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin_dashboard"))
    return render_template("create_project.html",
                           default_schema=json.dumps(DEFAULT_VIOLIN_SCHEMA, indent=2, ensure_ascii=False))


@app.route("/admin/projects/<int:pid>")
@login_required
@admin_required
def project_detail(pid):
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj:
        abort(404)
    videos = conn.execute("""
        SELECT v.*, COUNT(f.id) as frame_count,
               SUM(CASE WHEN f.status IN ('labeled','reviewed') THEN 1 ELSE 0 END) as done_count
        FROM videos v
        LEFT JOIN frames f ON f.video_id = v.id
        WHERE v.project_id=?
        GROUP BY v.id ORDER BY v.id DESC
    """, (pid,)).fetchall()
    users = conn.execute("SELECT * FROM users WHERE role='labeler' AND is_active=1").fetchall()
    conn.close()
    return render_template("project_detail.html", proj=proj, videos=videos, users=users)


# ────────────────────────── Admin: Video upload & extract ──────────────────────────

@app.route("/admin/projects/<int:pid>/upload", methods=["POST"])
@login_required
@admin_required
def upload_video(pid):
    f = request.files.get("video")
    if not f or not allowed_video(f.filename):
        return jsonify({"error": "Invalid file"}), 400

    player_id     = request.form.get("player_id", "unknown")
    session_label = request.form.get("session_label", "")
    notes         = request.form.get("notes", "")

    ext = Path(f.filename).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    save_path = UPLOADS_DIR / stored_name
    f.save(str(save_path))

    info = get_video_info(str(save_path))

    conn = get_db()
    cur = conn.execute("""
        INSERT INTO videos (project_id, filename, original_name, player_id,
                            session_label, notes, fps, total_frames,
                            width, height, duration_sec, uploaded_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (pid, stored_name, secure_filename(f.filename), player_id,
          session_label, notes,
          info.get("fps"), info.get("total_frames"),
          info.get("width"), info.get("height"),
          info.get("duration_sec"), session["user_id"]))
    video_id = cur.lastrowid
    conn.commit()
    conn.close()

    return jsonify({"video_id": video_id, "info": info})


@app.route("/admin/videos/<int:vid>/extract", methods=["POST"])
@login_required
@admin_required
def trigger_extract(vid):
    fps_target = float(request.form.get("fps_target", 2.0))
    start_sec  = float(request.form.get("start_sec", 0))
    end_sec    = float(request.form.get("end_sec", 0))
    extract_frames_async(vid, fps_target, start_sec, end_sec, socketio)
    return jsonify({"status": "extracting", "video_id": vid})


@app.route("/admin/videos/<int:vid>/status")
@login_required
def video_status(vid):
    conn = get_db()
    row = conn.execute("""
        SELECT v.status, COUNT(f.id) as frame_count,
               SUM(CASE WHEN f.status IN ('labeled','reviewed') THEN 1 ELSE 0 END) as done_count
        FROM videos v LEFT JOIN frames f ON f.video_id=v.id
        WHERE v.id=? GROUP BY v.id
    """, (vid,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    return jsonify(dict(row))


# ────────────────────────── Admin: Frame assignment ──────────────────────────

@app.route("/admin/projects/<int:pid>/assign", methods=["POST"])
@login_required
@admin_required
def assign_frames(pid):
    """라벨러들에게 프레임 자동 균등 배분"""
    data = request.get_json()
    user_ids = data.get("user_ids", [])
    if not user_ids:
        return jsonify({"error": "No users selected"}), 400

    conn = get_db()
    frames = conn.execute("""
        SELECT id FROM frames WHERE project_id=? AND status='unlabeled'
        ORDER BY id
    """, (pid,)).fetchall()

    for i, frame in enumerate(frames):
        uid = user_ids[i % len(user_ids)]
        conn.execute("UPDATE frames SET assigned_to=? WHERE id=?",
                     (uid, frame["id"]))

    conn.commit()
    conn.close()
    return jsonify({"assigned": len(frames), "to": len(user_ids), "users": user_ids})


# ────────────────────────── Admin: Export ──────────────────────────

@app.route("/admin/projects/<int:pid>/export/<fmt>")
@login_required
@admin_required
def export_project(pid, fmt):
    if fmt == "coco":
        path = export_coco(pid)
        return send_file(path, as_attachment=True)
    elif fmt == "yolo":
        path = export_yolo_pose(pid)
        return send_file(path, as_attachment=True)
    elif fmt == "csv":
        path = export_csv(pid)
        return send_file(path, as_attachment=True)
    abort(400)


# ────────────────────────── Admin: Stats API ──────────────────────────

@app.route("/api/projects/<int:pid>/stats")
@login_required
def project_stats(pid):
    conn = get_db()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status='unlabeled' THEN 1 ELSE 0 END) as unlabeled,
            SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) as in_progress,
            SUM(CASE WHEN status='labeled' THEN 1 ELSE 0 END) as labeled,
            SUM(CASE WHEN status='reviewed' THEN 1 ELSE 0 END) as reviewed
        FROM frames WHERE project_id=?
    """, (pid,)).fetchone()

    per_user = conn.execute("""
        SELECT u.username, COUNT(a.id) as count
        FROM annotations a JOIN users u ON u.id=a.labeled_by
        JOIN frames f ON f.id=a.frame_id
        WHERE f.project_id=?
        GROUP BY a.labeled_by ORDER BY count DESC
    """, (pid,)).fetchall()

    conn.close()
    return jsonify({
        "frames": dict(stats),
        "per_user": [dict(r) for r in per_user]
    })


# ────────────────────────── Labeler: Dashboard ──────────────────────────

@app.route("/labeler")
@login_required
def labeler_dashboard():
    conn = get_db()
    uid = session["user_id"]

    tasks = conn.execute("""
        SELECT f.id, f.filename, f.status, f.frame_index, f.timestamp_sec,
               v.original_name, v.id as video_id, p.name as project_name, p.id as project_id
        FROM frames f
        JOIN videos v ON v.id = f.video_id
        JOIN projects p ON p.id = f.project_id
        WHERE f.assigned_to=? AND f.status IN ('unlabeled','in_progress')
        ORDER BY f.id LIMIT 200
    """, (uid,)).fetchall()

    done = conn.execute("""
        SELECT COUNT(*) as cnt FROM frames
        WHERE assigned_to=? AND status IN ('labeled','reviewed')
    """, (uid,)).fetchone()["cnt"]

    conn.close()
    return render_template("labeler_dashboard.html",
                           tasks=tasks, done_count=done,
                           username=session["username"])


# ────────────────────────── Annotation UI ──────────────────────────

@app.route("/annotate/<int:frame_id>")
@login_required
def annotate(frame_id):
    conn = get_db()
    frame = conn.execute("""
        SELECT f.*, v.width, v.height, v.original_name,
               p.keypoint_schema, p.name as project_name
        FROM frames f
        JOIN videos v ON v.id=f.video_id
        JOIN projects p ON p.id=f.project_id
        WHERE f.id=?
    """, (frame_id,)).fetchone()

    if not frame:
        abort(404)

    # 권한 확인
    if session.get("role") != "admin" and frame["assigned_to"] != session["user_id"]:
        abort(403)

    # 기존 어노테이션
    existing = conn.execute(
        "SELECT * FROM annotations WHERE frame_id=? ORDER BY id DESC LIMIT 1",
        (frame_id,)
    ).fetchone()

    # 인접 프레임
    prev_frame = conn.execute("""
        SELECT id FROM frames
        WHERE video_id=? AND frame_index<? AND (assigned_to=? OR ?)
        ORDER BY frame_index DESC LIMIT 1
    """, (frame["video_id"], frame["frame_index"],
          session["user_id"], session.get("role") == "admin")).fetchone()

    next_frame = conn.execute("""
        SELECT id FROM frames
        WHERE video_id=? AND frame_index>? AND (assigned_to=? OR ?)
        ORDER BY frame_index ASC LIMIT 1
    """, (frame["video_id"], frame["frame_index"],
          session["user_id"], session.get("role") == "admin")).fetchone()

    schema = json.loads(frame["keypoint_schema"])
    connections = SKELETON_CONNECTIONS

    conn.close()
    return render_template("annotate.html",
                           frame=frame,
                           schema=schema,
                           connections=connections,
                           existing=dict(existing) if existing else None,
                           prev_id=prev_frame["id"] if prev_frame else None,
                           next_id=next_frame["id"] if next_frame else None)


# ────────────────────────── Annotation API ──────────────────────────

@app.route("/api/annotations", methods=["POST"])
@login_required
def save_annotation():
    data = request.get_json()
    frame_id  = data["frame_id"]
    keypoints = data["keypoints"]  # [{kp_id, x, y, visible}]
    bbox      = data.get("bbox")   # {x, y, w, h}
    notes     = data.get("notes", "")

    conn = get_db()

    # 기존 annotation 있으면 UPDATE
    existing = conn.execute(
        "SELECT id FROM annotations WHERE frame_id=? AND labeled_by=?",
        (frame_id, session["user_id"])
    ).fetchone()

    proj_id = conn.execute("SELECT project_id FROM frames WHERE id=?", (frame_id,)).fetchone()["project_id"]

    if existing:
        conn.execute("""
            UPDATE annotations SET keypoints=?, bbox=?, notes=?, updated_at=datetime('now')
            WHERE id=?
        """, (json.dumps(keypoints), json.dumps(bbox) if bbox else None, notes, existing["id"]))
    else:
        conn.execute("""
            INSERT INTO annotations (frame_id, project_id, labeled_by, keypoints, bbox, notes)
            VALUES (?,?,?,?,?,?)
        """, (frame_id, proj_id, session["user_id"],
              json.dumps(keypoints), json.dumps(bbox) if bbox else None, notes))

    conn.execute("""
        UPDATE frames SET status='labeled', labeled_by=?, labeled_at=datetime('now')
        WHERE id=?
    """, (session["user_id"], frame_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "saved"})


@app.route("/api/annotations/<int:frame_id>")
@login_required
def get_annotation(frame_id):
    conn = get_db()
    ann = conn.execute(
        "SELECT * FROM annotations WHERE frame_id=? ORDER BY id DESC LIMIT 1",
        (frame_id,)
    ).fetchone()
    conn.close()
    if not ann:
        return jsonify(None)
    d = dict(ann)
    d["keypoints"] = json.loads(d["keypoints"])
    if d.get("bbox"):
        d["bbox"] = json.loads(d["bbox"])
    return jsonify(d)


# ────────────────────────── Static frame serving ──────────────────────────

@app.route("/frames/<int:video_id>/<path:filename>")
@login_required
def serve_frame(video_id, filename):
    frame_dir = FRAMES_DIR / str(video_id)
    return send_from_directory(str(frame_dir), filename)


# ────────────────────────── SocketIO ──────────────────────────

@socketio.on("connect")
def on_connect():
    emit("connected", {"msg": "connected"})


# ────────────────────────── Main ──────────────────────────

if __name__ == "__main__":
    init_db()
    seed_admin("admin", "admin1234")
    print("\n==============================")
    print("  BowLabel - Violin Annotation")
    print("  http://localhost:5050")
    print("  Admin: admin / admin1234")
    print("==============================\n")
    socketio.run(app, host="0.0.0.0", port=5050, debug=True, allow_unsafe_werkzeug=True)
