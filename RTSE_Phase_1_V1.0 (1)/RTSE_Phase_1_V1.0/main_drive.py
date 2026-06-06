"""
# ---------------------------------------------------------
# RTOS Task Table
# ---------------------------------------------------------
# Task Name         Period   Priority    Job
# ReadFrontCamera   0.005s   HIGH        Reads front camera stream to shared_data
# ProcessingTask    0.005s   MEDIUM      Runs detect_tokens + detect_brightness
# DecisionTask      0.010s   MEDIUM      Computes steering + acceleration from tokens
# SendControlsTask  0.005s   HIGH        Sends steering/acceleration via socket
# MonitorTask       0.5s     LOW         Prints status: frame count, speed, tokens seen
# ---------------------------------------------------------
"""

import socket
import threading
import struct
import cv2
import numpy as np
import time
import select
import ctypes

from yolo_detector import detect_tokens, get_lane, detect_brightness, annotate_frame

# Steering convention (from sample_drive.py): -1.0 = LEFT, +1.0 = RIGHT

# Configuration
CAMERA_HOST = '127.0.0.1'
FRONT_CAMERA_PORT = 8080
CONTROL_HOST = '127.0.0.1'
CONTROL_PORT = 8081

# Shared Resources
shared_data = {
    'running': True,
    'latest_front_frame': None,
    'tokens': [],
    'brightness': 255.0,
    'lights_on': False,
    'steering': 0.0,
    'acceleration': 1.0,
    'frame_count': 0,
    # Per-decision cooldown timestamps (time.perf_counter)
    'last_red_steer_time': 0.0,    # cooldowns: 0.35s (ahead) / 0.25s (side)
    'last_green_steer_time': 0.0,  # cooldown 0.2s
    'last_yellow_steer_time': 0.0, # cooldown 0.4s
    'red_cx_history': [],          # list of last 10 red token cx values
    'event_trailing_car': False,   # set True when back camera detects approaching car
    'event_police': False,         # set True externally (future: back camera detection)
    'event_low_brightness': False, # set True when brightness < 60
    'frame_save_count': 0,
    'last_frame_save_time': 0.0,
}
data_lock = threading.Lock()

# Sockets
front_camera_sock = None
control_conn = None

class TaskPriority:
    HIGH = 1
    MEDIUM = 2
    LOW = 3

class RTTask(threading.Thread):
    def __init__(self, name, period, priority, execute_func):
        super().__init__()
        self.name = name
        self.period = period
        self.priority = priority
        self.execute_func = execute_func
        self.daemon = True

    def run(self):
        print(f"[{self.name}] Started | Period: {self.period}s | Priority: {self.priority}")
        try:
            handle = ctypes.windll.kernel32.GetCurrentThread()
            if self.priority == TaskPriority.HIGH:
                ctypes.windll.kernel32.SetThreadPriority(handle, 2)
            elif self.priority == TaskPriority.MEDIUM:
                ctypes.windll.kernel32.SetThreadPriority(handle, 0)
            elif self.priority == TaskPriority.LOW:
                ctypes.windll.kernel32.SetThreadPriority(handle, -2)
        except Exception:
            pass

        while shared_data.get('running', True):
            start_time = time.time()
            self.execute_func()
            exec_time = time.time() - start_time
            sleep_time = self.period - exec_time
            
            if sleep_time > 0:
                time.sleep(sleep_time)

def setup_cameras():
    global front_camera_sock
    print("Connecting to Front Camera...")
    while shared_data.get('running', True) and front_camera_sock is None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect((CAMERA_HOST, FRONT_CAMERA_PORT))
            front_camera_sock = s
            print("Connected to Front Camera successfully.")
        except Exception:
            time.sleep(1)

def setup_control_server():
    global control_conn
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((CONTROL_HOST, CONTROL_PORT))
    server_sock.listen()
    server_sock.settimeout(1.0)
    print(f"Control server listening on {CONTROL_HOST}:{CONTROL_PORT}")
    
    while shared_data.get('running', True):
        try:
            conn, addr = server_sock.accept()
            print(f"Control client connected from {addr}")
            control_conn = conn
            break
        except socket.timeout:
            continue

def read_single_camera(sock, window_name, data_key):
    if sock is None:
        return
        
    try:
        latest_frame_data = None
        sock.settimeout(None)
        length_bytes = sock.recv(4)
        if not length_bytes:
            return
            
        image_length = int.from_bytes(length_bytes, 'little')
        received_bytes = b''
        while len(received_bytes) < image_length and shared_data.get('running', True):
            packet = sock.recv(image_length - len(received_bytes))
            if not packet:
                break
            received_bytes += packet
            
        if len(received_bytes) == image_length:
            latest_frame_data = received_bytes
            
        while shared_data.get('running', True):
            readable, _, _ = select.select([sock], [], [], 0.0)
            if not readable:
                break
                
            sock.settimeout(1.0)
            length_bytes = sock.recv(4)
            if not length_bytes:
                return
            image_length = int.from_bytes(length_bytes, 'little')
            received_bytes = b''
            while len(received_bytes) < image_length and shared_data.get('running', True):
                packet = sock.recv(image_length - len(received_bytes))
                if not packet:
                    break
                received_bytes += packet
                
            if len(received_bytes) == image_length:
                latest_frame_data = received_bytes
                
        if latest_frame_data is not None:
            np_arr = np.frombuffer(latest_frame_data, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if frame is not None:
                with data_lock:
                    shared_data[data_key] = frame
                    shared_data['frame_count'] += 1
                
    except Exception:
        pass

def read_front_camera_task():
    read_single_camera(front_camera_sock, "Front Camera", 'latest_front_frame')

def processing_task():
    with data_lock:
        frame = shared_data.get('latest_front_frame', None)

    # FIX Cause 1: explicit None guard before any processing
    if frame is None:
        return

    # Save one frame every 2 seconds, up to 200 total
    now_save = time.perf_counter()
    with data_lock:
        last_save = shared_data['last_frame_save_time']
        count = shared_data['frame_save_count']

    if count < 200 and (now_save - last_save) >= 2.0:
        save_path = f'frames/frame_{count:04d}.png'
        cv2.imwrite(save_path, frame)
        with data_lock:
            shared_data['frame_save_count'] = count + 1
            shared_data['last_frame_save_time'] = now_save
        print(f'[COLLECT] Saved {save_path} ({count+1}/200)')

    brightness = detect_brightness(frame)
    tokens = detect_tokens(frame)

    with data_lock:
        shared_data['brightness'] = brightness
        shared_data['tokens'] = tokens

    # FIX Cause 3: save one debug frame to disk so we can inspect actual HSV colors
    with data_lock:
        already_saved = shared_data.get('debug_saved', False)
    if not already_saved:
        cv2.imwrite('debug_frame.png', frame)
        with data_lock:
            shared_data['debug_saved'] = True
        print('[ProcessingTask] Saved debug_frame.png for HSV inspection')

    back_frame = shared_data.get('latest_back_frame', None)
    if back_frame is not None:
        # Detect if a large object (approaching car) fills the bottom center of back frame
        h, w = back_frame.shape[:2]
        roi = back_frame[int(h*0.5):, int(w*0.3):int(w*0.7)]  # bottom center ROI
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        mean_val = np.mean(gray)
        # If bottom center is significantly brighter than background → headlights approaching
        if mean_val > 180:
            with data_lock:
                shared_data['event_trailing_car'] = True

# tap_steer: fires a timed steering pulse in a background daemon thread.
# duration=0.12s gives ~24 send-cycles at 200Hz — long enough for simulator to register.
# _tap_lock prevents overlapping taps from fighting each other.
_tap_lock = threading.Lock()

def tap_steer(value, duration=0.12):
    def _do_tap():
        if not _tap_lock.acquire(blocking=False):
            return  # another tap is active, skip this one
        try:
            with data_lock:
                shared_data["steering"] = value
            time.sleep(duration)
            with data_lock:
                shared_data["steering"] = 0.0
        finally:
            _tap_lock.release()
    t = threading.Thread(target=_do_tap, daemon=True)
    t.start()

def decision_task():
    """
    Priority-based decision engine using dynamic pixel boundaries.
    Steering: -1.0 = LEFT, +1.0 = RIGHT (from sample_drive.py).
    """
    now = time.perf_counter()

    # Read all shared state under a single lock acquisition
    with data_lock:
        tokens         = shared_data.get('tokens', [])
        brightness     = shared_data.get('brightness', 255.0)
        frame          = shared_data.get('latest_front_frame', None)
        t_red          = shared_data.get('last_red_steer_time', 0.0)
        t_green        = shared_data.get('last_green_steer_time', 0.0)
        t_yellow       = shared_data.get('last_yellow_steer_time', 0.0)

    if frame is None:
        return

    frame_width  = frame.shape[1]
    frame_height = frame.shape[0]

    # Dynamic lane boundaries (works for any resolution)
    left_boundary  = frame_width // 3        # ~213 for 640px
    right_boundary = 2 * frame_width // 3    # ~426 for 640px
    center_x       = frame_width // 2        # ~320 for 640px

    # PROXIMITY FILTER: ignore tokens in the top 20% (too far to act on accurately)
    tokens = [t for t in tokens if t['y'] > frame_height * 0.20]

    # --- Token selection: largest area first (detect_tokens already sorts this way) ---
    red_token    = next((t for t in tokens if t['color'] == 'red'),    None)
    green_token  = next((t for t in tokens if t['color'] == 'green'),  None)
    yellow_token = next((t for t in tokens if t['color'] == 'yellow'), None)

    # Phantom red suppression: only for FAR tokens (top half of frame)
    if red_token:
        if red_token['y'] < frame_height * 0.5:
            # Far token: check for phantom (stuck at same cx)
            with data_lock:
                history = shared_data['red_cx_history']
                history.append(red_token['cx'])
                if len(history) > 10:
                    history.pop(0)
                shared_data['red_cx_history'] = history
            if len(history) == 10 and (max(history) - min(history)) < 5:
                red_token = None
                print("[DECISION] Phantom red suppressed (far)")
        else:
            # Close token: always trust it, reset history
            with data_lock:
                shared_data['red_cx_history'] = []
    else:
        with data_lock:
            shared_data['red_cx_history'] = []

    # EVENT: Low Brightness
    if brightness < 60.0:
        with data_lock:
            shared_data['lights_on'] = True
            shared_data['event_low_brightness'] = True
        print("[EVENT] Low brightness detected")

    # EVENT: Trailing Car
    with data_lock:
        trailing = shared_data.get('event_trailing_car', False)
    if trailing:
        print("[EVENT] Trailing car -- switching lane")
        tap_steer(1.0, duration=0.15)
        with data_lock:
            shared_data['event_trailing_car'] = False
            shared_data['last_red_steer_time'] = time.perf_counter()
        return

    target_steering     = 0.0
    target_acceleration = 1.0
    steer_taken         = None
    color_printed       = None
    cx_printed          = None

    # P1. Red token DIRECTLY AHEAD -- steer AWAY
    if red_token and left_boundary <= red_token['cx'] <= right_boundary and (now - t_red) >= 0.35:
        # Red left-of-center -> steer RIGHT (+1.0) to dodge right
        # Red right-of-center -> steer LEFT (-1.0) to dodge left
        steer = 1.0 if red_token['cx'] < center_x else -1.0
        target_steering = steer
        steer_taken = 'red'
        color_printed = 'red'
        cx_printed = red_token['cx']

    # P2. Red token to the LEFT -- steer RIGHT to avoid
    elif red_token and red_token['cx'] < left_boundary and red_token['y'] > frame_height * 0.35 and (now - t_red) >= 0.25:
        target_steering = 0.7  # steer right, away from left-side red
        steer_taken = 'red'
        color_printed = 'red'
        cx_printed = red_token['cx']

    # P3. Red token to the RIGHT -- steer LEFT to avoid
    elif red_token and red_token['cx'] > right_boundary and red_token['y'] > frame_height * 0.35 and (now - t_red) >= 0.25:
        target_steering = -0.7  # steer left, away from right-side red
        steer_taken = 'red'
        color_printed = 'red'
        cx_printed = red_token['cx']

    # P4. Green token to the LEFT -- steer LEFT toward it
    elif green_token and green_token['cx'] < left_boundary and (now - t_green) >= 0.2:
        target_steering = -0.7  # steer left toward green
        steer_taken = 'green'
        color_printed = 'green'
        cx_printed = green_token['cx']

    # P5. Green token to the RIGHT -- steer RIGHT toward it
    elif green_token and green_token['cx'] > right_boundary and (now - t_green) >= 0.2:
        target_steering = 0.7  # steer right toward green
        steer_taken = 'green'
        color_printed = 'green'
        cx_printed = green_token['cx']

    # P6. Green DIRECTLY AHEAD -- stay straight, collect it
    elif green_token and left_boundary <= green_token['cx'] <= right_boundary:
        pass

    # P7. Yellow DIRECTLY AHEAD -- dodge it
    elif yellow_token and left_boundary <= yellow_token['cx'] <= right_boundary and (now - t_yellow) >= 0.4:
        steer = 1.0 if yellow_token['cx'] < center_x else -1.0
        target_steering = steer
        steer_taken = 'yellow'
        color_printed = 'yellow'
        cx_printed = yellow_token['cx']

    # P8. Default -- drive straight
    else:
        target_steering = 0.0
        target_acceleration = 1.0

    if steer_taken is not None:
        print(f"[DECISION] {color_printed} cx={cx_printed} -> steer={target_steering:.1f}")

    # --- Commit acceleration ---
    with data_lock:
        shared_data['acceleration'] = target_acceleration

    # --- Fire steering tap and update cooldown state ---
    if target_steering != 0.0 and steer_taken is not None:
        tap_steer(target_steering, duration=0.12)
        ts = time.perf_counter()
        with data_lock:
            if steer_taken == 'red':
                shared_data['last_red_steer_time'] = ts
            elif steer_taken == 'green':
                shared_data['last_green_steer_time'] = ts
            elif steer_taken == 'yellow':
                shared_data['last_yellow_steer_time'] = ts
    else:
        with data_lock:
            shared_data['steering'] = 0.0

def send_controls_task():
    global control_conn
    if control_conn is None:
        return
        
    with data_lock:
        steering = shared_data.get('steering', 0.0)
        acceleration = shared_data.get('acceleration', 1.0)
        
    try:
        data = struct.pack('ff', steering, acceleration)
        control_conn.sendall(data)
    except Exception as e:
        print(f"Control send error: {e}")
        control_conn = None

def monitor_task():
    with data_lock:
        frames = shared_data.get('frame_count', 0)
        speed = shared_data.get('acceleration', 1.0)
        tokens = shared_data.get('tokens', [])
        
    green = sum(1 for t in tokens if t['color'] == 'green')
    red = sum(1 for t in tokens if t['color'] == 'red')
    yellow = sum(1 for t in tokens if t['color'] == 'yellow')
    
    print(f"[Monitor] Frames: {frames} | Speed: {speed:.1f} | Tokens -> G:{green} Y:{yellow} R:{red}")

if __name__ == '__main__':
    print("Initializing RTOS Multi-Threaded Driving Agent...")
    
    import os
    os.makedirs('frames', exist_ok=True)

    threading.Thread(target=setup_control_server, daemon=True).start()
    threading.Thread(target=setup_cameras, daemon=True).start()
    
    t_front_camera = RTTask("ReadFrontCamera", period=0.005, priority=TaskPriority.HIGH, execute_func=read_front_camera_task)
    t_processing = RTTask("ProcessingTask", period=0.005, priority=TaskPriority.MEDIUM, execute_func=processing_task)
    # FIX Cause 4: period reduced from 0.010s → 0.005s to match ProcessingTask cadence
    t_decision = RTTask("DecisionTask", period=0.005, priority=TaskPriority.MEDIUM, execute_func=decision_task)
    t_controls = RTTask("SendControlsTask", period=0.005, priority=TaskPriority.HIGH, execute_func=send_controls_task)
    t_monitor = RTTask("MonitorTask", period=0.5, priority=TaskPriority.LOW, execute_func=monitor_task)
    
    t_front_camera.start()
    t_processing.start()
    t_decision.start()
    t_controls.start()
    t_monitor.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nKeyboard Interrupt detected. Stopping system...")
        with data_lock:
            shared_data["running"] = False

    t_front_camera.join()
    t_processing.join()
    t_decision.join()
    t_controls.join()
    t_monitor.join()
    
    if front_camera_sock:
        front_camera_sock.close()
    if control_conn:
        control_conn.close()
    print("Agent stopped.")
