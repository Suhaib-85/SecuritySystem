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
import subprocess
from dotenv import load_dotenv

# --- RASPBERRY PI HARDWARE INTERFACES ---
# Pi 5 NOTE: RPi.GPIO does NOT work on the Pi 5 (the RP1 I/O chip broke it).
# gpiozero with the lgpio backend is the correct, Pi-5-native choice.
from gpiozero import DigitalInputDevice, LED, Buzzer
from picamera2 import Picamera2

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

# --- GPIO PIN MAP (BCM numbering) ---
PIR_PIN = 4      # HC-SR501 OUT  -> physical pin 7
LED_PIN = 17     # green LED (via 330ohm) -> physical pin 11
BUZZER_PIN = 27  # buzzer trigger (via NPN transistor) -> physical pin 13

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


def _utcnow():
    """Timezone-aware UTC now, for DATA timestamps (edgeTimestamp) — the browser
    converts these to the viewer's local time for display. (datetime.utcnow() is
    deprecated on Python 3.12+, which is what Debian Trixie ships, so we avoid it.)"""
    return datetime.datetime.now(datetime.timezone.utc)


def _localnow():
    """Local wall-clock time, for human-readable FILENAMES so they match the clock
    on the wall rather than UTC. NOTE: this depends on the Pi's system timezone
    being set correctly (a fresh Pi image may default to UTC — set it with
    `sudo raspi-config` -> Localisation -> Timezone, or `sudo timedatectl
    set-timezone Asia/Karachi`)."""
    return datetime.datetime.now()


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
# On the Pi (Debian Trixie / Python 3.13) the first branch binds: ai-edge-litert
# ships a cp313 aarch64 wheel and is the maintained successor to tflite_runtime.
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


# NOTE: The Windows build used webcam frame-differencing (check_motion_detected)
# as a STAND-IN for a motion sensor. On the Pi we read the real HC-SR501 PIR
# directly from GPIO, so that function is gone — see `pir.is_active` in the loop.


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
    now = _localnow()
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
            "timestamp": _utcnow().isoformat(),
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
                "timestamp": meta.get("timestamp", _utcnow().isoformat()),
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

# GPIO peripherals (gpiozero auto-selects the lgpio pin factory on the Pi 5).
pir = DigitalInputDevice(PIR_PIN)          # HC-SR501 latched digital output
status_led = LED(LED_PIN)                  # green status LED

# The buzzer is OPTIONAL: a 2-pin buzzer needs an NPN transistor driver. Until
# that's wired, leaving BUZZER_PIN unconnected is harmless — gpiozero just
# toggles the pin. If init ever fails, we degrade to a no-op rather than crash.
try:
    buzzer = Buzzer(BUZZER_PIN)
except Exception as e:
    print(f"⚠️  BUZZER: init failed ({e}) — running without audible alert.")
    buzzer = None

# Camera: Pi 5 + Camera Module 3 is a libcamera/CSI device, so we use picamera2,
# NOT cv2.VideoCapture(0) (which targets V4L2 webcams and won't grab the CSI cam).
picam2 = Picamera2()
# COLOR-ORDER GOTCHA: picamera2's "RGB888" yields a BGR-ordered numpy array,
# which is exactly what OpenCV (imwrite / VideoWriter) and our existing model
# pipeline expect — so frames are drop-in compatible with the Windows build and
# need NO cvtColor. (If saved stills ever look blue-tinted, this is the knob.)
video_config = picam2.create_video_configuration(
    main={"size": (1280, 720), "format": "RGB888"}
)
picam2.configure(video_config)
picam2.start()
time.sleep(1.0)  # let auto-exposure / white-balance settle before first read

# --- LOCK TRUE CAPTURE RESOLUTION ---
# Probe one real frame and reuse its actual dimensions everywhere, so the
# VideoWriter never silently drops frames over a size mismatch.
probe_frame = picam2.capture_array()
if probe_frame is not None:
    if probe_frame.shape[2] == 4:  # defensive: some modes hand back 4 channels
        probe_frame = probe_frame[:, :, :3]
    FRAME_HEIGHT, FRAME_WIDTH = probe_frame.shape[:2]
else:
    FRAME_WIDTH, FRAME_HEIGHT = 1280, 720
print(f"📐 CAPTURE RESOLUTION LOCKED AT: {FRAME_WIDTH}x{FRAME_HEIGHT}")


def create_video_writer(output_path):
    """Create a VideoWriter for recording intrusion clips.

    On the Raspberry Pi 5, H.264 (avc1) is available, so clips are browser-native
    and play INLINE in the dashboard Gallery. We try avc1 first and fall back to
    mp4v only if the local OpenCV/FFmpeg build can't open an H.264 writer.
    """
    for codec in ("avc1", "mp4v"):
        fourcc = cv2.VideoWriter_fourcc(*codec)
        writer = cv2.VideoWriter(
            output_path, fourcc, TARGET_FPS, (FRAME_WIDTH, FRAME_HEIGHT)
        )
        if writer.isOpened():
            return writer
        writer.release()
    print(f"❌ CODEC: Could not initialize any VideoWriter for {output_path}")
    return None


def faststart_fixup(filepath):
    """Relocate the MP4 moov atom to the file's start so browsers can begin
    playback immediately without downloading the entire file first.

    OpenCV's VideoWriter writes moov at the END (it can't know the total frame
    count until recording finishes). This post-process uses FFmpeg's
    -movflags +faststart to remux the file (no re-encoding, just rearranges
    bytes) — typically takes under a second for a 60s clip.

    Best-effort: if FFmpeg isn't installed or the remux fails, the original file
    is left untouched. The video still plays, just with a slightly delayed start
    in the browser.
    """
    tmp_path = filepath + ".faststart.mp4"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", filepath,
                "-c", "copy",
                "-movflags", "+faststart",
                tmp_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if result.returncode == 0 and os.path.exists(tmp_path):
            os.replace(tmp_path, filepath)  # atomic on same filesystem
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass  # FFmpeg not available or failed — original file is fine
    finally:
        # Clean up the temp file if it exists (e.g. failed remux left a partial)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


# --- LED STATE MACHINE ---
# Desired behaviour:
#   disarmed              -> off
#   armed, idle           -> solid on
#   armed, intrusion live -> blink (~0.25s)
#   intrusion just ended  -> off for 3s, then back to solid (if still armed)
# We only issue a gpiozero call when the mode CHANGES, so blink() isn't restarted
# every loop (which would make it stutter). blink/on/off are all non-blocking.
_led_mode = None


def update_led():
    global _led_mode
    if not is_system_armed:
        mode = "off"
    elif intrusion_active:
        mode = "blink"
    elif (time.time() - last_intrusion_end) < 3:
        mode = "off"
    else:
        mode = "solid"

    if mode == _led_mode:
        return
    if mode == "off":
        status_led.off()
    elif mode == "solid":
        status_led.on()
    elif mode == "blink":
        status_led.blink(on_time=0.25, off_time=0.25)
    _led_mode = mode


def chirp():
    """Brief audible alert when a threat is verified (no-op if buzzer absent)."""
    if buzzer is not None:
        try:
            buzzer.beep(on_time=0.1, off_time=0.05, n=2, background=True)
        except Exception:
            pass


print("\n📷 CORE ONLINE: Hardware capture loops engaged. (Ctrl+C to stop)")

ai_window_counter = 0

# --- MAIN SYSTEM STATE LOOPS ---
try:
    while True:
        loop_start = time.time()
        current_time = time.time()

        # LED reflects system state on EVERY iteration — placed before the
        # arm-delay / cooldown gates below so it keeps updating even when those
        # gates `continue` and skip the rest of the loop.
        update_led()

        try:
            frame = picam2.capture_array()
        except Exception as e:
            print(f"Camera capture exception ({e}), retrying capture sequence...")
            time.sleep(0.5)
            continue
        if frame is None:
            time.sleep(0.1)
            continue
        if frame.shape[2] == 4:  # normalise to 3 channels if needed
            frame = frame[:, :, :3]

        # Enforce systemic delay filters to handle initialization or cool-down transitions cleanly
        if current_time - arm_timestamp < ARM_DELAY:
            continue
        if current_time - last_intrusion_end < INTRUSION_COOLDOWN:
            continue

        # Real PIR read (replaces the Windows frame-differencing stand-in).
        pir_triggered = pir.is_active

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

                    chirp()  # brief audible alert on verified threat

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
                        now_str = _localnow().strftime("%H%M%S")
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
                    faststart_fixup(old_video_path)
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
                        faststart_fixup(os.path.join(RECORDINGS_DIR, video_filename))
                        save_to_pending(
                            os.path.join(RECORDINGS_DIR, video_filename),
                            "video",
                            intrusion_session_id,
                        )

        processing_time = time.time() - loop_start
        sleep_duration = FRAME_INTERVAL - processing_time
        if sleep_duration > 0:
            time.sleep(sleep_duration)

except KeyboardInterrupt:
    print("\n🛑 SHUTDOWN: Ctrl+C received — releasing hardware cleanly...")
finally:
    try:
        if video_writer:
            video_writer.release()
    except Exception:
        pass
    try:
        picam2.stop()
    except Exception:
        pass
    for dev in (status_led, pir, buzzer):
        try:
            if dev is not None:
                dev.close()
        except Exception:
            pass
    print("✅ SHUTDOWN: Hardware released. Goodbye.")
