"""
database.py - BowLabel SQLite schema v3
Master annotation: violin_bowing_scene + 9 keypoints per frame
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"
SCHEMA_VERSION = 3

CLASS_NAME = "violin_bowing_scene"
DEFAULT_PILOT_COUNT = 20

# visibility: 0=미처리  1=occluded  2=visible  3=outside
VIS_UNSET    = 0
VIS_OCCLUDED = 1
VIS_VISIBLE  = 2
VIS_OUTSIDE  = 3

DEFAULT_VIOLIN_SCHEMA = [
    {"id": 0, "name": "bridge_g_foot",             "color": "#44FF88", "group": "bridge",
     "label_short": "브릿지·G", "desc": "G현 측 브릿지 발 중심"},
    {"id": 1, "name": "bridge_e_foot",             "color": "#4488FF", "group": "bridge",
     "label_short": "브릿지·E", "desc": "E현 측 브릿지 발 중심"},
    {"id": 2, "name": "fingerboard_nut_g_corner",  "color": "#88FFAA", "group": "fingerboard",
     "label_short": "넛·G", "desc": "nut 쪽 지판 G 모서리"},
    {"id": 3, "name": "fingerboard_nut_e_corner",  "color": "#88AAFF", "group": "fingerboard",
     "label_short": "넛·E", "desc": "nut 쪽 지판 E 모서리"},
    {"id": 4, "name": "fingerboard_body_g_corner", "color": "#66DD88", "group": "fingerboard",
     "label_short": "바디·G", "desc": "body 쪽 지판 G 끝"},
    {"id": 5, "name": "fingerboard_body_e_corner", "color": "#6688DD", "group": "fingerboard",
     "label_short": "바디·E", "desc": "body 쪽 지판 E 끝"},
    {"id": 6, "name": "bow_frog_endpoint",         "color": "#FFAA00", "group": "bow",
     "label_short": "활·frog", "desc": "frog 쪽 활 끝점"},
    {"id": 7, "name": "bow_tip_endpoint",          "color": "#FF4444", "group": "bow",
     "label_short": "활·tip", "desc": "tip 쪽 활 끝점"},
    {"id": 8, "name": "bow_midpoint_visible",      "color": "#FF8844", "group": "bow",
     "label_short": "활·중점", "desc": "보이는 stick/hair 중심 중점"},
]

GROUP_LABELS = {
    "bridge": "브릿지",
    "fingerboard": "지판",
    "bow": "활",
}

SKELETON_CONNECTIONS = [
    [0, 1],
    [2, 3], [4, 5], [2, 4], [3, 5],
    [6, 8], [8, 7], [6, 7],
]

QUALITY_OPTIONS = [
    ("high",   "높음 — 9점 대부분 명확"),
    ("medium", "보통 — 일부 가림, visibility 처리됨"),
    ("low",    "낮음 — 흐림/작음, 학습 제외 권장"),
]


def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def reset_db():
    for p in (DB_PATH, DB_PATH.with_suffix(".db-shm"), DB_PATH.with_suffix(".db-wal")):
        if p.exists():
            p.unlink()
    init_db()
    print("[DB] Reset complete")


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'labeler',
        created_at TEXT DEFAULT (datetime('now')),
        is_active INTEGER DEFAULT 1
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        class_name TEXT NOT NULL DEFAULT 'violin_bowing_scene',
        keypoint_schema TEXT NOT NULL,
        pilot_count INTEGER DEFAULT 20,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now')),
        status TEXT DEFAULT 'active'
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER REFERENCES projects(id),
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        player_id TEXT,
        session_label TEXT,
        notes TEXT,
        fps REAL,
        total_frames INTEGER,
        width INTEGER,
        height INTEGER,
        duration_sec REAL,
        uploaded_by INTEGER REFERENCES users(id),
        uploaded_at TEXT DEFAULT (datetime('now')),
        status TEXT DEFAULT 'pending'
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS frames (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER REFERENCES videos(id),
        project_id INTEGER REFERENCES projects(id),
        filename TEXT NOT NULL,
        frame_index INTEGER NOT NULL,
        timestamp_sec REAL,
        batch_type TEXT DEFAULT 'main',
        UNIQUE(video_id, frame_index)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS frame_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        frame_id INTEGER NOT NULL REFERENCES frames(id),
        user_id INTEGER NOT NULL REFERENCES users(id),
        status TEXT DEFAULT 'unlabeled',
        labeled_at TEXT,
        reviewed_by INTEGER REFERENCES users(id),
        reviewed_at TEXT,
        UNIQUE(frame_id, user_id)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS annotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        frame_id INTEGER REFERENCES frames(id),
        project_id INTEGER REFERENCES projects(id),
        labeled_by INTEGER REFERENCES users(id),
        keypoints TEXT NOT NULL,
        bbox TEXT,
        quality TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(frame_id, labeled_by)
    )""")

    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
              (str(SCHEMA_VERSION),))
    conn.commit()
    conn.close()
    print(f"[DB] Initialized v{SCHEMA_VERSION}")


def seed_admin(username="admin", password="admin1234"):
    from werkzeug.security import generate_password_hash
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (username, generate_password_hash(password, method="pbkdf2:sha256"))
        )
        conn.commit()
        print(f"[DB] Admin: {username} / {password}")
    finally:
        conn.close()


def coco_visibility(v: int) -> int:
    """BowLabel → COCO/YOLO visibility"""
    if v == VIS_VISIBLE:
        return 2
    if v == VIS_OCCLUDED:
        return 1
    return 0


def annotation_complete(keypoints: list, bbox: dict, n_kp: int = 9):
    """All 9 kps addressed (visible needs coords; occluded/outside ok without). bbox required."""
    missing = []
    if len(keypoints) < n_kp:
        missing.append("__keypoints__")
    for kp in keypoints:
        vis = kp.get("visible", VIS_UNSET)
        kp_id = str(kp.get("kp_id", "?"))
        if vis == VIS_UNSET:
            missing.append(kp_id)
        elif vis == VIS_VISIBLE:
            x, y = kp.get("x"), kp.get("y")
            if x is None or y is None:
                missing.append(kp_id)
    if not bbox or not bbox.get("w") or not bbox.get("h"):
        missing.append("__bbox__")
    return len(missing) == 0, missing
