import cv2
import numpy as np
import time
import os
import socketio
import threading
import datetime
import requests

# --- 1. CONFIGURATION ---
SERVER_URL = "http://127.0.0.1:3000"
SECRET_KEY = "301_BSCS_2K22"  # <--- CHECK YOUR KEY
CONFIDENCE_THRESHOLD = 0.50
MAX_VIDEO_LENGTH = 60  # 1 Minute Max
STILL_IMAGE_INTERVAL = 30  # New photo every 30s of motion
MOTION_TIMEOUT = 10  # End intrusion if no motion for 10s

# Paths
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(CURRENT_DIR, "model")
RECORDINGS_DIR = os.path.join(CURRENT_DIR, "recordings")
MODEL_PATH = os.path.join(MODEL_DIR, "ssd_mobilenet_v2_coco_quant_postprocess.tflite")
LABEL_PATH = os.path.join(MODEL_DIR, "coco_labels.txt")

if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)

# --- GLOBAL STATE ---
is_system_armed = False
is_connected = False
intrusion_active = False

# Variables for the Intrusion State Machine
intrusion_start_time = 0
last_motion_time = 0
last_still_capture_time = 0
video_writer = None
video_filename = ""

# --- 2. NETWORK MANAGER ---
sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)


@sio.event
def connect():
    global is_connected
    is_connected = True
    print("\n✅ NETWORK: Connected!")
    sio.emit("register_pi", {"token": SECRET_KEY})


@sio.event
def disconnect():
    global is_connected
    is_connected = False


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
                print(f"🧠 AI Inference: {inference_time:.1f}ms (Person: {int(scores[i]*100)}%)")
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


def upload_to_server(file_path, file_type="video"):
    """Upload media file to backend GridFS storage"""
    try:
        with open(file_path, 'rb') as f:
            files = {'video': (os.path.basename(file_path), f, 'video/x-msvideo' if file_type == "video" else 'image/jpeg')}
            headers = {'Authorization': f'Bearer {SECRET_KEY}'}
            response = requests.post(f"{SERVER_URL}/api/upload", files=files, headers=headers, timeout=30)
            
        if response.status_code == 201:
            print(f"✅ Upload successful: {os.path.basename(file_path)}")
            return True
        else:
            print(f"❌ Upload failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Upload error: {e}")
        return False


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

                print(f"\n🚨 INTRUSION STARTED! (Confidence: {int(confidence*100)}%)")

                # A. Send Alert
                if is_connected:
                    sio.emit(
                        "pi_alert", {"message": "Person detected! Recording started."}
                    )

                # B. Start Video
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                video_filename = f"intruder_{timestamp}.avi"
                video_writer = cv2.VideoWriter(
                    os.path.join(RECORDINGS_DIR, video_filename),
                    fourcc,
                    20.0,
                    (640, 480),
                )

                # C. Save High Quality Still #1
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                still_filename = f"evidence_{timestamp}_start.jpg"
                still_path = os.path.join(RECORDINGS_DIR, still_filename)
                cv2.imwrite(still_path, frame)
                
                # Upload initial still image to GridFS
                upload_thread = threading.Thread(
                    target=upload_to_server, 
                    args=(still_path, "image")
                )
                upload_thread.start()

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
                
                # Upload still image to GridFS
                upload_thread = threading.Thread(
                    target=upload_to_server, 
                    args=(still_path, "image")
                )
                upload_thread.start()
                
                last_still_capture_time = current_time

            # D. END CONDITIONS
            # 1. Time limit reached (1 min)
            # 2. No motion for 10 seconds
            time_since_motion = current_time - last_motion_time
            recording_duration = current_time - intrusion_start_time

            if (
                recording_duration > MAX_VIDEO_LENGTH
                or time_since_motion > MOTION_TIMEOUT
            ):
                # STATE TRANSITION: END INTRUSION
                print(f"⏹️ INTRUSION ENDED. (Duration: {int(recording_duration)}s)")
                intrusion_active = False
                video_writer.release()
                video_writer = None

                # Upload video to GridFS
                if video_filename:
                    upload_thread = threading.Thread(
                        target=upload_to_server, 
                        args=(os.path.join(RECORDINGS_DIR, video_filename), "video")
                    )
                    upload_thread.start()

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
