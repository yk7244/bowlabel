"""
database.py - SQLite schema and helpers
"""
import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # Users
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'labeler',  -- 'admin' or 'labeler'
        created_at TEXT DEFAULT (datetime('now')),
        is_active INTEGER DEFAULT 1
    )""")

    # Projects (e.g. "Violin Bow Stroke v1")
    c.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        keypoint_schema TEXT NOT NULL,  -- JSON: [{id, name, color, connections:[]}]
        created_by INTEGER REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now')),
        status TEXT DEFAULT 'active'   -- 'active' | 'archived'
    )""")

    # Videos
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
        status TEXT DEFAULT 'pending'  -- 'pending' | 'extracting' | 'ready' | 'error'
    )""")

    # Frames
    c.execute("""
    CREATE TABLE IF NOT EXISTS frames (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER REFERENCES videos(id),
        project_id INTEGER REFERENCES projects(id),
        filename TEXT NOT NULL,
        frame_index INTEGER NOT NULL,
        timestamp_sec REAL,
        assigned_to INTEGER REFERENCES users(id),
        status TEXT DEFAULT 'unlabeled',  -- 'unlabeled' | 'in_progress' | 'labeled' | 'reviewed'
        labeled_by INTEGER REFERENCES users(id),
        labeled_at TEXT,
        reviewed_by INTEGER REFERENCES users(id),
        reviewed_at TEXT
    )""")

    # Annotations
    c.execute("""
    CREATE TABLE IF NOT EXISTS annotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        frame_id INTEGER REFERENCES frames(id),
        project_id INTEGER REFERENCES projects(id),
        labeled_by INTEGER REFERENCES users(id),
        keypoints TEXT NOT NULL,   -- JSON: [{kp_id, x, y, visible}]
        bbox TEXT,                 -- JSON: {x, y, w, h} optional
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )""")

    conn.commit()
    conn.close()
    print("[DB] Initialized")


def seed_admin(username="admin", password="admin1234"):
    """초기 어드민 계정 생성"""
    from werkzeug.security import generate_password_hash
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (username, generate_password_hash(password, method="pbkdf2:sha256"))
        )
        conn.commit()
        print(f"[DB] Admin created: {username} / {password}")
    finally:
        conn.close()


# Default violin bow keypoint schema
DEFAULT_VIOLIN_SCHEMA = [
    {"id": 0,  "name": "bow_tip",        "color": "#FF4444", "group": "bow"},
    {"id": 1,  "name": "bow_mid_upper",  "color": "#FF8844", "group": "bow"},
    {"id": 2,  "name": "bow_mid",        "color": "#FFAA00", "group": "bow"},
    {"id": 3,  "name": "bow_mid_lower",  "color": "#FFCC44", "group": "bow"},
    {"id": 4,  "name": "bow_frog",       "color": "#FFFF44", "group": "bow"},
    {"id": 5,  "name": "r_thumb",        "color": "#44FF44", "group": "right_hand"},
    {"id": 6,  "name": "r_index",        "color": "#44FFAA", "group": "right_hand"},
    {"id": 7,  "name": "r_middle",       "color": "#44FFFF", "group": "right_hand"},
    {"id": 8,  "name": "r_ring",         "color": "#4488FF", "group": "right_hand"},
    {"id": 9,  "name": "r_pinky",        "color": "#8844FF", "group": "right_hand"},
    {"id": 10, "name": "r_wrist",        "color": "#CC44FF", "group": "right_arm"},
    {"id": 11, "name": "r_elbow",        "color": "#FF44CC", "group": "right_arm"},
    {"id": 12, "name": "r_shoulder",     "color": "#FF4488", "group": "right_arm"},
    {"id": 13, "name": "l_wrist",        "color": "#AAFFAA", "group": "left_arm"},
    {"id": 14, "name": "l_elbow",        "color": "#AAFFFF", "group": "left_arm"},
    {"id": 15, "name": "l_shoulder",     "color": "#AAAAFF", "group": "left_arm"},
    {"id": 16, "name": "violin_scroll",  "color": "#FFFFFF", "group": "violin"},
    {"id": 17, "name": "violin_bridge",  "color": "#CCCCCC", "group": "violin"},
    {"id": 18, "name": "chin_rest",      "color": "#999999", "group": "violin"},
]

SKELETON_CONNECTIONS = [
    [0, 1], [1, 2], [2, 3], [3, 4],   # bow
    [5, 6], [6, 7], [7, 8], [8, 9],   # right hand fingers
    [4, 5], [4, 6], [4, 7], [4, 8], [4, 9],  # frog to fingers
    [5, 10], [10, 11], [11, 12],       # right arm
    [13, 14], [14, 15],                # left arm
    [16, 17],                          # violin
]
