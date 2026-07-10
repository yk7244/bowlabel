"""
exporter.py - COCO / YOLO Pose / CSV / Agreement export
"""
import json
import csv
import zipfile
import shutil
from pathlib import Path
from database import (get_db, SKELETON_CONNECTIONS, CLASS_NAME,
                      coco_visibility, generate_bbox)


def _split_schema(schema):
    """Return (core_defs, optional_ids) preserving id order."""
    core = [k for k in schema if not k.get("optional")]
    optional_ids = {k["id"] for k in schema if k.get("optional")}
    return core, optional_ids


def _kp_lookup(kps_raw):
    return {k.get("kp_id"): k for k in kps_raw}

BASE_DIR = Path(__file__).parent
FRAMES_DIR = BASE_DIR / "frames"
EXPORTS_DIR = BASE_DIR / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)


def _query_rows(conn, project_id, main_only=False):
    where = ["f.project_id=?", "fa.status IN ('labeled','reviewed')"]
    params = [project_id]
    if main_only:
        where.append("f.batch_type='main'")
    sql = f"""
        SELECT f.*, a.keypoints, a.bbox, a.quality, a.labeled_by, a.notes,
               v.width, v.height, u.username,
               p.class_name, p.keypoint_schema, f.batch_type
        FROM frames f
        JOIN frame_assignments fa ON fa.frame_id=f.id AND fa.user_id=a.labeled_by
        JOIN annotations a ON a.frame_id=f.id
        JOIN users u ON u.id=a.labeled_by
        JOIN videos v ON v.id=f.video_id
        JOIN projects p ON p.id=f.project_id
        WHERE {" AND ".join(where)}
        ORDER BY f.video_id, f.frame_index
    """
    return conn.execute(sql, params).fetchall()


def export_coco(project_id: int, main_only: bool = True) -> str:
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    schema = json.loads(proj["keypoint_schema"])
    core, _ = _split_schema(schema)
    kp_names = [k["name"] for k in core]
    class_name = proj["class_name"] or CLASS_NAME

    rows = _query_rows(conn, project_id, main_only=main_only)
    images, annotations_list = [], []

    for i, row in enumerate(rows):
        W, H = row["width"] or 1920, row["height"] or 1080
        img_id = f"{row['id']}_{row['labeled_by']}"
        images.append({
            "id": img_id,
            "file_name": f"{row['video_id']}/{row['filename']}",
            "width": W, "height": H,
            "batch_type": row["batch_type"],
            "labeled_by": row["username"],
        })
        kps_raw = json.loads(row["keypoints"])
        lut = _kp_lookup(kps_raw)
        coco_kps = []
        num_vis = 0
        for kdef in core:
            kp = lut.get(kdef["id"], {})
            v = coco_visibility(kp.get("visible", 0))
            coco_kps += [kp.get("x") or 0, kp.get("y") or 0, v]
            if v > 0:
                num_vis += 1
        bbox = generate_bbox(kps_raw, schema, W, H) or {"x": 0, "y": 0, "w": 0, "h": 0}
        annotations_list.append({
            "id": i,
            "image_id": img_id,
            "category_id": 1,
            "keypoints": coco_kps,
            "num_keypoints": num_vis,
            "bbox": [bbox["x"], bbox["y"], bbox["w"], bbox["h"]],
            "area": bbox["w"] * bbox["h"],
            "iscrowd": 0,
            "quality": row["quality"],
        })

    coco = {
        "info": {"description": proj["name"], "version": "5.0", "class": class_name},
        "categories": [{
            "id": 1, "name": class_name,
            "keypoints": kp_names,
            "skeleton": SKELETON_CONNECTIONS,
        }],
        "images": images,
        "annotations": annotations_list,
    }
    conn.close()
    out_path = EXPORTS_DIR / f"project_{project_id}_coco.json"
    with open(out_path, "w") as f:
        json.dump(coco, f, indent=2)
    return str(out_path)


def export_yolo_pose(project_id: int, main_only: bool = True) -> str:
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    schema = json.loads(proj["keypoint_schema"])
    core, _ = _split_schema(schema)
    n_kp = len(core)
    class_name = proj["class_name"] or CLASS_NAME

    rows = _query_rows(conn, project_id, main_only=main_only)
    out_dir = EXPORTS_DIR / f"project_{project_id}_yolo"
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    (out_dir / "labels").mkdir(parents=True, exist_ok=True)

    for row in rows:
        W, H = row["width"] or 1920, row["height"] or 1080
        kps_raw = json.loads(row["keypoints"])
        lut = _kp_lookup(kps_raw)
        bbox = generate_bbox(kps_raw, schema, W, H)
        if not bbox:
            continue
        cx = (bbox["x"] + bbox["w"] / 2) / W
        cy = (bbox["y"] + bbox["h"] / 2) / H
        bw = bbox["w"] / W
        bh = bbox["h"] / H
        kp_str = ""
        for kdef in core:
            kp = lut.get(kdef["id"], {})
            v = coco_visibility(kp.get("visible", 0))
            kp_str += f" {(kp.get('x') or 0)/W:.6f} {(kp.get('y') or 0)/H:.6f} {v}"
        base = f"{row['video_id']}_{row['filename'].replace('.jpg','')}"
        with open(out_dir / "labels" / f"{base}.txt", "w") as f:
            f.write(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}{kp_str}\n")
        src = FRAMES_DIR / str(row["video_id"]) / row["filename"]
        dst = out_dir / "images" / f"{base}.jpg"
        if src.exists():
            shutil.copy2(src, dst)

    yaml = f"""path: {out_dir}
train: images
val: images

kpt_shape: [{n_kp}, 3]

names:
  0: {class_name}
"""
    with open(out_dir / "data.yaml", "w") as f:
        f.write(yaml)

    zip_path = EXPORTS_DIR / f"project_{project_id}_yolo.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in out_dir.rglob("*"):
            zf.write(p, p.relative_to(out_dir))
    conn.close()
    return str(zip_path)


def export_csv(project_id: int, main_only: bool = True) -> str:
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    schema = json.loads(proj["keypoint_schema"])
    rows = _query_rows(conn, project_id, main_only=main_only)

    out_path = EXPORTS_DIR / f"project_{project_id}.csv"
    header = ["video_id", "frame_file", "frame_index", "batch_type", "labeled_by", "quality", "notes"]
    for kdef in schema:
        header += [f"{kdef['name']}_x", f"{kdef['name']}_y", f"{kdef['name']}_v"]
    header += ["bbox_x", "bbox_y", "bbox_w", "bbox_h"]

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            kps_raw = json.loads(row["keypoints"])
            lut = _kp_lookup(kps_raw)
            bbox = generate_bbox(
                kps_raw, schema,
                row["width"] or 1920,
                row["height"] or 1080,
            ) or {}
            line = [row["video_id"], row["filename"], row["frame_index"],
                    row["batch_type"], row["username"], row["quality"], row["notes"]]
            for kdef in schema:
                kp = lut.get(kdef["id"], {})
                line += [kp.get("x") if kp.get("x") is not None else "",
                         kp.get("y") if kp.get("y") is not None else "",
                         kp.get("visible", 0)]
            line += [bbox.get("x", 0), bbox.get("y", 0), bbox.get("w", 0), bbox.get("h", 0)]
            w.writerow(line)
    conn.close()
    return str(out_path)


def export_agreement(project_id: int) -> str:
    """Pilot 프레임 — 라벨러별 어노테이션 전체 (agreement 분석용)"""
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    schema = json.loads(proj["keypoint_schema"])

    rows = conn.execute("""
        SELECT f.id AS frame_id, f.frame_index, f.filename, f.video_id,
               u.username, a.keypoints, a.bbox, a.quality, fa.status
        FROM frames f
        JOIN frame_assignments fa ON fa.frame_id=f.id
        JOIN users u ON u.id=fa.user_id
        LEFT JOIN annotations a ON a.frame_id=f.id AND a.labeled_by=u.id
        WHERE f.project_id=? AND f.batch_type='pilot'
        ORDER BY f.frame_index, u.username
    """, (project_id,)).fetchall()

    out_path = EXPORTS_DIR / f"project_{project_id}_pilot_agreement.csv"
    header = ["frame_id", "frame_index", "video_id", "filename", "labeler", "status", "quality"]
    for kdef in schema:
        header += [f"{kdef['name']}_x", f"{kdef['name']}_y", f"{kdef['name']}_v"]

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            line = [row["frame_id"], row["frame_index"], row["video_id"],
                    row["filename"], row["username"], row["status"], row["quality"]]
            lut = _kp_lookup(json.loads(row["keypoints"])) if row["keypoints"] else {}
            for kdef in schema:
                kp = lut.get(kdef["id"], {})
                line += [kp.get("x") if kp.get("x") is not None else "",
                         kp.get("y") if kp.get("y") is not None else "",
                         kp.get("visible", 0)]
            w.writerow(line)
    conn.close()
    return str(out_path)
