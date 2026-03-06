import cv2
import numpy as np
import time
import os
import socketio
import threading
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

# --- 1. CONFIGURATION ---
SERVER_URL = os.getenv("SERVER_URL")
SECRET_KEY = os.getenv("PI_API_KEY")  # <--- CHECK YOUR KEY

if not SECRET_KEY:
    raise ValueError("FATAL: PI_API_KEY must be set in .env file")

CONFIDENCE_THRESHOLD = 0.50
MAX_VIDEO_LENGTH = 60  # 1 Minute Max
STILL_IMAGE_INTERVAL = 30  # New photo every 30s of motion
MOTION_TIMEOUT = 10  # End intrusion if no motion for 10s

# Paths
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(CURRENT_DIR, "model")
RECORDINGS_DIR = os.path.join(CURRENT_DIR, "recordings")  # Simulated SD card
PENDING_DIR = os.path.join(CURRENT_DIR, "pending_uploads")
MODEL_PATH = os.path.join(MODEL_DIR, "ssd_mobilenet_v2_coco_quant_postprocess.tflite")
LABEL_PATH = os.path.join(MODEL_DIR, "coco_labels.txt")

if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)
if not os.path.exists(PENDING_DIR):
    os.makedirs(PENDING_DIR)

# --- GLOBAL STATE ---
is_system_armed = False
is_connected = False
intrusion_active = False

# Variables for the Intrusion State Machine
intrusion_start_time = 0
last_motion_time = 0
last_still_capture_time = 0
last_ai_recheck_time = 0
video_writer = None
video_filename = ""
intrusion_session_id = ""

# Pending uploads management
pending_uploads = []
is_sweeping = False
last_error_message = ""

# --- 2. NETWORK MANAGER ---
sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)


@sio.event
def connect():
    global is_connected, last_error_message
    is_connected = True
    last_error_message = ""
    print(f"\n✅ NETWORK: Connected! ID: {sio.sid}")
    sio.emit("register_pi", {"token": SECRET_KEY})


@sio.event
def disconnect():
    global is_connected
    is_connected = False
    print(f"\n❌ NETWORK: Disconnected. Waiting for server...")


@sio.event
def connect_error(err):
    global last_error_message
    if str(err) != last_error_message:
        print(f"❌ Socket Connection Error: {err} (Will keep trying silently...)")
        last_error_message = str(err)


@sio.event
def state_update(data):
    global is_system_armed
    is_system_armed = data.get("isActive", False)
    status = "ARMED 🔴" if is_system_armed else "DISARMED 🟢"
    print(f"🔄 STATE: System is now {status}")


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

# --- 3. AI & MOTION HELPERS ---
try:
    import tflite_runtime.interpreter as tflite
except ImportError:
    import tensorflow.lite as tflite

interpreter = tflite.Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
height = input_details[0]["shape"][1]
width = input_details[0]["shape"][2]

with open(LABEL_PATH, "r") as f:
    labels = [line.strip() for line in f.readlines() if line.strip()]


def check_ai_for_person(frame):
    """Runs the AI model on a single frame. Returns (True, confidence) if person found."""
    start_time = time.time()

    frame_resized = cv2.resize(frame, (width, height))
    input_data = np.expand_dims(frame_resized, axis=0)

    if input_details[0]["dtype"] == np.uint8:
        input_data = input_data.astype(np.uint8)
    else:
        input_data = (np.float32(input_data) - 127.5) / 127.5

    interpreter.set_tensor(input_details[0]["index"], input_data)
    interpreter.invoke()

    scores = interpreter.get_tensor(output_details[2]["index"])[0]
    classes = interpreter.get_tensor(output_details[1]["index"])[0]

    inference_time = (time.time() - start_time) * 1000  # Convert to milliseconds

    for i in range(len(scores)):
        if scores[i] > CONFIDENCE_THRESHOLD:
            object_name = (
                labels[int(classes[i])] if int(classes[i]) < len(labels) else "Unknown"
            )
            if object_name == "person":
                print(
                    f"🧠 AI Inference: {inference_time:.1f}ms (Person: {int(scores[i]*100)}%)"
                )
                return True, scores[i]

    print(f"🧠 AI Inference: {inference_time:.1f}ms (No person detected)")
    return False, 0.0


def check_pir_simulation(current_frame, prev_frame):
    """
    Simulates a PIR sensor by checking for gross motion between frames.
    On real hardware, replace this with: return GPIO.input(PIR_PIN)
    """
    if prev_frame is None:
        return False

    # Convert to grayscale and blur to remove noise
    gray1 = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    gray1 = cv2.GaussianBlur(gray1, (21, 21), 0)
    gray2 = cv2.GaussianBlur(gray2, (21, 21), 0)

    # Compute difference
    delta = cv2.absdiff(gray1, gray2)
    thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]

    # If more than 500 pixels changed, we have motion
    motion_pixels = cv2.countNonZero(thresh)
    return motion_pixels > 500


def save_to_pending(file_path, file_type="video"):
    """Save file to pending uploads directory for sweeper to handle"""
    try:
        filename = os.path.basename(file_path)
        pending_path = os.path.join(PENDING_DIR, filename)

        # Copy file to pending directory
        import shutil

        shutil.copy2(file_path, pending_path)

        # Add to pending uploads list
        pending_uploads.append(
            {
                "filename": filename,
                "filepath": pending_path,
                "type": file_type,
                "attempts": 0,
            }
        )

        print(f"📁 Queued for upload: {filename}")
        return True
    except Exception as e:
        print(f"❌ Failed to queue file: {e}")
        return False


def attempt_upload(pending_file):
    """Attempt to upload a single pending file"""
    global pending_uploads, last_error_message

    try:
        with open(pending_file["filepath"], "rb") as f:
            files = {
                "video": (
                    pending_file["filename"],
                    f,
                    "video/mp4" if pending_file["type"] == "video" else "image/jpeg",
                )
            }
            data = {
                "sessionId": intrusion_session_id,
                "fileType": pending_file["type"],
                "edgeTimestamp": pending_file.get(
                    "timestamp", "datetime.datetime.now().isoformat()"
                ),
            }
            headers = {"Authorization": f"Bearer {SECRET_KEY}"}

            # Enhanced configuration for large files
            response = requests.post(
                f"{SERVER_URL}/api/upload",
                files=files,
                data=data,
                headers=headers,
                timeout=120,
                stream=True,  # Enable streaming for large files
            )

        pending_file["attempts"] += 1

        if response.status_code == 201:
            print(f"🧹 [SWEEPER] Upload Success: {pending_file['filename']}")
            # Remove from pending list and delete local file
            os.remove(pending_file["filepath"])
            pending_uploads.remove(pending_file)
            return True
        else:
            error_msg = f"[SWEEPER] Upload failed for {pending_file['filename']} | Reason: {response.status_code} - {response.text} (Will retry later)"
            if error_msg != last_error_message:
                print(f"❌ {error_msg}")
                last_error_message = error_msg
            return False
    except requests.exceptions.ConnectionError as err:
        # Silent retry for connection refused errors
        if err.errno == 61:  # ECONNREFUSED
            pass  # Don't log connection refused errors
        else:
            error_msg = f"[SWEEPER] Connection error for {pending_file['filename']}: {err} (Will retry later)"
            if error_msg != last_error_message:
                print(f"❌ {error_msg}")
                last_error_message = error_msg
        return False
    except Exception as e:
        error_msg = f"[SWEEPER] Upload error (attempt {pending_file['attempts']}) for {pending_file['filename']}: {e}"
        if error_msg != last_error_message:
            print(f"❌ {error_msg}")
            last_error_message = error_msg
        return False


def sweeper_function():
    """Background sweeper to handle pending uploads"""
    global is_sweeping, pending_uploads

    if is_sweeping or len(pending_uploads) == 0:
        return

    is_sweeping = True
    print(
        f"\n🧹 [SWEEPER] Found {len(pending_uploads)} pending file(s). Attempting uploads..."
    )

    for pending_file in pending_uploads[
        :
    ]:  # Copy list to avoid modification during iteration
        attempt_upload(pending_file)

    is_sweeping = False


def get_safe_timestamp():
    """Generate safe timestamp for filenames (replaces : and . with -)"""
    return datetime.datetime.now().isoformat().replace(":", "-").replace(".", "-")


def cleanup_old_recordings(days_to_keep=7):
    """Deletes files in the recordings directory older than specified days to prevent SD card from filling up."""
    if not os.path.exists(RECORDINGS_DIR):
        return

    current_time = time.time()
    cutoff_time = current_time - (days_to_keep * 86400)  # 86400 seconds in a day
    deleted_count = 0

    for filename in os.listdir(RECORDINGS_DIR):
        filepath = os.path.join(RECORDINGS_DIR, filename)
        if os.path.isfile(filepath):
            file_mtime = os.path.getmtime(filepath)
            if file_mtime < cutoff_time:
                try:
                    os.remove(filepath)
                    deleted_count += 1
                except Exception as e:
                    print(
                        f"❌ [CLEANUP] Failed to delete old recording {filename}: {e}"
                    )

    if deleted_count > 0:
        print(
            f"♻️ [CLEANUP] Removed {deleted_count} old recording(s) (> {days_to_keep} days) to free up space."
        )


def start_sweeper():
    """Start the background sweeper thread"""
    last_cleanup_time = 0

    def sweeper_loop():
        nonlocal last_cleanup_time
        while True:
            time.sleep(10)  # Run every 10 seconds
            sweeper_function()
            current_time = time.time()
            if current_time - last_cleanup_time > 3600:
                cleanup_old_recordings(days_to_keep=7)
                last_cleanup_time = current_time

    sweeper_thread = threading.Thread(target=sweeper_loop, daemon=True)
    sweeper_thread.start()
    print("🧹 [SWEEPER] Background uploader started")


def recover_pending_files():
    """Recover any pending files from previous sessions"""
    global pending_uploads

    if os.path.exists(PENDING_DIR):
        existing_files = os.listdir(PENDING_DIR)
        if existing_files:
            print(
                f"🔄 [RECOVERY] Found {len(existing_files)} pending file(s) from previous session"
            )

            for filename in existing_files:
                filepath = os.path.join(PENDING_DIR, filename)
                file_type = "video" if filename.endswith(".mp4") else "image"

                pending_uploads.append(
                    {
                        "filename": filename,
                        "filepath": filepath,
                        "type": file_type,
                        "attempts": 0,
                    }
                )
                print(f"📁 Recovered: {filename}")

            print("🧹 [RECOVERY] Files queued for upload")
        else:
            print("🧹 [RECOVERY] No pending files found")
    else:
        print("🧹 [RECOVERY] No pending directory found")


# --- INITIALIZATION ---
# Start the background sweeper for robust uploads
start_sweeper()

# Recover any pending files from previous sessions
recover_pending_files()


# --- 4. MAIN LOOP ---
cap = cv2.VideoCapture(0)
fourcc = cv2.VideoWriter_fourcc(*"XVID")
prev_frame = None

print("\n📷 SYSTEM ONLINE: Waiting for 'PIR' Motion...")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    current_time = time.time()

    # 1. PIR SENSOR CHECK (Simulated)
    pir_triggered = check_pir_simulation(frame, prev_frame)
    prev_frame = frame.copy()

    # VISUAL DEBUG: Blue dot if PIR is "feeling" movement
    if pir_triggered:
        cv2.circle(frame, (20, 20), 10, (255, 0, 0), -1)

    # --- STATE MACHINE LOGIC ---

    if is_system_armed:

        # STATE: IDLE -> CHECKING
        if not intrusion_active and pir_triggered:
            # PIR felt something! Quick, check with AI.
            person_found, confidence = check_ai_for_person(frame)

            if person_found:
                # STATE TRANSITION: INTRUSION STARTED
                intrusion_active = True
                intrusion_start_time = current_time
                last_motion_time = current_time
                last_still_capture_time = current_time
                last_ai_recheck_time = current_time
                intrusion_session_id = get_safe_timestamp()

                print(f"\n--- MOTION DETECTED ---")
                print(
                    f"🚨 INTRUSION STARTED! (Session: {intrusion_session_id}, Confidence: {int(confidence*100)}%)"
                )

                # A. Send Alert
                if is_connected:
                    sio.emit(
                        "pi_alert", {"message": "Person detected! Recording started."}
                    )
                else:
                    print("Alert dropped (No socket connection)")

                # B. Start Video
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                video_filename = f"intruder_{timestamp}.mp4"
                video_path = os.path.join(RECORDINGS_DIR, video_filename)
                video_writer = cv2.VideoWriter(
                    video_path,
                    cv2.VideoWriter_fourcc(*"avc1"),
                    20.0,
                    (640, 480),
                )

                # C. Save High Quality Still #1
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                still_filename = f"evidence_{timestamp}_start.jpg"
                still_path = os.path.join(RECORDINGS_DIR, still_filename)
                cv2.imwrite(still_path, frame)

                print(f"📁 Saved to SD Card: {still_filename}")

                # Queue for robust upload
                save_to_pending(still_path, "image")
                save_to_pending(video_path, "video")

                print("3. Passing to Background Uploader...")

        # STATE: INTRUSION ACTIVE
        elif intrusion_active:

            # A. Record Video
            frame_small = cv2.resize(frame, (640, 480))
            video_writer.write(frame_small)

            # B. Check for continued motion
            if pir_triggered:
                last_motion_time = current_time

            # C. Check for "Every 30s" Still
            if current_time - last_still_capture_time > STILL_IMAGE_INTERVAL:
                print("📸 Taking updated evidence photo...")
                timestamp = datetime.datetime.now().strftime("%H-%M-%S")
                still_filename = f"evidence_{timestamp}_update.jpg"
                still_path = os.path.join(RECORDINGS_DIR, still_filename)
                cv2.imwrite(still_path, frame)

                # Queue for robust upload
                save_to_pending(still_path, "image")

                last_still_capture_time = current_time

            # D. END CONDITIONS & AI RECHECK LOGIC
            # 1. Time limit reached (1 min)
            # 2. No motion for 10 seconds - then AI recheck
            time_since_motion = current_time - last_motion_time
            time_since_ai_recheck = current_time - last_ai_recheck_time
            recording_duration = current_time - intrusion_start_time

            # AI RECHECK: If no motion for 10s, recheck with AI
            if time_since_motion > 10 and time_since_ai_recheck > 10:
                print("🔍 No motion for 10s - Running AI recheck...")
                person_still_present, confidence = check_ai_for_person(frame)
                last_ai_recheck_time = current_time

                if person_still_present:
                    print(
                        f"✅ Person still detected! (Confidence: {int(confidence*100)}%) - Continuing monitoring"
                    )
                    last_motion_time = (
                        current_time  # Reset motion timer to keep recording
                    )
                    # Take an additional still image as evidence
                    timestamp = datetime.datetime.now().strftime("%H-%M-%S")
                    still_filename = f"evidence_{timestamp}_recheck.jpg"
                    still_path = os.path.join(RECORDINGS_DIR, still_filename)
                    cv2.imwrite(still_path, frame)

                    # Queue for robust upload
                    save_to_pending(still_path, "image")
                else:
                    print(
                        "❌ Person no longer detected - Will end intrusion if no motion for another 10s"
                    )

            # END INTRUSION: Max time OR person gone after recheck
            if recording_duration > MAX_VIDEO_LENGTH or (
                time_since_motion > 20 and time_since_ai_recheck > 10
            ):
                # STATE TRANSITION: END INTRUSION
                print(f"⏹️ INTRUSION ENDED. (Duration: {int(recording_duration)}s)")
                intrusion_active = False
                video_writer.release()
                video_writer = None

                # Upload video to GridFS
                if video_filename:
                    print(f"📁 Saved to SD Card: {video_filename}")
                    save_to_pending(
                        os.path.join(RECORDINGS_DIR, video_filename), "video"
                    )

                if is_connected:
                    reason = (
                        "Max time reached"
                        if recording_duration > MAX_VIDEO_LENGTH
                        else "Motion stopped"
                    )
                    sio.emit("pi_alert", {"message": f"Intrusion ended. ({reason})"})

    # --- DASHBOARD OVERLAY ---
    status_text = "ARMED" if is_system_armed else "DISARMED"
    color = (0, 0, 255) if is_system_armed else (0, 255, 0)

    cv2.putText(frame, status_text, (50, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    if intrusion_active:
        cv2.putText(
            frame,
            "REC 🔴",
            (width - 100, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2,
        )
        cv2.putText(
            frame,
            f"Motion Idle: {int(time.time() - last_motion_time)}s",
            (10, height - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 255),
            1,
        )

    cv2.imshow("Security Feed", frame)

    if cv2.waitKey(1) == ord("q"):
        if video_writer:
            video_writer.release()
        break

cap.release()
cv2.destroyAllWindows()
