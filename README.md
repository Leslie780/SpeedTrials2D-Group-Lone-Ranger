# SpeedTrials2D — Group Lone Ranger
SECJ 4423 Real-Time Software Engineering | UTM 2026

## Setup

Download and extract the simulator from: https://github.com/MokhtarOuardi/Hack2Drive_2026/releases

Place the SpeedTrials2D/ folder inside RTSE_Phase_1_V1.0 (1)/RTSE_Phase_1_V1.0/

Install dependencies: pip install -r requirements.txt

Launch the simulator: SpeedTrials2D.exe

Run the agent: python main_drive.py

## RTOS Task Architecture
| Task | Period | Priority | Job |
|------|--------|----------|-----|
| ReadFrontCamera | 0.005s | HIGH | Capture front camera frame |
| ProcessingTask | 0.005s | MEDIUM | Token detection (OpenCV HSV) |
| DecisionTask | 0.010s | MEDIUM | Steering + acceleration logic |
| SendControlsTask | 0.005s | HIGH | Send commands to simulator |
| MonitorTask | 0.500s | LOW | Live status logging |

## File Structure

token_detector.py — HSV-based token and brightness detection

main_drive.py — Main RTOS driving agent

test_detector.py — Offline detector test (no simulator needed)

sample_drive.py — Original starter script (do not modify)