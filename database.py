"""
database.py - BowLabel SQLite schema v5

Master annotation: one `violin_bowing_scene` object per frame.

Core keypoints (9, kpt_shape [9,3]) — chosen for empirical labelability:
  instrument (visible landmarks near the bowing region)
    0 fingerboard_body_g_corner
    1 fingerboard_body_e_corner
    2 tailpiece_upper_center
    3 tailpiece_lower_center
  bow axis (visible stick centerline, resampled from a polyline)
    4 bow_axis_visible_start   (frog/button side of the *visible* axis)
    5 bow_axis_visible_25      (auto, 25% along visible arc-length)
    6 bow_axis_visible_50      (auto, 50%)
    7 bow_axis_visible_75      (auto, 75%)
    8 bow_axis_visible_end     (tip/head side of the *visible* axis)

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
import json
import math
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"
SCHEMA_VERSION = 5

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

    # ── bow axis (visible stick centerline) ─────────────────────────────
    {"id": 4, "name": "bow_axis_visible_start", "color": "#f59e0b", "group": "bow",
     "label": "Bow axis · start", "kind": "bow", "auto": False,
     "desc": "Frog/button-side end of the VISIBLE bow-stick axis."},
    {"id": 5, "name": "bow_axis_visible_25", "color": "#fb923c", "group": "bow",
     "label": "Bow axis · 25%", "kind": "bow", "auto": True,
     "desc": "Auto: 25% along the visible bow-axis arc-length."},
    {"id": 6, "name": "bow_axis_visible_50", "color": "#f97316", "group": "bow",
     "label": "Bow axis · 50%", "kind": "bow", "auto": True,
     "desc": "Auto: 50% along the visible bow-axis arc-length."},
    {"id": 7, "name": "bow_axis_visible_75", "color": "#ea6a2c", "group": "bow",
     "label": "Bow axis · 75%", "kind": "bow", "auto": True,
     "desc": "Auto: 75% along the visible bow-axis arc-length."},
    {"id": 8, "name": "bow_axis_visible_end", "color": "#e94560", "group": "bow",
     "label": "Bow axis · end", "kind": "bow", "auto": False,
     "desc": "Tip/head-side end of the VISIBLE bow-stick axis."},

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
    "bow": "Bow axis (visible stick)",
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

    # v4 -> v5 is a name-only migration: keypoint ids and saved coordinates
    # remain valid, while projects receive the unambiguous bow_axis_* names.
    current_by_id = {k["id"]: k for k in DEFAULT_VIOLIN_SCHEMA}
    for project in conn.execute("SELECT id, keypoint_schema FROM projects").fetchall():
        try:
            schema = json.loads(project["keypoint_schema"])
        except (TypeError, json.JSONDecodeError):
            continue
        changed = False
        for item in schema:
            if (str(item.get("name", "")).startswith("bow_visible_stick_")
                    and item.get("id") in current_by_id):
                item.update(current_by_id[item["id"]])
                changed = True
        if changed:
            conn.execute(
                "UPDATE projects SET keypoint_schema=? WHERE id=?",
                (json.dumps(schema, ensure_ascii=False), project["id"])
            )

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


def generate_bbox(keypoints: list, schema: list, image_width: int,
                  image_height: int, margin_ratio: float = 0.08,
                  min_size: int = 96):
    """
    Deterministically create the violin_bowing_scene interaction-region bbox.

    Sources:
      - core keypoints only (optional landmarks never affect the extent)
      - visible or occluded points that have finite coordinates
    Rule:
      tight axis-aligned box + max(20 px, 8% of longest side) margin,
      then expand to at least 96x96 px and clamp to the image.
    """
    core_ids = {k["id"] for k in schema if not k.get("optional")}
    usable = []
    for kp in keypoints:
        if kp.get("kp_id") not in core_ids:
            continue
        if kp.get("visible") not in (VIS_VISIBLE, VIS_OCCLUDED):
            continue
        x, y = kp.get("x"), kp.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if math.isfinite(x) and math.isfinite(y):
                usable.append((float(x), float(y)))
    if not usable or image_width <= 0 or image_height <= 0:
        return None

    x1 = min(p[0] for p in usable)
    x2 = max(p[0] for p in usable)
    y1 = min(p[1] for p in usable)
    y2 = max(p[1] for p in usable)
    margin = max(20.0, margin_ratio * max(x2 - x1, y2 - y1))
    x1, x2 = x1 - margin, x2 + margin
    y1, y2 = y1 - margin, y2 + margin

    def fit_axis(lo, hi, limit):
        target = min(float(min_size), float(limit))
        if hi - lo < target:
            center = (lo + hi) / 2
            lo, hi = center - target / 2, center + target / 2
        lo, hi = max(0.0, lo), min(float(limit), hi)
        if hi - lo < target:
            if lo <= 0:
                hi = min(float(limit), target)
            else:
                lo = max(0.0, float(limit) - target)
        return lo, hi

    x1, x2 = fit_axis(x1, x2, image_width)
    y1, y2 = fit_axis(y1, y2, image_height)
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def annotation_complete(keypoints: list, bbox: dict, schema: list = None):
    """
    A frame counts as `labeled` when every *core* (non-optional) keypoint is
    addressed and an automatic bbox can be generated.
      - unset                       -> missing
      - visible without coords       -> missing
      - occluded / outside            -> addressed
      - at least one core visible/occluded point needs coordinates for auto bbox
    Optional keypoints are never required.
    Returns (is_complete, missing_ids) where missing_ids are strings.
    """
    schema = schema or DEFAULT_VIOLIN_SCHEMA
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
