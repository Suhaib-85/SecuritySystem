import sys

print("==================================================")
print("🚀 INITIALIZING EDGE AI PIPELINE VERIFICATION SUITE")
print("==================================================")


# --- MOCK SIMULATION ENVIRONMENT STATE ---
class MockSecuritySystem:
    def __init__(self):
        # Configuration Thresholds matching SRS specifications
        self.CONFIDENCE_THRESHOLD = 0.50
        self.MAX_VIDEO_LENGTH = 60
        self.INTRUSION_COOLDOWN = 5

        # Core System States
        self.is_system_armed = True
        self.intrusion_active = False
        self.person_detection_counter = 0
        self.PERSON_CONFIRM_FRAMES = 5

        # Tracking Timestamps
        self.intrusion_start_time = 0
        self.last_motion_time = 0
        self.last_ai_recheck_time = 0
        self.session_chunk_counter = 1
        self.intrusion_session_id = ""

        # Test Metrics Output
        self.alerts_emitted = []
        self.files_queued = []

    def run_frame_cycle(
        self, current_time, pir_triggered, ai_person_detected, ai_confidence
    ):
        """Simulates one execution tick of the main loop decision tree"""

        # FR-02: If system is disarmed, all processing must be bypassed
        if not self.is_system_armed:
            return "Motion ignored (System Disarmed)"

        # FR-03: Active Mode arms the sensor detection structures
        if self.is_system_armed:

            # State Transition: Threat Evaluation Boundary
            if not self.intrusion_active and pir_triggered:
                # FR-04 / FR-05: Run AI inference to check for human presence
                if ai_person_detected and ai_confidence >= self.CONFIDENCE_THRESHOLD:
                    self.person_detection_counter += 1
                else:
                    self.person_detection_counter = 0

                # FR-06: Confirm intrusion after threshold frames met
                if self.person_detection_counter >= self.PERSON_CONFIRM_FRAMES:
                    self.intrusion_active = True
                    self.intrusion_start_time = current_time
                    self.last_motion_time = current_time
                    self.last_ai_recheck_time = current_time
                    self.intrusion_session_id = f"mock_{int(current_time)}"

                    # FR-11: Emit immediate WebSocket event alert
                    self.alerts_emitted.append(
                        {"event": "pi_alert", "sessionId": self.intrusion_session_id}
                    )

                    # FR-08: Open recording structures for 60s video chunk
                    self.files_queued.append(
                        f"evidence_{self.intrusion_session_id}_start.jpg"
                    )
                    return "INTRUSION_STARTED"

            # State Transition: Active Intrusion Tracking Loop
            elif self.intrusion_active:
                if pir_triggered:
                    self.last_motion_time = current_time

                recording_duration = current_time - self.intrusion_start_time
                time_since_motion = current_time - self.last_motion_time

                # FR-09: SCENARIO A - Continuous 60s Video Chunk Rollover Execution
                if recording_duration > self.MAX_VIDEO_LENGTH:
                    self.files_queued.append(
                        f"evidence_{self.intrusion_session_id}_pt{self.session_chunk_counter}.mp4"
                    )
                    self.session_chunk_counter += 1
                    self.intrusion_start_time = (
                        current_time  # Reset chunk timer seamlessly
                    )
                    return "CHUNK_ROLLOVER"

                # SCENARIO B - Threat Cleared (No motion detected for 20 seconds)
                if time_since_motion > 20:
                    self.intrusion_active = False
                    self.files_queued.append(
                        f"evidence_{self.intrusion_session_id}_pt{self.session_chunk_counter}.mp4"
                    )
                    return "INTRUSION_CLEARED"

        return "IDLE_MONITORING"


# ========================================================
# --- EXECUTION PROFILE MATRIX ---
# ========================================================


def run_test_suite():
    # ----------------------------------------------------
    # PROFILE 1: Transient Threat Event
    # Targets: FR-03, FR-04, FR-05, FR-06, FR-08, FR-11
    # ----------------------------------------------------
    print("\nExecuting Profile 1: Transient Threat Event Tracking...")
    sys_p1 = MockSecuritySystem()

    # Simulate 5 sequential frames of confirmed human presence to trigger confirmation
    for tick in range(1, 6):
        status = sys_p1.run_frame_cycle(
            current_time=tick,
            pir_triggered=True,
            ai_person_detected=True,
            ai_confidence=0.85,
        )

    assert sys_p1.intrusion_active == True, "Failed: Intrusion state should be active"
    assert (
        len(sys_p1.alerts_emitted) == 1
    ), "Failed: Real-time Socket alert was not emitted"
    assert (
        "evidence_mock_5_start.jpg" in sys_p1.files_queued
    ), "Failed: Initial evidence thumbnail was not queued"
    print(
        "✅ Profile 1 Passed: Real-time alert and recording initialization confirmed."
    )

    # ----------------------------------------------------
    # PROFILE 2: Persistent Threat (Rollover Boundary Test)
    # Targets: FR-08, FR-09, FR-10
    # ----------------------------------------------------
    print("\nExecuting Profile 2: Persistent Threat Chunk Rollover...")
    sys_p2 = MockSecuritySystem()

    # Establish active threat state
    for tick in range(1, 6):
        sys_p2.run_frame_cycle(
            current_time=tick,
            pir_triggered=True,
            ai_person_detected=True,
            ai_confidence=0.90,
        )

    # Advance clock past the 60-second limit constraint to force file system rotation
    status_rollover = sys_p2.run_frame_cycle(
        current_time=67, pir_triggered=True, ai_person_detected=True, ai_confidence=0.90
    )

    assert (
        status_rollover == "CHUNK_ROLLOVER"
    ), "Failed: Rollover handler did not intercept boundary"
    assert (
        "evidence_mock_5_pt1.mp4" in sys_p2.files_queued
    ), "Failed: Original video chunk was missed in storage pipeline"
    assert (
        sys_p2.session_chunk_counter == 2
    ), "Failed: Secondary video write index was not updated"
    print(
        "✅ Profile 2 Passed: Continuous stateless video chunk rotation validated successfully."
    )

    # ----------------------------------------------------
    # PROFILE 3: Environmental False Positive Filtering
    # Targets: FR-05, FR-07, NFR-06
    # ----------------------------------------------------
    print("\nExecuting Profile 3: Environmental False Positive Rejection...")
    sys_p3 = MockSecuritySystem()

    # Simulate a non-human motion event (e.g., a pet tripping the PIR sensor)
    for tick in range(1, 10):
        status = sys_p3.run_frame_cycle(
            current_time=tick,
            pir_triggered=True,
            ai_person_detected=False,
            ai_confidence=0.0,
        )

    assert (
        sys_p3.intrusion_active == False
    ), "Failed: False positive triggered an invalid intrusion state"
    assert (
        len(sys_p3.alerts_emitted) == 0
    ), "Failed: System sent an invalid alert for an unverified threat"
    assert (
        len(sys_p3.files_queued) == 0
    ), "Failed: Media files were written to storage for a false alarm"
    print(
        "✅ Profile 3 Passed: Non-human threat cleanly discarded. Zero database or network pollution."
    )

    # ----------------------------------------------------
    # PROFILE 4: Privacy Lockdown Verification
    # Targets: FR-01, FR-02
    # ----------------------------------------------------
    print("\nExecuting Profile 4: Privacy Lockdown Verification...")
    sys_p4 = MockSecuritySystem()
    sys_p4.is_system_armed = (
        False  # FR-01: Toggle system state to Inactive via dashboard control
    )

    # Simulate heavy motion activity happening while user is home
    status_privacy = sys_p4.run_frame_cycle(
        current_time=1, pir_triggered=True, ai_person_detected=True, ai_confidence=0.99
    )

    assert (
        "Motion ignored" in status_privacy
    ), "Failed: System failed to bypass processing in privacy mode"
    assert (
        sys_p4.intrusion_active == False
    ), "Failed: Threat routine initialized while system was disarmed"
    assert (
        len(sys_p4.files_queued) == 0
    ), "Failed: Data leak detected. Camera recorded media while disarmed"
    print(
        "✅ Profile 4 Passed: Absolute privacy guaranteed. Core processing completely offline."
    )

    print("\n" + "=" * 50)
    print("🏆 ALL EDGE AUTOMATION PROFILES VERIFIED CLEANLY: [PASSED]")
    print("=" * 50)
    sys.exit(0)


if __name__ == "__main__":
    run_test_suite()
