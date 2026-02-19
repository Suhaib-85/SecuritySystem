import cv2
import numpy as np
import time
import os
import tflite_runtime.interpreter as tflite

# --- 1. CONFIGURATION ---
# We use os.path.join to handle Windows vs Linux slash differences automatically
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(CURRENT_DIR, "model")

MODEL_PATH = os.path.join(MODEL_DIR, "ssd_mobilenet_v2_coco_quant_postprocess.tflite")
LABEL_PATH = os.path.join(MODEL_DIR, "coco_labels.txt")
CONFIDENCE_THRESHOLD = 0.5

# --- 2. LOAD LABELS ---
print(f"Loading labels from: {LABEL_PATH}")
with open(LABEL_PATH, 'r') as f:
    # Remove whitespace and empty lines
    labels = [line.strip() for line in f.readlines() if line.strip()]

# --- 3. LOAD MODEL ---
print(f"Loading TFLite Model from: {MODEL_PATH}")
try:
    interpreter = tflite.Interpreter(model_path=MODEL_PATH)
    interpreter.allocate_tensors()
except Exception as e:
    print(f"\n❌ FATAL ERROR: Could not load model.")
    print(f"Check that the file exists at: {MODEL_PATH}")
    print(f"Python Error: {e}")
    exit(1)

# Get model details
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
height = input_details[0]['shape'][1]
width = input_details[0]['shape'][2]

# --- 4. START WEBCAM ---
cap = cv2.VideoCapture(0) # 0 is usually the default laptop webcam
if not cap.isOpened():
    print("❌ ERROR: Could not open webcam.")
    exit(1)

print(f"\n✅ VISION SYSTEM ONLINE")
print(f"Model Resolution: {width}x{height}")
print("Press 'q' to quit...")

# --- 5. MAIN LOOP ---
while True:
    start_time = time.time()
    
    # Read Frame
    ret, frame = cap.read()
    if not ret:
        print("Failed to grab frame")
        break

    # Pre-process frame (Resize & Format)
    frame_resized = cv2.resize(frame, (width, height))
    input_data = np.expand_dims(frame_resized, axis=0)

    # For quantized models, we often need uint8. For float models, we need float32.
    # This check makes the script universal.
    if input_details[0]['dtype'] == np.uint8:
        input_data = input_data.astype(np.uint8)
    else:
        # Normalize to [-1, 1] for float models
        input_data = (np.float32(input_data) - 127.5) / 127.5

    # Run Inference (The "Brain" works here)
    interpreter.set_tensor(input_details[0]['index'], input_data)
    interpreter.invoke()

    # Get Results
    # The outputs are usually: [Boxes, Classes, Scores, Count]
    boxes = interpreter.get_tensor(output_details[0]['index'])[0]
    classes = interpreter.get_tensor(output_details[1]['index'])[0]
    scores = interpreter.get_tensor(output_details[2]['index'])[0]

    # Draw Detections
    for i in range(len(scores)):
        score = scores[i]
        
        # Only show confident detections
        if score > CONFIDENCE_THRESHOLD:
            
            # Map Class ID to Label Name
            class_id = int(classes[i])
            if class_id < len(labels):
                object_name = labels[class_id]
            else:
                object_name = "Unknown"

            # 🎯 FILTER: Only draw box if it's a PERSON
            if object_name == "person":
                # Convert normalized coordinates (0 to 1) to pixel coordinates
                h, w, _ = frame.shape
                ymin, xmin, ymax, xmax = boxes[i]
                
                left = int(xmin * w)
                top = int(ymin * h)
                right = int(xmax * w)
                bottom = int(ymax * h)

                # Draw Green Box
                cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
                
                # Draw Label Background
                label_text = f"{object_name}: {int(score*100)}%"
                label_size, baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
                cv2.rectangle(frame, (left, top - label_size[1] - 10), (left + label_size[0], top), (0, 255, 0), cv2.FILLED)
                
                # Draw Label Text
                cv2.putText(frame, label_text, (left, top - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)

    # Calculate FPS
    fps = 1 / (time.time() - start_time)
    cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

    # Show the Window
    cv2.imshow('Security Feed - Press Q to Quit', frame)

    # Quit on 'q' key press
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()