"""
database.py - BowLabel SQLite schema v4

Master annotation: one `violin_bowing_scene` object per frame.

Core keypoints (9, kpt_shape [9,3]) — chosen for empirical labelability:
  instrument (visible landmarks near the bowing region)
    0 fingerboard_body_g_corner
    1 fingerboard_body_e_corner
    2 tailpiece_upper_center
    3 tailpiece_lower_center
  bow (visible stick centerline, resampled from a polyline)
    4 bow_visible_stick_start   (frog/button side of the *visible* stick)
    5 bow_visible_stick_25      (auto, 25% along visible arc-length)
    6 bow_visible_stick_50      (auto, 50%)
    7 bow_visible_stick_75      (auto, 75%)
    8 bow_visible_stick_end     (tip/head side of the *visible* stick)

Optional keypoints (labeled only when clearly visible; used for subset
validation, never required for completion, excluded from YOLO kpt_shape):
    9  bridge_g_foot_center
    10 bridge_e_foot_center
    11 bow_string_contact_center

Rationale (see CHANGELOG / README):
  - nut & scroll dropped: frequently occluded by the left hand / out of frame.
  - bridge feet & string contact are not consistently visible -> optional only.
  - the bow is cambered, so we never fit a single global tip-frog line; we label
    the *visible* stick centerline and resample it, then compute a local tangent.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"
SCHEMA_VERSION = 4

CLASS_NAME = "violin_bowing_scene"
DEFAULT_PILOT_COUNT = 20

# visibility (BowLabel internal)
VIS_UNSET    = 0  # not addressed yet
VIS_OCCLUDED = 1  # in frame but hidden behind hand/instrument (localizable)
VIS_VISIBLE  = 2  # clearly visible, clicked
VIS_OUTSIDE  = 3  # outside frame / cannot localize

# how many resampled points represent the visible bow stick
BOW_SAMPLES = 5

DEFAULT_VIOLIN_SCHEMA = [
    # ── instrument anchors ─────────────────────────────────────────────
    {"id": 0, "name": "fingerboard_body_g_corner", "color": "#34d399", "group": "instrument",
     "label": "Fingerboard body · G", "kind": "point",
     "desc": "Body-side end of the fingerboard, G-string corner."},
    {"id": 1, "name": "fingerboard_body_e_corner", "color": "#22c1a6", "group": "instrument",
     "label": "Fingerboard body · E", "kind": "point",
     "desc": "Body-side end of the fingerboard, E-string corner."},
    {"id": 2, "name": "tailpiece_upper_center", "color": "#38bdf8", "group": "instrument",
     "label": "Tailpiece · upper", "kind": "point",
     "desc": "Center of the tailpiece top edge (bridge side)."},
    {"id": 3, "name": "tailpiece_lower_center", "color": "#3b82f6", "group": "instrument",
     "label": "Tailpiece · lower", "kind": "point",
     "desc": "Center of the tailpiece bottom (endpin/chinrest side)."},

    # ── bow (visible stick centerline) ─────────────────────────────────
    {"id": 4, "name": "bow_visible_stick_start", "color": "#f59e0b", "group": "bow",
     "label": "Bow visible · start", "kind": "bow", "auto": False,
     "desc": "Frog/button side end of the VISIBLE stick centerline."},
    {"id": 5, "name": "bow_visible_stick_25", "color": "#fb923c", "group": "bow",
     "label": "Bow visible · 25%", "kind": "bow", "auto": True,
     "desc": "Auto: 25% along the visible stick arc-length."},
    {"id": 6, "name": "bow_visible_stick_50", "color": "#f97316", "group": "bow",
     "label": "Bow visible · 50%", "kind": "bow", "auto": True,
     "desc": "Auto: 50% along the visible stick arc-length."},
    {"id": 7, "name": "bow_visible_stick_75", "color": "#ea6a2c", "group": "bow",
     "label": "Bow visible · 75%", "kind": "bow", "auto": True,
     "desc": "Auto: 75% along the visible stick arc-length."},
    {"id": 8, "name": "bow_visible_stick_end", "color": "#e94560", "group": "bow",
     "label": "Bow visible · end", "kind": "bow", "auto": False,
     "desc": "Tip/head side end of the VISIBLE stick centerline."},

    # ── optional (validation only) ─────────────────────────────────────
    {"id": 9, "name": "bridge_g_foot_center", "color": "#a78bfa", "group": "optional",
     "label": "Bridge foot · G (opt)", "kind": "point", "optional": True,
     "desc": "OPTIONAL: G-side bridge foot center — only if clearly visible."},
    {"id": 10, "name": "bridge_e_foot_center", "color": "#8b5cf6", "group": "optional",
     "label": "Bridge foot · E (opt)", "kind": "point", "optional": True,
     "desc": "OPTIONAL: E-side bridge foot center — only if clearly visible."},
    {"id": 11, "name": "bow_string_contact_center", "color": "#f472b6", "group": "optional",
     "label": "Bow–string contact (opt)", "kind": "point", "optional": True,
     "desc": "OPTIONAL: where the bow hair crosses the sounding string."},
]

# ids that must not be required for completion / excluded from YOLO kpt_shape
OPTIONAL_IDS = [k["id"] for k in DEFAULT_VIOLIN_SCHEMA if k.get("optional")]
CORE_IDS     = [k["id"] for k in DEFAULT_VIOLIN_SCHEMA if not k.get("optional")]
BOW_IDS      = [k["id"] for k in DEFAULT_VIOLIN_SCHEMA if k["group"] == "bow"]

GROUP_LABELS = {
    "instrument": "Instrument",
    "bow": "Bow (visible stick)",
    "optional": "Optional",
}

# drawn only for visualisation
SKELETON_CONNECTIONS = [
    [0, 1],            # fingerboard width
    [2, 3],            # tailpiece axis
    [4, 5], [5, 6], [6, 7], [7, 8],   # bow stick chain
]

QUALITY_OPTIONS = [
    ("high",   "High — instrument anchors + bow clearly labeled"),
    ("medium", "Medium — some occlusion, visibility flags used"),
    ("low",    "Low — bow too short/blurred, exclude from training"),
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


def _ensure_column(conn, table, column, decl):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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
        meta TEXT,
        quality TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(frame_id, labeled_by)
    )""")

    # migrations for DBs created before v4
    _ensure_column(conn, "annotations", "meta", "TEXT")

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
    """BowLabel visibility -> COCO/YOLO visibility flag."""
    if v == VIS_VISIBLE:
        return 2
    if v == VIS_OCCLUDED:
        return 1
    return 0  # unset / outside


def annotation_complete(keypoints: list, bbox: dict, schema: list = None):
    """
    A frame counts as `labeled` when every *core* (non-optional) keypoint is
    addressed and a valid bbox exists.
      - unset                       -> missing
      - visible without coords       -> missing
      - occluded (coords) / outside  -> ok
    Optional keypoints are never required.
    Returns (is_complete, missing_ids) where missing_ids are strings.
    """
    schema = schema or DEFAULT_VIOLIN_SCHEMA
    optional_ids = {k["id"] for k in schema if k.get("optional")}
    core_ids = {k["id"] for k in schema if not k.get("optional")}

    by_id = {k.get("kp_id"): k for k in keypoints}
    missing = []
    for kid in sorted(core_ids):
        kp = by_id.get(kid)
        if not kp:
            missing.append(str(kid))
            continue
        vis = kp.get("visible", VIS_UNSET)
        if vis == VIS_UNSET:
            missing.append(str(kid))
        elif vis == VIS_VISIBLE and (kp.get("x") is None or kp.get("y") is None):
            missing.append(str(kid))

    if not bbox or not bbox.get("w") or not bbox.get("h"):
        missing.append("__bbox__")
    return len(missing) == 0, missing
