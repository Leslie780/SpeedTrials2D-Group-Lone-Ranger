# Getting token_model.onnx (YOLO Model)
## Step 1 — Collect frames
Run main_drive.py with the simulator. Frames are auto-saved to the `frames/` folder (every 10th frame, up to 500).
## Step 2 — Label on Roboflow
1. Go to https://roboflow.com and create a free account
2. New Project → Object Detection
3. Upload all images from the `frames/` folder
4. Label 3 classes: `green_token`, `red_token`, `yellow_token`
5. Use Roboflow's Smart Polygon tool to speed up labelling
6. Add augmentations: brightness ±30%, blur, noise (helps with yellow event corruptions)
## Step 3 — Train
1. Click Train → YOLOv8 → Nano (fastest)
2. Use Roboflow's free GPU
3. Training takes ~20 minutes
## Step 4 — Export
1. Deploy → Export → ONNX format
2. Download and rename to `token_model.onnx`
3. Place in the same folder as `main_drive.py`
## Step 5 — Activate
Restart `main_drive.py` — YOLO activates automatically.
Console will show: `[YOLO] Model loaded from token_model.onnx`
