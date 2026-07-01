"""
exporter.py - Export annotations to COCO / YOLO Pose / CSV formats
"""
import json
import csv
import zipfile
import shutil
from pathlib import Path
from database import get_db, DEFAULT_VIOLIN_SCHEMA, SKELETON_CONNECTIONS

BASE_DIR   = Path(__file__).parent
FRAMES_DIR = BASE_DIR / "frames"
EXPORTS_DIR = BASE_DIR / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)


def export_coco(project_id: int, out_name: str = None) -> str:
    """COCO Keypoint format JSON 생성"""
    conn = get_db()

    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    schema = json.loads(proj["keypoint_schema"])
    kp_names = [k["name"] for k in schema]

    frames = conn.execute("""
        SELECT f.*, a.keypoints, a.bbox, a.labeled_by
        FROM frames f
        JOIN annotations a ON a.frame_id = f.id
        WHERE f.project_id=? AND f.status IN ('labeled','reviewed')
    """, (project_id,)).fetchall()

    images, annotations_list = [], []

    for i, row in enumerate(frames):
        img_id = row["id"]
        images.append({
            "id": img_id,
            "file_name": f"{row['video_id']}/{row['filename']}",
            "width": 1920, "height": 1080  # updated per-frame if needed
        })

        kps_raw = json.loads(row["keypoints"])
        coco_kps = []
        num_labeled = 0
        for kp in kps_raw:
            x, y, v = kp.get("x", 0), kp.get("y", 0), kp.get("visible", 0)
            coco_kps += [x, y, v]
            if v > 0:
                num_labeled += 1

        bbox = json.loads(row["bbox"]) if row["bbox"] else {"x": 0, "y": 0, "w": 0, "h": 0}

        annotations_list.append({
            "id": i,
            "image_id": img_id,
            "category_id": 1,
            "keypoints": coco_kps,
            "num_keypoints": num_labeled,
            "bbox": [bbox["x"], bbox["y"], bbox["w"], bbox["h"]],
            "area": bbox["w"] * bbox["h"],
            "iscrowd": 0
        })

    coco = {
        "info": {"description": proj["name"], "version": "1.0"},
        "categories": [{
            "id": 1,
            "name": "violinist",
            "keypoints": kp_names,
            "skeleton": SKELETON_CONNECTIONS
        }],
        "images": images,
        "annotations": annotations_list
    }

    conn.close()
    out_name = out_name or f"project_{project_id}_coco"
    out_path = EXPORTS_DIR / f"{out_name}.json"
    with open(out_path, "w") as f:
        json.dump(coco, f, indent=2)
    print(f"[EXPORT] COCO -> {out_path}")
    return str(out_path)


def export_yolo_pose(project_id: int, out_name: str = None) -> str:
    """YOLO Pose format (txt per image + yaml)"""
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    schema = json.loads(proj["keypoint_schema"])
    n_kp = len(schema)

    frames = conn.execute("""
        SELECT f.*, a.keypoints, a.bbox, v.width, v.height
        FROM frames f
        JOIN annotations a ON a.frame_id = f.id
        JOIN videos v ON v.id = f.video_id
        WHERE f.project_id=? AND f.status IN ('labeled','reviewed')
    """, (project_id,)).fetchall()

    out_name = out_name or f"project_{project_id}_yolo"
    out_dir = EXPORTS_DIR / out_name
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    (out_dir / "labels").mkdir(parents=True, exist_ok=True)

    for row in frames:
        W = row["width"] or 1920
        H = row["height"] or 1080
        kps_raw = json.loads(row["keypoints"])
        bbox = json.loads(row["bbox"]) if row["bbox"] else {"x": 0, "y": 0, "w": W, "h": H}

        cx = (bbox["x"] + bbox["w"] / 2) / W
        cy = (bbox["y"] + bbox["h"] / 2) / H
        bw = bbox["w"] / W
        bh = bbox["h"] / H

        kp_str = ""
        for kp in kps_raw:
            x_n = kp.get("x", 0) / W
            y_n = kp.get("y", 0) / H
            v   = kp.get("visible", 0)
            kp_str += f" {x_n:.6f} {y_n:.6f} {v}"

        label_name = f"{row['video_id']}_{row['filename'].replace('.jpg','')}.txt"
        with open(out_dir / "labels" / label_name, "w") as f:
            f.write(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}{kp_str}\n")

        src_img = FRAMES_DIR / str(row["video_id"]) / row["filename"]
        dst_img = out_dir / "images" / f"{row['video_id']}_{row['filename']}"
        if src_img.exists():
            shutil.copy2(src_img, dst_img)

    # YAML
    yaml_content = f"""path: {out_dir}
train: images
val: images

kpt_shape: [{n_kp}, 3]

names:
  0: violinist
"""
    with open(out_dir / "data.yaml", "w") as f:
        f.write(yaml_content)

    # ZIP
    zip_path = EXPORTS_DIR / f"{out_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in out_dir.rglob("*"):
            zf.write(p, p.relative_to(out_dir))

    conn.close()
    print(f"[EXPORT] YOLO Pose -> {zip_path}")
    return str(zip_path)


def export_csv(project_id: int) -> str:
    """CSV 포맷 (분석용)"""
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    schema = json.loads(proj["keypoint_schema"])
    kp_names = [k["name"] for k in schema]

    frames = conn.execute("""
        SELECT f.video_id, f.filename, f.frame_index, f.timestamp_sec,
               a.keypoints, a.labeled_by, u.username
        FROM frames f
        JOIN annotations a ON a.frame_id = f.id
        JOIN users u ON u.id = a.labeled_by
        WHERE f.project_id=? AND f.status IN ('labeled','reviewed')
        ORDER BY f.video_id, f.frame_index
    """, (project_id,)).fetchall()

    out_path = EXPORTS_DIR / f"project_{project_id}.csv"
    header = ["video_id", "frame_file", "frame_index", "timestamp_sec", "labeled_by"]
    for kn in kp_names:
        header += [f"{kn}_x", f"{kn}_y", f"{kn}_visible"]

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in frames:
            kps = json.loads(row["keypoints"])
            line = [row["video_id"], row["filename"],
                    row["frame_index"], row["timestamp_sec"], row["username"]]
            for kp in kps:
                line += [kp.get("x", 0), kp.get("y", 0), kp.get("visible", 0)]
            w.writerow(line)

    conn.close()
    print(f"[EXPORT] CSV -> {out_path}")
    return str(out_path)
