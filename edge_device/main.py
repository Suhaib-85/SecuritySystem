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
SECRET_KEY = os.getenv("PI_API_KEY")

if not SECRET_KEY:
    raise ValueError("FATAL: PI_API_KEY must be set in the environment profile")

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
    except Exception as e:
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


# --- HARDWARE RUNTIME INIT ---
start_sweeper()

cap = cv2.VideoCapture(0)
if cap:
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

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

                now_utc = datetime.datetime.utcnow()
                intrusion_session_id = now_utc.strftime("%Y%m%d_%H%M%S")

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
                video_writer = cv2.VideoWriter(
                    video_path,
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    TARGET_FPS,
                    (1280, 720),
                )

                still_filename = f"evidence_{intrusion_session_id}_start.jpg"
                still_path = os.path.join(RECORDINGS_DIR, still_filename)
                cv2.imwrite(still_path, frame)
                save_to_pending(still_path, "image", intrusion_session_id)

        elif intrusion_active:
            if video_writer:
                video_writer.write(frame)
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
                video_writer = cv2.VideoWriter(
                    video_path,
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    TARGET_FPS,
                    (1280, 720),
                )
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
