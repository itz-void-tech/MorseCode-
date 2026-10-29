
import time
import cv2
import numpy as np
import mediapipe as mp

# MediaPipe Face Mesh eye landmark indices
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D",
    ".": "E", "..-.": "F", "--.": "G", "....": "H",
    "..": "I", ".---": "J", "-.-": "K", ".-..": "L",
    "--": "M", "-.": "N", "---": "O", ".--.": "P",
    "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2",
    "...--": "3", "....-": "4", ".....": "5",
    "-....": "6", "--...": "7", "---..": "8",
    "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?",
    "-.-.--": "!", "-....-": "-"
}

# Adjust these after testing.
MIN_CLOSURE = 0.16
DOT_MAX = 0.45
DASH_MAX = 1.50
LETTER_GAP = 1.20
WORD_GAP = 2.50
CALIBRATION_SECONDS = 2.0
CLOSE_CONFIRM = 0.06
OPEN_CONFIRM = 0.05


def distance(a, b):
    return float(np.linalg.norm(
        np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    ))


def eye_ratio(points, indices, width, height):
    p = [
        (points[i].x * width, points[i].y * height)
        for i in indices
    ]

    vertical_a = distance(p[1], p[5])
    vertical_b = distance(p[2], p[4])
    horizontal = distance(p[0], p[3])

    if horizontal < 1e-6:
        return None

    return (vertical_a + vertical_b) / (2.0 * horizontal)


def decode(buffer):
    return MORSE.get(buffer, "?")


def main():
    # CAP_DSHOW often initializes Windows webcams more reliably.
    camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)

    if not camera.isOpened():
        camera.release()
        camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("ERROR: Webcam unavailable.")
        print("Close other camera applications and check Windows camera permissions.")
        return

    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)

    open_samples = []
    closed_samples = []
    calibration_mode = None
    calibration_start = 0.0

    open_ear = None
    closed_ear = None
    threshold = None

    smoothed_ear = None
    smoothing = 0.35

    face_detected = False
    eye_closed = False
    close_candidate = None
    open_candidate = None
    closure_start = None

    morse_buffer = ""
    message = ""
    last_signal_time = None
    word_space_added = False

    status = "Press C to calibrate OPEN eyes."
    last_event = "None"

    try:
        with mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.45,
            min_tracking_confidence=0.45
        ) as mesh:

            while True:
                ok, frame = camera.read()
                if not ok:
                    status = "Camera frame unavailable."
                    break

                frame = cv2.flip(frame, 1)
                height, width = frame.shape[:2]
                now = time.monotonic()

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = mesh.process(rgb)

                face_detected = bool(result.multi_face_landmarks)
                ear = None

                if face_detected:
                    points = result.multi_face_landmarks[0].landmark

                    left = eye_ratio(points, LEFT_EYE, width, height)
                    right = eye_ratio(points, RIGHT_EYE, width, height)

                    if left is not None and right is not None:
                        raw_ear = (left + right) / 2.0

                        if smoothed_ear is None:
                            smoothed_ear = raw_ear
                        else:
                            smoothed_ear = (
                                smoothing * raw_ear
                                + (1.0 - smoothing) * smoothed_ear
                            )

                        ear = smoothed_ear

                        # Draw the six measurement points around each eye.
                        for index in LEFT_EYE + RIGHT_EYE:
                            x = int(points[index].x * width)
                            y = int(points[index].y * height)
                            cv2.circle(
                                frame, (x, y), 2, (60, 230, 170), -1
                            )

                        # Collect samples for the selected calibration stage.
                        if calibration_mode and ear is not None:
                            target = (
                                open_samples
                                if calibration_mode == "OPEN"
                                else closed_samples
                            )

                            target.append(ear)

                            elapsed = now - calibration_start

                            if elapsed >= CALIBRATION_SECONDS:
                                if len(target) >= 10:
                                    if calibration_mode == "OPEN":
                                        open_ear = float(np.median(target))
                                        status = (
                                            f"Open-eye baseline saved: "
                                            f"{open_ear:.3f}. Now press K."
                                        )
                                    else:
                                        closed_ear = float(np.median(target))

                                        if open_ear is not None:
                                            separation = open_ear - closed_ear

                                            if separation >= 0.025:
                                                threshold = (
                                                    open_ear + closed_ear
                                                ) / 2.0
                                                status = (
                                                    "Calibration successful. "
                                                    f"Threshold={threshold:.3f}"
                                                )
                                            else:
                                                threshold = None
                                                status = (
                                                    "Calibration failed: "
                                                    "open and closed measurements "
                                                    "are too similar. Retry C then K."
                                                )
                                        else:
                                            status = "Press C first to calibrate open eyes."

                                else:
                                    status = (
                                        "Not enough samples. Improve lighting "
                                        "and retry calibration."
                                    )

                                calibration_mode = None

                        # Blink detection only runs after both calibrations.
                        if threshold is not None and calibration_mode is None:
                            looks_closed = ear < threshold

                            if not eye_closed:
                                if looks_closed:
                                    if close_candidate is None:
                                        close_candidate = now
                                    elif now - close_candidate >= CLOSE_CONFIRM:
                                        eye_closed = True
                                        closure_start = close_candidate
                                        open_candidate = None
                                else:
                                    close_candidate = None

                            else:
                                if not looks_closed:
                                    if open_candidate is None:
                                        open_candidate = now
                                    elif now - open_candidate >= OPEN_CONFIRM:
                                        duration = max(
                                            0.0, open_candidate - closure_start
                                        )

                                        eye_closed = False
                                        close_candidate = None
                                        open_candidate = None
                                        closure_start = None

                                        if MIN_CLOSURE <= duration <= DOT_MAX:
                                            morse_buffer += "."
                                            last_event = f"DOT {duration:.2f}s"
                                            last_signal_time = now
                                            word_space_added = False
                                            status = "Dot registered."

                                        elif DOT_MAX < duration <= DASH_MAX:
                                            morse_buffer += "-"
                                            last_event = f"DASH {duration:.2f}s"
                                            last_signal_time = now
                                            word_space_added = False
                                            status = "Dash registered."

                                        elif duration < MIN_CLOSURE:
                                            status = (
                                                f"Ignored short closure "
                                                f"({duration:.2f}s)"
                                            )
                                        else:
                                            status = (
                                                "Closure too long; signal ignored."
                                            )

                            # Commit a letter once after a pause.
                            if not eye_closed and last_signal_time is not None:
                                gap = now - last_signal_time

                                if gap >= LETTER_GAP and morse_buffer:
                                    letter = decode(morse_buffer)
                                    message += letter
                                    status = f"Decoded letter: {letter}"
                                    morse_buffer = ""

                                # A longer pause adds a word boundary once.
                                if gap >= WORD_GAP and not word_space_added:
                                    if message and not message.endswith(" "):
                                        message += " "
                                    word_space_added = True

                else:
                    # Do not interpret tracking loss as a blink.
                    close_candidate = None
                    open_candidate = None
                    if not calibration_mode:
                        status = (
                            "FACE NOT TRACKED — face the camera, "
                            "improve lighting, and keep both eyes visible."
                        )

                # Draw dashboard overlay.
                panel = frame.copy()
                cv2.rectangle(
                    panel, (0, 0), (width, 165), (22, 28, 36), -1
                )
                frame = cv2.addWeighted(panel, 0.88, frame, 0.12, 0)

                def label(text, x, y, scale=0.55, color=(235, 235, 235)):
                    cv2.putText(
                        frame, text, (x, y),
                        cv2.FONT_HERSHEY_SIMPLEX, scale,
                        color, 1, cv2.LINE_AA
                    )

                label("EYECODE | LIVE EYE MORSE", 18, 28, 0.75, (80, 230, 180))
                label(
                    "Face: " + ("TRACKED" if face_detected else "NOT TRACKED"),
                    18, 57, 0.55,
                    (80, 230, 180) if face_detected else (60, 80, 255)
                )
                label(
                    f"EAR: {ear:.3f}" if ear is not None else "EAR: --",
                    250, 57
                )
                label(
                    f"Open: {open_ear:.3f}" if open_ear is not None else "Open: --",
                    420, 57
                )
                label(
                    f"Closed: {closed_ear:.3f}" if closed_ear is not None else "Closed: --",
                    580, 57
                )
                label(
                    f"Threshold: {threshold:.3f}" if threshold is not None else "Threshold: not calibrated",
                    18, 88
                )
                label(
                    f"Eye: {'CLOSED' if eye_closed else 'OPEN'}",
                    300, 88, 0.55,
                    (70, 190, 255) if eye_closed else (80, 230, 180)
                )
                label(f"Morse: {morse_buffer or '(empty)'}", 18, 119, 0.65)
                label(f"Last: {last_event}", 250, 119, 0.55)
                label(f"Text: {message[-55:]}", 18, 151, 0.60)

                label(status[:115], 18, height - 48, 0.46, (90, 230, 180))
                label(
                    "C=open calibration | K=closed calibration | "
                    "SPACE=commit | ENTER=word | BACKSPACE=delete | R=reset | Q=quit",
                    18, height - 18, 0.38
                )

                cv2.imshow("EyeCode", frame)
                key = cv2.waitKey(1) & 0xFF

                if key in (ord("q"), ord("Q"), 27):
                    break

                elif key in (ord("c"), ord("C")):
                    open_samples.clear()
                    open_ear = None
                    closed_ear = None
                    threshold = None
                    calibration_mode = "OPEN"
                    calibration_start = now
                    status = "Keep eyes naturally OPEN for two seconds."

                elif key in (ord("k"), ord("K")):
                    if open_ear is None:
                        status = "First press C and complete open-eye calibration."
                    else:
                        closed_samples.clear()
                        closed_ear = None
                        threshold = None
                        calibration_mode = "CLOSED"
                        calibration_start = now
                        status = "Gently close eyes for two seconds."

                elif key == 32:  # Space
                    if morse_buffer:
                        message += decode(morse_buffer)
                        morse_buffer = ""
                        last_signal_time = None
                        status = "Character committed."

                elif key in (10, 13):  # Enter
                    if morse_buffer:
                        message += decode(morse_buffer)
                        morse_buffer = ""

                    if message and not message.endswith(" "):
                        message += " "

                    last_signal_time = None
                    word_space_added = True
                    status = "Word boundary added."

                elif key in (8, 127):  # Backspace
                    if morse_buffer:
                        morse_buffer = morse_buffer[:-1]
                    elif message:
                        message = message[:-1]

                    status = "Last symbol or character deleted."

                elif key in (ord("r"), ord("R")):
                    message = ""
                    morse_buffer = ""
                    last_signal_time = None
                    word_space_added = False
                    status = "Message reset."

    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
