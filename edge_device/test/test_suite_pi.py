import sys
import os
import time

print("==================================================")
print("🚀 RASPBERRY PI EDGE VERIFICATION SUITE")
print("==================================================")
print("Every check is isolated: a failure in one is recorded")
print("and the suite continues, so you always see the full picture.")
print("==================================================")


# ============================================================
# BULLETPROOF RUNNER
# ------------------------------------------------------------
# Every check — logic or hardware — flows through this. It catches
# ANY exception (not just the ones a check anticipates), records the
# result, and guarantees the next check still runs. A check signals
# failure by returning False OR by raising; either way it's contained.
# KeyboardInterrupt is deliberately NOT caught, so Ctrl+C still exits.
# ============================================================
_results = []  # list of (name, passed: bool, detail: str)


def run_check(name, fn):
    try:
        outcome = fn()
        passed = outcome is not False  # return False to fail; None/True passes
        _results.append((name, passed, "" if passed else "returned failure"))
        print(f"   {'✅' if passed else '❌'} {name}")
    except AssertionError as e:
        _results.append((name, False, str(e)))
        print(f"   ❌ {name}: {e}")
    except Exception as e:
        # Any unexpected error is contained here — the suite marches on.
        _results.append((name, False, f"{type(e).__name__}: {e}"))
        print(f"   ❌ {name}: unexpected {type(e).__name__}: {e}")


# ============================================================
# PART A — DECISION-LOGIC CHECKS (hardware-independent)
# ------------------------------------------------------------
# Pure simulation of the main-loop decision tree (ported from
# test_suite.py). No camera/GPIO/network — validates FR-level
# behaviour of the intrusion state machine.
# ============================================================
class MockSecuritySystem:
    def __init__(self):
        self.CONFIDENCE_THRESHOLD = 0.50
        self.MAX_VIDEO_LENGTH = 60
        self.INTRUSION_COOLDOWN = 5

        self.is_system_armed = True
        self.intrusion_active = False
        self.person_detection_counter = 0
        self.PERSON_CONFIRM_FRAMES = 5

        self.intrusion_start_time = 0
        self.last_motion_time = 0
        self.last_ai_recheck_time = 0
        self.session_chunk_counter = 1
        self.intrusion_session_id = ""

        self.alerts_emitted = []
        self.files_queued = []

    def run_frame_cycle(self, current_time, pir_triggered, ai_person_detected, ai_confidence):
        # FR-02: disarmed -> bypass all processing
        if not self.is_system_armed:
            return "Motion ignored (System Disarmed)"

        if self.is_system_armed:
            if not self.intrusion_active and pir_triggered:
                # FR-04 / FR-05: AI inference for human presence
                if ai_person_detected and ai_confidence >= self.CONFIDENCE_THRESHOLD:
                    self.person_detection_counter += 1
                else:
                    self.person_detection_counter = 0

                # FR-06: confirm intrusion after threshold frames
                if self.person_detection_counter >= self.PERSON_CONFIRM_FRAMES:
                    self.intrusion_active = True
                    self.intrusion_start_time = current_time
                    self.last_motion_time = current_time
                    self.last_ai_recheck_time = current_time
                    self.intrusion_session_id = f"mock_{int(current_time)}"
                    self.alerts_emitted.append(
                        {"event": "pi_alert", "sessionId": self.intrusion_session_id}
                    )  # FR-11
                    self.files_queued.append(
                        f"evidence_{self.intrusion_session_id}_start.jpg"
                    )  # FR-08
                    return "INTRUSION_STARTED"

            elif self.intrusion_active:
                if pir_triggered:
                    self.last_motion_time = current_time

                recording_duration = current_time - self.intrusion_start_time
                time_since_motion = current_time - self.last_motion_time

                # FR-09: SCENARIO A — 60s chunk rollover
                if recording_duration > self.MAX_VIDEO_LENGTH:
                    self.files_queued.append(
                        f"evidence_{self.intrusion_session_id}_pt{self.session_chunk_counter}.mp4"
                    )
                    self.session_chunk_counter += 1
                    self.intrusion_start_time = current_time
                    return "CHUNK_ROLLOVER"

                # SCENARIO B — threat cleared (no motion for 20s)
                if time_since_motion > 20:
                    self.intrusion_active = False
                    self.files_queued.append(
                        f"evidence_{self.intrusion_session_id}_pt{self.session_chunk_counter}.mp4"
                    )
                    return "INTRUSION_CLEARED"

        return "IDLE_MONITORING"


def logic_transient_threat():
    """Profile 1 — FR-03/04/05/06/08/11: 5 confirmed frames -> intrusion + alert + thumbnail."""
    s = MockSecuritySystem()
    for tick in range(1, 6):
        s.run_frame_cycle(tick, True, True, 0.85)
    assert s.intrusion_active is True, "intrusion state should be active"
    assert len(s.alerts_emitted) == 1, "real-time socket alert was not emitted"
    assert "evidence_mock_5_start.jpg" in s.files_queued, "initial thumbnail not queued"


def logic_rollover():
    """Profile 2 — FR-08/09/10: clock past 60s forces a seamless chunk rollover."""
    s = MockSecuritySystem()
    for tick in range(1, 6):
        s.run_frame_cycle(tick, True, True, 0.90)
    status = s.run_frame_cycle(67, True, True, 0.90)
    assert status == "CHUNK_ROLLOVER", "rollover handler did not intercept boundary"
    assert "evidence_mock_5_pt1.mp4" in s.files_queued, "original chunk missed in pipeline"
    assert s.session_chunk_counter == 2, "secondary write index not updated"


def logic_false_positive():
    """Profile 3 — FR-05/07, NFR-06: non-human motion raises no alert, writes nothing."""
    s = MockSecuritySystem()
    for tick in range(1, 10):
        s.run_frame_cycle(tick, True, False, 0.0)
    assert s.intrusion_active is False, "false positive triggered an invalid intrusion"
    assert len(s.alerts_emitted) == 0, "invalid alert sent for unverified threat"
    assert len(s.files_queued) == 0, "media written for a false alarm"


def logic_privacy_lockdown():
    """Profile 4 — FR-01/02: disarmed system ignores motion and records nothing."""
    s = MockSecuritySystem()
    s.is_system_armed = False
    status = s.run_frame_cycle(1, True, True, 0.99)
    assert "Motion ignored" in status, "failed to bypass processing in privacy mode"
    assert s.intrusion_active is False, "threat routine ran while disarmed"
    assert len(s.files_queued) == 0, "data leak: media recorded while disarmed"


# ============================================================
# PART B — HARDWARE SELF-CHECKS (Pi only)
# ============================================================
def _model_paths():
    base = os.path.dirname(os.path.abspath(__file__))
    return (
        os.path.join(base, "..", "model", "ssd_mobilenet_v2_coco_quant_postprocess.tflite"),
        os.path.join(base, "..", "model", "coco_labels.txt"),
    )


def hw_model_files():
    """Model + labels present on disk."""
    model, labels = _model_paths()
    assert os.path.exists(model), f"missing {model}"
    assert os.path.exists(labels), f"missing {labels}"


def hw_ai_runtime():
    """AI runtime imports and loads the model."""
    try:
        import ai_edge_litert.interpreter as lr
        interp_cls, backend = lr.Interpreter, "ai-edge-litert"
    except ImportError:
        import tflite_runtime.interpreter as tf
        interp_cls, backend = tf.Interpreter, "tflite_runtime"
    model, _ = _model_paths()
    interp = interp_cls(model_path=model, num_threads=2)
    interp.allocate_tensors()
    print(f"      ({backend} loaded + tensors allocated)")


def hw_camera():
    """picamera2 captures one real frame."""
    from picamera2 import Picamera2
    cam = Picamera2()
    cam.configure(cam.create_video_configuration(main={"size": (1280, 720), "format": "RGB888"}))
    cam.start()
    time.sleep(1)
    frame = cam.capture_array()
    cam.stop()
    cam.close()
    assert frame is not None and frame.shape[0] > 0 and frame.shape[1] > 0, "empty frame"
    print(f"      (captured {frame.shape[1]}x{frame.shape[0]})")


def hw_gpio():
    """PIR readable, LED visibly pulsed."""
    from gpiozero import DigitalInputDevice, LED
    pir = DigitalInputDevice(4)
    led = LED(17)
    led.on(); time.sleep(0.3); led.off()  # visible confirmation
    state = pir.is_active
    pir.close(); led.close()
    print(f"      (PIR currently {'HIGH' if state else 'LOW'}, LED pulsed)")


def hw_server():
    """Backend reachable at SERVER_URL from this device."""
    from dotenv import load_dotenv
    load_dotenv()
    url = os.getenv("SERVER_URL", "http://localhost:3000")
    import requests
    r = requests.get(url, timeout=5)
    print(f"      ({url} -> HTTP {r.status_code})")
    assert r.status_code < 500, f"server returned {r.status_code}"


# ============================================================
# ORCHESTRATION
# ============================================================
if __name__ == "__main__":
    print("\n--- PART A: DECISION-LOGIC CHECKS ---")
    run_check("Logic: transient threat", logic_transient_threat)
    run_check("Logic: chunk rollover", logic_rollover)
    run_check("Logic: false-positive rejection", logic_false_positive)
    run_check("Logic: privacy lockdown", logic_privacy_lockdown)

    print("\n--- PART B: HARDWARE SELF-CHECKS ---")
    # Insurance: if the Pi libraries aren't importable (e.g. run outside the
    # venv), skip Part B with a clear note instead of failing every check.
    try:
        import picamera2  # noqa: F401
        import gpiozero   # noqa: F401
        pi_libs = True
    except ImportError:
        pi_libs = False

    if not pi_libs:
        print("   ⏭️  SKIPPED: picamera2/gpiozero not importable.")
        print("      (Are you in the venv? `source .venv/bin/activate`)")
    else:
        run_check("Hardware: model files", hw_model_files)
        run_check("Hardware: AI runtime", hw_ai_runtime)
        run_check("Hardware: camera", hw_camera)
        run_check("Hardware: GPIO (PIR + LED)", hw_gpio)
        run_check("Hardware: server reachability", hw_server)

    # ---- Summary (always reached) ----
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    failed = [r for r in _results if not r[1]]
    for name, ok, detail in _results:
        line = f"  {'✅' if ok else '❌'} {name}"
        if not ok and detail:
            line += f"  —  {detail}"
        print(line)
    print("-" * 50)
    print(f"  {len(_results) - len(failed)}/{len(_results)} checks passed.")
    print("=" * 50)

    sys.exit(1 if failed else 0)
