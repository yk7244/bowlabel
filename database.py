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


# 활쓰기 피드백용 키포인트 스키마 (8개)
# 관절/손가락은 MediaPipe Pose+Hands로 별도 추론 — 라벨링 부담 최소화
#
# 탐지 목적:
#   bow_*     → 활 각도, 방향(업/다운보우), 속도, sounding point
#   violin_*  → 현 평면, 활 위치(브릿지~지판 비율)
#   string_*  → 4현 위치 (브릿지 기준 활이 어느 현에 닿는지)
DEFAULT_VIOLIN_SCHEMA = [
    {"id": 0, "name": "bow_tip",       "color": "#FF4444", "group": "bow",     "desc": "활 끝 (tip)"},
    {"id": 1, "name": "bow_frog",      "color": "#FFAA00", "group": "bow",     "desc": "개구리 (frog)"},
    {"id": 2, "name": "bow_mid",       "color": "#FF8844", "group": "bow",     "desc": "활 중앙"},
    {"id": 3, "name": "bow_contact",   "color": "#FF6666", "group": "bow",     "desc": "활모-현 접점"},
    {"id": 4, "name": "violin_bridge", "color": "#FFFFFF", "group": "violin",  "desc": "브릿지 중앙"},
    {"id": 5, "name": "violin_nut",    "color": "#CCCCCC", "group": "violin",  "desc": "넛 (scroll쪽)"},
    {"id": 6, "name": "string_g",      "color": "#44FF88", "group": "strings", "desc": "G현 (브릿지)"},
    {"id": 7, "name": "string_e",      "color": "#4488FF", "group": "strings", "desc": "E현 (브릿지)"},
]

# 필수 키포인트 (저장 전 검증)
REQUIRED_KEYPOINTS = ["bow_tip", "bow_frog", "bow_contact", "violin_bridge"]

GROUP_LABELS = {
    "bow": "활 (Bow)",
    "violin": "바이올린",
    "strings": "현 (Strings)",
}

SKELETON_CONNECTIONS = [
    [0, 2], [2, 1],        # bow: tip → mid → frog
    [3, 2],                # contact → mid
    [4, 5],                # bridge → nut (현 평면)
    [6, 4], [4, 7],        # G현 → bridge → E현
    [3, 4],                # contact → bridge (sounding point 참조)
]
