"""
extractor.py - Video upload & frame extraction engine
"""
import cv2
import os
import threading
from pathlib import Path
from database import get_db

BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
FRAMES_DIR  = BASE_DIR / "frames"

UPLOADS_DIR.mkdir(exist_ok=True)
FRAMES_DIR.mkdir(exist_ok=True)

ALLOWED_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}


def allowed_video(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTS


def get_video_info(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {}
    fps    = cap.get(cv2.CAP_PROP_FPS)
    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w      = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {"fps": round(fps, 3), "total_frames": total,
            "width": w, "height": h,
            "duration_sec": round(total / fps, 2) if fps > 0 else 0}


def extract_frames_async(video_id: int, fps_target: float = 2.0,
                         start_sec: float = 0, end_sec: float = 0,
                         socketio=None):
    """백그라운드 스레드에서 프레임 추출 후 DB에 저장"""
    def run():
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM videos WHERE id=?", (video_id,)
            ).fetchone()
            if not row:
                return

            video_path = str(UPLOADS_DIR / row["filename"])
            info = get_video_info(video_path)
            src_fps = info.get("fps", 30)
            total   = info.get("total_frames", 0)

            frame_dir = FRAMES_DIR / str(video_id)
            frame_dir.mkdir(exist_ok=True)

            cap = cv2.VideoCapture(video_path)
            start_f = int(start_sec * src_fps) if start_sec > 0 else 0
            end_f   = int(end_sec   * src_fps) if end_sec   > 0 else total
            interval = max(1, int(round(src_fps / fps_target))) if fps_target > 0 else 1

            cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)

            saved = 0
            fi = start_f
            while fi < end_f:
                ret, frame = cap.read()
                if not ret:
                    break
                if (fi - start_f) % interval == 0:
                    fname = f"frame_{fi:08d}.jpg"
                    cv2.imwrite(str(frame_dir / fname), frame,
                                [cv2.IMWRITE_JPEG_QUALITY, 92])
                    ts = round(fi / src_fps, 3)
                    conn.execute("""
                        INSERT OR IGNORE INTO frames
                        (video_id, project_id, filename, frame_index, timestamp_sec)
                        VALUES (?,?,?,?,?)
                    """, (video_id, row["project_id"], fname, fi, ts))
                    saved += 1
                fi += 1

            cap.release()
            conn.execute("UPDATE videos SET status='ready' WHERE id=?", (video_id,))
            conn.commit()

            if socketio:
                socketio.emit("extract_done", {
                    "video_id": video_id, "frames_saved": saved
                })
            print(f"[EXTRACT] video {video_id}: {saved} frames saved")

        except Exception as e:
            conn.execute("UPDATE videos SET status='error' WHERE id=?", (video_id,))
            conn.commit()
            print(f"[EXTRACT ERROR] {e}")
        finally:
            conn.close()

    conn = get_db()
    conn.execute("UPDATE videos SET status='extracting' WHERE id=?", (video_id,))
    conn.commit()
    conn.close()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
