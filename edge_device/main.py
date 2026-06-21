import cv2
import numpy as np
import time
import os
import socketio
import threading
import datetime
import requests
import json
import shutil
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:3000")
SECRET_KEY = os.getenv("PI_SECRET")

if not SECRET_KEY:
    raise ValueError("FATAL: PI_SECRET must be set in the environment profile")

CONFIDENCE_THRESHOLD = 0.50
MAX_VIDEO_LENGTH = 60
TARGET_FPS = 10.0
FRAME_INTERVAL = 1.0 / TARGET_FPS

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(CURRENT_DIR, "model")
RECORDINGS_DIR = os.path.join(CURRENT_DIR, "recordings")
PENDING_DIR = os.path.join(CURRENT_DIR, "pending_uploads")
MODEL_PATH = os.path.join(MODEL_DIR, "ssd_mobilenet_v2_coco_quant_postprocess.tflite")
LABEL_PATH = os.path.join(MODEL_DIR, "coco_labels.txt")

# Ensure required operating directories exist locally
for folder in [RECORDINGS_DIR, PENDING_DIR]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# --- GLOBAL ARCHITECTURE STATE ---
is_system_armed = False  # Controlled dynamically via dashboard WebSockets
is_connected = False
intrusion_active = False
MAX_UPLOAD_LIMIT = 5
frame_counter = 0
missed_alerts = []
ARM_DELAY = 5
arm_timestamp = 0
MIN_INTRUSION_DURATION = 2
AI_CONFIRM_WINDOW = 10

pending_lock = threading.Lock()
person_detection_counter = 0
PERSON_CONFIRM_FRAMES = 5
last_frame_had_person = False
confidence_sum = 0.0

intrusion_start_time = 0
last_motion_time = 0
last_ai_recheck_time = 0
video_writer = None
video_filename = ""
intrusion_session_id = ""
session_chunk_counter = 1
last_intrusion_end = 0
INTRUSION_COOLDOWN = 5

pending_uploads = []
is_sweeping = False
last_error_message = ""

# --- NETWORK CORE (SOCKET.IO OVER WebSockets) ---
sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)


@sio.event
def connect():
    global is_connected, last_error_message
    is_connected = True
    last_error_message = ""
    print(f"\n✅ NETWORK: Connected successfully. Session ID: {sio.sid}")
    sio.emit("register_pi", {"token": SECRET_KEY})

    # Flush alert cache generated during network dropouts
    for alert in missed_alerts:
        sio.emit("pi_alert", alert)
    missed_alerts.clear()


@sio.event
def disconnect():
    global is_connected
    is_connected = False
    print("\n❌ NETWORK: Dropped connection interface. Re-establishing channel...")


@sio.event
def connect_error(err):
    global last_error_message
    if str(err) != last_error_message:
        print(f"❌ Socket Connection Error: {err}")
        last_error_message = str(err)


@sio.event
def state_update(data):
    global is_system_armed, arm_timestamp
    new_state = data.get("isActive", False)
    if new_state and not is_system_armed:
        arm_timestamp = time.time()
    is_system_armed = new_state
    print(
        f"🔄 STATE: System telemetry synchronized to: {'ARMED 🔴' if is_system_armed else 'DISARMED 🟢'}"
    )


def network_loop():
    while True:
        if not sio.connected:
            try:
                sio.connect(
                    SERVER_URL,
                    transports=["websocket", "polling"],
                    wait_timeout=5,
                    auth={"token": SECRET_KEY},
                )
            except Exception:
                time.sleep(2)
        else:
            time.sleep(1)


threading.Thread(target=network_loop, daemon=True).start()

# --- OPTIMIZED AI RUNTIME INITIALIZATION ---
try:
    import ai_edge_litert.interpreter as litert_interpreter

    interpreter_class = litert_interpreter.Interpreter
    print("🧠 AI ENGINE: Native Google LiteRT Runtime successfully bound.")
except ImportError:
    try:
        import tflite_runtime.interpreter as tflite

        interpreter_class = tflite.Interpreter
        print(
            "🧠 AI ENGINE: Falling back to legacy tflite_runtime interpreter platform."
        )
    except ImportError:
        import tensorflow.lite as tflite

        interpreter_class = tflite.Interpreter
        print(
            "🧠 AI ENGINE: Falling back to development ecosystem standard TensorFlow Lite framework."
        )

interpreter = interpreter_class(model_path=MODEL_PATH, num_threads=2)
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
height = input_details[0]["shape"][1]
width = input_details[0]["shape"][2]

with open(LABEL_PATH, "r") as f:
    labels = [line.strip() for line in f.readlines() if line.strip()]


def check_ai_for_person(frame):
    start_time = time.time()
    frame_resized = cv2.resize(frame, (width, height))
    input_data = np.expand_dims(frame_resized, axis=0)

    if input_details[0]["dtype"] == np.uint8:
        input_data = input_data.astype(np.uint8)
    else:
        input_data = (np.float32(input_data) - 127.5) / 127.5

    interpreter.set_tensor(input_details[0]["index"], input_data)
    interpreter.invoke()

    boxes = interpreter.get_tensor(output_details[0]["index"])[0]
    classes = interpreter.get_tensor(output_details[1]["index"])[0]
    scores = interpreter.get_tensor(output_details[2]["index"])[0]

    inference_time = (time.time() - start_time) * 1000

    for i in range(len(scores)):
        if scores[i] < CONFIDENCE_THRESHOLD:
            continue
        object_name = (
            labels[int(classes[i])] if int(classes[i]) < len(labels) else "Unknown"
        )

        if object_name == "person":
            ymin, xmin, ymax, xmax = boxes[i]
            if (ymax - ymin) >= 0.1 and (xmax - xmin) >= 0.1:
                print(
                    f"🧠 AI Inference: {inference_time:.1f}ms (Person Confirmed: {int(scores[i]*100)}%)"
                )
                return True, scores[i]

    print(f"🧠 AI Inference: {inference_time:.1f}ms (No threat presence classified)")
    return False, 0.0


def check_motion_detected(current_frame, prev_frame):
    if prev_frame is None:
        return False
    gray1 = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    gray1 = cv2.GaussianBlur(gray1, (21, 21), 0)
    gray2 = cv2.GaussianBlur(gray2, (21, 21), 0)
    delta = cv2.absdiff(gray1, gray2)
    thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]
    return cv2.countNonZero(thresh) > 3000


# --- UNIQUE SESSION ID (millisecond resolution + same-ms guard) ---
_last_session_stamp = ""
_session_dedupe_counter = 0


def make_session_id():
    """Return a unique, chronologically-sortable intrusion session id.

    Uses millisecond resolution (YYYYMMDD_HHMMSS_mmm). A same-millisecond guard
    appends an incrementing counter so two intrusions confirmed within the exact
    same millisecond can never collide into the same id (and therefore the same
    filename), which previously caused two events to share one file on disk.
    """
    global _last_session_stamp, _session_dedupe_counter
    now = datetime.datetime.utcnow()
    stamp = now.strftime("%Y%m%d_%H%M%S_") + f"{now.microsecond // 1000:03d}"

    if stamp == _last_session_stamp:
        _session_dedupe_counter += 1
        return f"{stamp}_{_session_dedupe_counter}"

    _last_session_stamp = stamp
    _session_dedupe_counter = 0
    return stamp


# --- TRANSACTIONAL STORAGE WORKERS ---
def save_to_pending(file_path, file_type="video", session_id=None):
    try:
        filename = os.path.basename(file_path)
        pending_path = os.path.join(PENDING_DIR, filename)

        shutil.copy2(file_path, pending_path)

        active_session = session_id or intrusion_session_id or "unknown"
        metadata = {
            "filename": filename,
            "filepath": pending_path,
            "type": file_type,
            "attempts": 0,
            "sessionId": active_session,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }

        with open(f"{pending_path}.json", "w") as f:
            json.dump(metadata, f)

        with pending_lock:
            pending_uploads.append(metadata)
        print(f"📁 STORAGE: Asset compiled and indexed for queue execution: {filename}")
        return True
    except Exception as e:
        print(f"❌ STORAGE ERROR: Failed to stage asset for transmission: {e}")
        return False


def attempt_upload(pending_file):
    global pending_uploads, last_error_message

    try:
        with open(pending_file["filepath"], "rb") as f:
            data = {
                "sessionId": pending_file.get("sessionId", "unknown"),
                "fileType": pending_file["type"],
                "edgeTimestamp": pending_file.get("timestamp"),
            }
            mime_type = "video/mp4" if pending_file["type"] == "video" else "image/jpeg"
            files = {"video": (pending_file["filename"], f, mime_type)}
            headers = {"Authorization": f"Bearer {SECRET_KEY}"}

            response = requests.post(
                f"{SERVER_URL}/api/upload",
                data=data,
                files=files,
                headers=headers,
                timeout=120,
            )

        pending_file["attempts"] += 1
        json_path = f"{pending_file['filepath']}.json"

        if response.status_code == 201:
            print(
                f"🧹 SWEEPER: Transaction clear. Uploaded successfully: {pending_file['filename']}"
            )
            os.remove(pending_file["filepath"])
            if os.path.exists(json_path):
                os.remove(json_path)
            with pending_lock:
                if pending_file in pending_uploads:
                    pending_uploads.remove(pending_file)
            return True
        else:
            if pending_file["attempts"] >= MAX_UPLOAD_LIMIT:
                print(
                    f"❌ SWEEPER: Boundary limit dropped. Dropping corrupted package: {pending_file['filename']}"
                )
                os.remove(pending_file["filepath"])
                if os.path.exists(json_path):
                    os.remove(json_path)
                with pending_lock:
                    if pending_file in pending_uploads:
                        pending_uploads.remove(pending_file)
            return False
    except FileNotFoundError:
        # The media file is gone (already uploaded, or removed externally). There
        # is nothing to retry, so drop this stale queue entry and its sidecar
        # instead of mislabelling it as a network failure and retrying forever.
        print(
            f"⚠️  SWEEPER: File no longer on disk for {pending_file['filename']} — dropping stale queue entry."
        )
        json_path = f"{pending_file['filepath']}.json"
        if os.path.exists(json_path):
            try:
                os.remove(json_path)
            except OSError:
                pass
        with pending_lock:
            if pending_file in pending_uploads:
                pending_uploads.remove(pending_file)
        return False
    except Exception as e:
        # Genuine network/transport failure: keep the file and retry next sweep.
        print(f"❌ SWEEPER TRANSMISSION FAILED: Network channel blocked: {e}")
        return False


def sweeper_function():
    global is_sweeping, pending_uploads
    if is_sweeping or len(pending_uploads) == 0:
        return
    is_sweeping = True
    with pending_lock:
        files = pending_uploads[:]
    for pending_file in files:
        attempt_upload(pending_file)
    is_sweeping = False


def start_sweeper():
    def sweeper_loop():
        while True:
            time.sleep(10)
            sweeper_function()

    threading.Thread(target=sweeper_loop, daemon=True).start()


def bootstrap_pending():
    """Recover un-uploaded evidence left in PENDING_DIR after a crash/restart.

    The in-memory queue starts empty, but media from a previous run may still be
    on disk. Each media file has a {name}.json sidecar (written by
    save_to_pending) holding sessionId/type/timestamp. We rebuild the queue from
    those sidecars so the next sweeper cycle drains them — guaranteeing evidence
    eventually reaches the database and isn't stranded.

    A media file whose sidecar is missing or corrupt cannot be uploaded safely
    (no sessionId/timestamp for correct Gallery grouping), so it is discarded to
    avoid indefinite disk bloat. Orphaned sidecars (no media) are cleaned up too.
    """
    recovered = 0
    try:
        entries = os.listdir(PENDING_DIR)
    except FileNotFoundError:
        return

    media_files = [
        f for f in entries if not f.endswith(".json") and not f.startswith(".")
    ]

    for filename in media_files:
        filepath = os.path.join(PENDING_DIR, filename)
        json_path = f"{filepath}.json"

        if not os.path.exists(json_path):
            print(f"🗑️  RECOVERY: No sidecar for {filename} — discarding orphan.")
            try:
                os.remove(filepath)
            except OSError:
                pass
            continue

        try:
            with open(json_path, "r") as f:
                meta = json.load(f)

            # Reset attempts so recovered evidence gets a fresh upload budget.
            # Preserve sessionId/type/timestamp so the Gallery groups and orders
            # the clip by its ORIGINAL capture time, not the recovery time.
            metadata = {
                "filename": filename,
                "filepath": filepath,
                "type": meta.get("type", "video"),
                "attempts": 0,
                "sessionId": meta.get("sessionId", "unknown"),
                "timestamp": meta.get(
                    "timestamp", datetime.datetime.utcnow().isoformat() + "Z"
                ),
            }
            with pending_lock:
                pending_uploads.append(metadata)
            recovered += 1
        except (json.JSONDecodeError, OSError, ValueError) as e:
            print(f"🗑️  RECOVERY: Corrupt sidecar for {filename} ({e}) — discarding orphan.")
            for p in (filepath, json_path):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # Sweep up any orphaned sidecars whose media file is gone.
    for filename in [f for f in os.listdir(PENDING_DIR) if f.endswith(".json")]:
        json_path = os.path.join(PENDING_DIR, filename)
        if not os.path.exists(json_path[:-5]):  # strip ".json"
            try:
                os.remove(json_path)
            except OSError:
                pass

    if recovered:
        print(f"🔁 RECOVERY: Re-queued {recovered} un-uploaded asset(s) from a previous session.")


# --- HARDWARE RUNTIME INIT ---
bootstrap_pending()
start_sweeper()

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

# --- LOCK TRUE CAPTURE RESOLUTION ---
# cap.set() only REQUESTS a resolution; many webcams ignore it and hand back a
# different size. cv2.VideoWriter silently drops every frame whose dimensions
# don't exactly match the size it was opened with, leaving 0-byte / unplayable
# clips. So we probe one real frame and reuse its actual dimensions everywhere.
ret, probe_frame = cap.read()
if ret and probe_frame is not None:
    FRAME_HEIGHT, FRAME_WIDTH = probe_frame.shape[:2]
else:
    FRAME_WIDTH, FRAME_HEIGHT = 1280, 720
print(f"📐 CAPTURE RESOLUTION LOCKED AT: {FRAME_WIDTH}x{FRAME_HEIGHT}")


def create_video_writer(output_path):
    """Create a VideoWriter for recording intrusion clips.

    Uses mp4v (MPEG-4 Part 2), which is bundled with OpenCV and needs no
    external codec DLL — so recording is reliable on this Windows dev machine.
    mp4v produces small files (a full 60s 720p clip stays well under the 50MB
    upload limit) but may not play *inline* in some browsers; clips always
    download and play in any local media player.

    NOTE: On the Raspberry Pi 5, H.264 (avc1) is hardware-accelerated and
    works out of the box. When migrating, switch the codec tuple back to
    ("avc1", "mp4v") to get browser-native inline playback for free.
    """
    for codec in ("mp4v",):
        fourcc = cv2.VideoWriter_fourcc(*codec)
        writer = cv2.VideoWriter(
            output_path, fourcc, TARGET_FPS, (FRAME_WIDTH, FRAME_HEIGHT)
        )
        if writer.isOpened():
            return writer
        writer.release()
    print(f"❌ CODEC: Could not initialize any VideoWriter for {output_path}")
    return None


prev_frame = None
ai_window_counter = 0

print("\n📷 CORE ONLINE: Hardware capture loops engaged.")

# --- MAIN SYSTEM STATE LOOPS ---
while True:
    loop_start = time.time()
    current_time = time.time()

    ret, frame = cap.read()
    if not ret:
        print("Camera hardware read exception, retrying capture sequence...")
        time.sleep(1)
        continue

    # Enforce systemic delay filters to handle initialization or cool-down transitions cleanly
    if current_time - arm_timestamp < ARM_DELAY:
        continue
    if current_time - last_intrusion_end < INTRUSION_COOLDOWN:
        continue

    pir_triggered = check_motion_detected(frame, prev_frame)
    prev_frame = frame.copy()

    # Visual telemetry interface marker (Blue dot signals hardware movement registration)
    if pir_triggered:
        cv2.circle(frame, (20, 20), 10, (255, 0, 0), -1)

    # --- DECISION TREE EXECUTIVE LOGIC ---
    if is_system_armed:
        frame_counter += 1
        if not intrusion_active and pir_triggered:
            ai_window_counter = AI_CONFIRM_WINDOW

        if not intrusion_active and ai_window_counter > 0:
            ai_window_counter -= 1
            person_found, confidence = check_ai_for_person(frame)

            if person_found:
                confidence_sum += confidence
                person_detection_counter = (
                    (person_detection_counter + 1) if last_frame_had_person else 1
                )
                last_frame_had_person = True
                print(
                    f"[{current_time:.1f}s] Verification threshold tracking: {person_detection_counter}/{PERSON_CONFIRM_FRAMES}"
                )
            else:
                last_frame_had_person = False
                person_detection_counter = 0

            if person_detection_counter >= PERSON_CONFIRM_FRAMES:
                if (confidence_sum / person_detection_counter) < 0.60:
                    person_detection_counter = 0
                    confidence_sum = 0
                    last_frame_had_person = False
                    continue

                intrusion_active = True
                intrusion_start_time = current_time
                last_motion_time = current_time
                last_ai_recheck_time = current_time
                session_chunk_counter = 1

                intrusion_session_id = make_session_id()

                print(
                    f"\n🚨 [{current_time:.1f}s] THREAT VERIFIED. INTRUSION PROTOCOL ENGAGED. Session ID: {intrusion_session_id}"
                )

                alert_payload = {
                    "message": "Threat verified. Recording pipeline active.",
                    "location": "Front Cam (Hardware Cluster)",
                    "sessionId": intrusion_session_id,
                }
                if is_connected:
                    sio.emit("pi_alert", alert_payload)
                else:
                    print(f"✉️ Alert buffered locally: {alert_payload['message']}")
                    missed_alerts.append(alert_payload)

                video_filename = (
                    f"evidence_{intrusion_session_id}_pt{session_chunk_counter}.mp4"
                )
                video_path = os.path.join(RECORDINGS_DIR, video_filename)
                video_writer = create_video_writer(video_path)

                still_filename = f"evidence_{intrusion_session_id}_start.jpg"
                still_path = os.path.join(RECORDINGS_DIR, still_filename)
                cv2.imwrite(still_path, frame)
                save_to_pending(still_path, "image", intrusion_session_id)

        elif intrusion_active:
            if video_writer:
                # Resize to the writer's locked resolution. If a frame ever comes
                # back at a different size, an unmatched write is silently dropped.
                video_writer.write(cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT)))
            if pir_triggered:
                last_motion_time = current_time

            time_since_motion = current_time - last_motion_time
            time_since_ai_recheck = current_time - last_ai_recheck_time
            recording_duration = current_time - intrusion_start_time

            if time_since_motion > 10 and time_since_ai_recheck > 10:
                print(
                    f"🔍 [{current_time:.1f}s] Motion signature quiet for 10s - Executing algorithmic re-check evaluation..."
                )
                person_still_present, confidence = check_ai_for_person(frame)
                last_ai_recheck_time = current_time

                if person_still_present:
                    last_motion_time = current_time
                    now_str = datetime.datetime.utcnow().strftime("%H%M%S")
                    still_filename = (
                        f"evidence_{intrusion_session_id}_{now_str}_recheck.jpg"
                    )
                    still_path = os.path.join(RECORDINGS_DIR, still_filename)
                    cv2.imwrite(still_path, frame)
                    save_to_pending(still_path, "image", intrusion_session_id)

            # SCENARIO A: 60-Second Video Boundary Hit (Intruder remains within sector)
            if recording_duration > MAX_VIDEO_LENGTH:
                print(
                    f"\n📦 [{current_time:.1f}s] Primary chunk limit reached. Executing stateless asset rollover loop..."
                )
                if video_writer:
                    video_writer.release()

                old_video_path = os.path.join(RECORDINGS_DIR, video_filename)
                save_to_pending(old_video_path, "video", intrusion_session_id)

                session_chunk_counter += 1
                video_filename = (
                    f"evidence_{intrusion_session_id}_pt{session_chunk_counter}.mp4"
                )
                video_path = os.path.join(RECORDINGS_DIR, video_filename)

                print(f"🎬 Rollover block opened: {video_filename}")
                video_writer = create_video_writer(video_path)
                intrusion_start_time = current_time

            # SCENARIO B: System Boundary Cleared (Sector unoccupied for 20 seconds)
            elif time_since_motion > 20 and time_since_ai_recheck > 10:
                print(
                    f"⏹️ [{current_time:.1f}s] INTRUSION THREAT RESCINDED. Closing operational session log metadata structures."
                )
                intrusion_active = False
                if video_writer:
                    video_writer.release()
                    video_writer = None
                last_intrusion_end = current_time

                if video_filename:
                    save_to_pending(
                        os.path.join(RECORDINGS_DIR, video_filename),
                        "video",
                        intrusion_session_id,
                    )

    cv2.imshow("Security Feed", frame)
    if cv2.waitKey(1) == ord("q"):
        break

    processing_time = time.time() - loop_start
    sleep_duration = FRAME_INTERVAL - processing_time
    if sleep_duration > 0:
        time.sleep(sleep_duration)

if cap:
    cap.release()
cv2.destroyAllWindows()
