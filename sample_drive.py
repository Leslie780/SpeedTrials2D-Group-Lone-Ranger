import socket
import threading
import struct
import cv2
import numpy as np
import time
import keyboard
import select
import ctypes

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
CAMERA_HOST = '127.0.0.1'
FRONT_CAMERA_PORT = 8080
BACK_CAMERA_PORT = 8082
CONTROL_HOST = '127.0.0.1'
CONTROL_PORT = 8081

# Shared Resources with Mutex Lock for Concurrency
shared_data = {
    'latest_front_frame': None,
    'latest_back_frame': None,
    'front_scene': None,
    'police_light_status': None,
    'chasing_car_status': None,
    'target_coin_status': None,
    'obstacle_coin_status': None,
    'steering_input' : 0.0,
    'acceleration_input' : 0.0
}
data_lock = threading.Lock()
is_running = True

# ---------------------------------------------------------
# Real-Time Scheduling Framework (Do not change this in your code)
# ---------------------------------------------------------
class TaskPriority:
    HIGH = 1
    MEDIUM = 2
    LOW = 3

class RTTask(threading.Thread):
    """
    Real-Time Task implementing:
    - Concurrency (inherits threading.Thread)
    - Task Period (enforced in run loop)
    - Task Priority (logical priority assigned)
    """
    def __init__(self, name, period, priority, execute_func):
        super().__init__()
        self.name = name
        self.period = period
        self.priority = priority
        self.execute_func = execute_func
        self.daemon = True
        self._last_error_time = 0.0

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
        except Exception as e:
            pass

        while is_running:
            start_time = time.time()
            try:
                self.execute_func()
            except Exception as e:
                now = time.time()
                if now - self._last_error_time > 1.0:
                    print(f"[{self.name}] Task error: {e}")
                    self._last_error_time = now
            exec_time = time.time() - start_time
            sleep_time = self.period - exec_time
            
            if sleep_time > 0:
                time.sleep(sleep_time)

# ---------------------------------------------------------
# Network Connection Setup (Do not change this in your code)
# ---------------------------------------------------------
front_camera_sock = None
back_camera_sock = None
control_conn = None

def setup_cameras():
    global front_camera_sock, back_camera_sock
    
    print("Connecting to Cameras...")
    front_connected = False
    back_connected = False
    
    while is_running and not (front_connected and back_connected):
        if not front_connected:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.0)
                s.connect((CAMERA_HOST, FRONT_CAMERA_PORT))
                front_camera_sock = s
                print("Connected to Front Camera successfully.")
                front_connected = True
            except Exception:
                pass
                
        if not back_connected:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.0)
                s.connect((CAMERA_HOST, BACK_CAMERA_PORT))
                back_camera_sock = s
                print("Connected to Back Camera successfully.")
                back_connected = True
            except Exception:
                pass
                
        if not (front_connected and back_connected):
            time.sleep(1)

def setup_control_server():
    global control_conn
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((CONTROL_HOST, CONTROL_PORT))
    server_sock.listen()
    server_sock.settimeout(1.0)
    print(f"Control server listening on {CONTROL_HOST}:{CONTROL_PORT}")
    
    while is_running:
        try:
            conn, addr = server_sock.accept()
            print(f"Control client connected from {addr}")
            control_conn = conn
            break
        except socket.timeout:
            continue

# ---------------------------------------------------------
# Task Implementations (This is where you write your tasks)
# ---------------------------------------------------------

def read_single_camera(sock, window_name, data_key):
    #This function reads the latest frame from the camera socket and stores it in the shared data
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
        while len(received_bytes) < image_length and is_running:
            packet = sock.recv(image_length - len(received_bytes))
            if not packet:
                break
            received_bytes += packet
            
        if len(received_bytes) == image_length:
            latest_frame_data = received_bytes
            
        while is_running:
            readable, _, _ = select.select([sock], [], [], 0.0)
            if not readable:
                break
                
            sock.settimeout(1.0)
            length_bytes = sock.recv(4)
            if not length_bytes:
                return
            image_length = int.from_bytes(length_bytes, 'little')
            received_bytes = b''
            while len(received_bytes) < image_length and is_running:
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
                
                # You may disable this if you don't need to display the frames / This could effect the fps
                frame_resized = cv2.resize(frame, (640, 480))
                cv2.imshow(window_name, frame_resized)
                cv2.waitKey(1)
                
    except Exception as e:
        pass

def read_front_camera_task():
    read_single_camera(front_camera_sock, "Front Camera", 'latest_front_frame')

def read_back_camera_task():
    read_single_camera(back_camera_sock, "Back Camera", 'latest_back_frame')

# =========================================================
# Autonomous-driving tuning parameters
# (These are the knobs to adjust if the car misbehaves.
#  Colours are read in HSV. OpenCV uses H in [0,179], S/V in [0,255].)
# =========================================================
DEBUG = False            # set True to pop up a window showing what the car "sees"

ROI_TOP = 0.38           # top of the look-ahead band (fraction of image height)
ROI_BOT = 0.86           # bottom of the band (above the car's own hood)
STEER_GAIN = 1.18        # how aggressively to steer for a given lateral error
TURN_SLOWDOWN = 0.24     # how much hard turns cut the throttle
STEER_SMOOTHING = 0.24   # higher = react faster, lower = smoother
STEER_DEADBAND = 0.035   # ignore tiny steering corrections near the centre
STEER_RATE_LIMIT = 0.040 # max steering change per control update
TARGET_SMOOTHING = 0.20  # higher = target changes faster
TARGET_RATE_LIMIT = 0.020 # max target-point shift as a fraction of image width
MIN_ACCEL = 0.52         # keep enough speed for chase/darkness events
MIN_TOKEN_AREA = 80      # ignore colour blobs smaller than this (noise)
RED_DANGER_AREA = 420    # a red blob bigger than this counts as "close"
YELLOW_DANGER_AREA = 520
GREEN_REACH_LIMIT = 0.46 # do not chase green coins that require leaving the lane
HAZARD_PATH_WIDTH = 0.12 # hazard must be close to our path before we dodge
HAZARD_CLOSE_Y = 0.46    # y fraction in the ROI; larger means closer to the car
VEHICLE_DANGER_AREA = 900
EVASION_BIAS = 0.28      # lane-width fraction used to shake off chasing cars
THREAT_PERIOD = 0.05     # run police/chaser detection at 20 Hz
CHASER_HOLD_TIME = 0.80  # keep escaping briefly when rear detection flickers
DARKNESS_LUMA = 58.0
DARKNESS_ACCEL = 1.00
DARKNESS_MIN_ACCEL = 0.78
DARKNESS_TURN_SLOWDOWN = 0.16
DARKNESS_PULSE_SECONDS = 0.18
DARKNESS_RETRY_SECONDS = 1.00

# Green = best reward, Yellow = lesser reward, Red = hazard to dodge.
GREEN_LO,  GREEN_HI  = (35,  45,  35),  (90,  255, 255)
YELLOW_LO, YELLOW_HI = (16,  75,  60),  (40,  255, 255)
RED1_LO,   RED1_HI   = (0,   60,  35),  (12,  255, 255)
RED2_LO,   RED2_HI   = (168, 60,  35),  (180, 255, 255)
BLUE_LO,   BLUE_HI   = (92,  60,  35),  (135, 255, 255)
# Asphalt = low saturation, mid value (used for lane-keeping when no token is visible)
ROAD_LO,   ROAD_HI   = (0,   0,   25),  (180, 90,  210)

_kernel = np.ones((5, 5), np.uint8)
_wide_kernel = np.ones((9, 9), np.uint8)
_opencv_lock = threading.Lock()
_vision_state = {
    'road_cx': None,
    'target_x': None,
    'steering': 0.0,
    'last_control_time': 0.0,
    'evasion_side': 1.0,
    'last_evasion_swap': 0.0,
    'last_threat_check': 0.0,
    'front_threat': None,
    'back_threat': None,
    'last_chaser_seen': 0.0,
    'darkness_pulse_until': 0.0,
    'last_darkness_pulse': 0.0,
}


def _clean(mask):
    """Remove speckle noise and close small holes in a binary mask."""
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _kernel)
    return mask


def _enhance_for_vision(frame):
    """Brighten dark frames while preserving colour relationships."""
    if frame is None or frame.size == 0:
        return frame

    if frame.dtype != np.uint8:
        frame = np.clip(frame, 0, 255).astype(np.uint8)
    frame = np.ascontiguousarray(frame)

    try:
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        mean_l = float(np.mean(l))

        # CLAHE is not reliably thread-safe in this OpenCV build, so keep it local
        # and serialize this small section across the camera monitor tasks.
        with _opencv_lock:
            clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
            l = clahe.apply(l)

        if mean_l < 110.0:
            alpha = float(np.clip(135.0 / max(mean_l, 8.0), 1.25, 3.4))
            beta = 18 if mean_l < 55.0 else 8
            l = cv2.convertScaleAbs(l, alpha=alpha, beta=beta)

        return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    except cv2.error:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mean_v = float(np.mean(gray))
        alpha = float(np.clip(125.0 / max(mean_v, 8.0), 1.0, 3.0))
        return cv2.convertScaleAbs(frame, alpha=alpha, beta=12)


def _dominance_masks(vision_bgr):
    """Colour masks that still work when HSV value is unreliable in darkness."""
    b, g, r = cv2.split(vision_bgr.astype(np.int16))
    green = ((g > r + 18) & (g > b + 12) & (g > 45)).astype(np.uint8) * 255
    red = ((r > g + 18) & (r > b + 12) & (r > 45)).astype(np.uint8) * 255
    yellow = ((r > 80) & (g > 75) & (b < np.minimum(r, g) - 35) &
              (np.abs(r - g) < 50)).astype(np.uint8) * 255
    return green, yellow, red


def _build_masks(roi):
    """Return enhanced ROI plus token and road masks."""
    if roi is None or roi.size == 0:
        empty = np.zeros((1, 1), dtype=np.uint8)
        return np.zeros((1, 1, 3), dtype=np.uint8), None, empty, empty, empty, empty

    vision = _enhance_for_vision(roi)
    hsv = cv2.cvtColor(vision, cv2.COLOR_BGR2HSV)

    green_hsv = cv2.inRange(hsv, GREEN_LO, GREEN_HI)
    yellow_hsv = cv2.inRange(hsv, YELLOW_LO, YELLOW_HI)
    red_hsv = cv2.bitwise_or(cv2.inRange(hsv, RED1_LO, RED1_HI),
                             cv2.inRange(hsv, RED2_LO, RED2_HI))
    green_dom, yellow_dom, red_dom = _dominance_masks(vision)

    green_mask = _clean(cv2.bitwise_or(green_hsv, green_dom))
    yellow_mask = _clean(cv2.bitwise_or(yellow_hsv, yellow_dom))
    red_mask = _clean(cv2.bitwise_or(red_hsv, red_dom))

    road_mask = cv2.inRange(hsv, ROAD_LO, ROAD_HI)
    road_mask = cv2.morphologyEx(road_mask, cv2.MORPH_OPEN, _wide_kernel)
    road_mask = cv2.morphologyEx(road_mask, cv2.MORPH_CLOSE, _wide_kernel)

    return vision, hsv, green_mask, yellow_mask, red_mask, road_mask


def _best_blob(mask, min_area, x_range=None):
    """Return (cx, cy, area) of the largest *token-like* blob, or None.
    Rejects long/thin shapes (roadside grass, red rumble strips) and anything
    outside the road's horizontal span, so only round on-road tokens qualify."""
    blobs = _token_blobs(mask, min_area, x_range)
    if not blobs:
        return None
    best = max(blobs, key=lambda blob: blob[2])
    return best


def _token_blobs(mask, min_area, x_range=None):
    """Return token-like round blobs as (cx, cy, area), sorted by closeness."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for c in cnts:
        area = cv2.contourArea(c)
        if area <= float(min_area):
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bh == 0:
            continue
        aspect = bw / float(bh)
        if aspect < 0.4 or aspect > 2.5:        # reject long strips (grass / rumble)
            continue
        peri = cv2.arcLength(c, True)
        if peri == 0:
            continue
        if 4 * np.pi * area / (peri * peri) < 0.45:   # reject non-round shapes
            continue
        M = cv2.moments(c)
        if M['m00'] == 0:
            continue
        bx = M['m10'] / M['m00']
        if x_range is not None and not (x_range[0] <= bx <= x_range[1]):
            continue                            # token must be on the road
        blobs.append((bx, M['m01'] / M['m00'], area))
    return sorted(blobs, key=lambda blob: (blob[1], blob[2]), reverse=True)


def _select_collectible_token(blobs, road_cx, previous_target, w, roi_h):
    """Pick the green coin that is valuable without making the car swerve wildly."""
    if not blobs:
        return None

    best, best_score = None, -1e9
    for bx, by, area in blobs:
        y_norm = float(np.clip(by / max(1.0, roi_h), 0.0, 1.0))
        lateral = abs(bx - road_cx) / max(1.0, w / 2.0)
        if lateral > GREEN_REACH_LIMIT and y_norm < 0.62:
            continue

        closeness = 1.45 * y_norm
        size_score = min(1.0, area / 1700.0)
        lateral_penalty = 1.15 * lateral
        switch_penalty = 0.0
        if previous_target is not None:
            switch_penalty = 0.20 * abs(bx - previous_target) / max(1.0, w)

        score = closeness + size_score - lateral_penalty - switch_penalty
        if score > best_score:
            best_score = score
            best = (bx, by, area, score)

    return best


def _road_center(road_mask, fallback_x):
    """Estimate the horizontal centre of the road from a grey-asphalt mask."""
    M = cv2.moments(road_mask)
    if M['m00'] > 5000:          # enough road pixels to trust the estimate
        return M['m10'] / M['m00']
    return fallback_x


def _road_bounds(road_mask, w):
    """Left/right edge columns of the road, used to keep token search on-road."""
    cols = np.where(road_mask.sum(axis=0) > 0)[0]
    if len(cols) < 10:
        return 0.0, float(w)
    return float(np.percentile(cols, 3)), float(np.percentile(cols, 97))


def _lane_center_from_lines(vision_roi, fallback_x):
    """Use bright lane markings as a fallback when the asphalt is too dark."""
    h, w = vision_roi.shape[:2]
    hsv = cv2.cvtColor(vision_roi, cv2.COLOR_BGR2HSV)
    lower = hsv[int(h * 0.35):, :]
    lane_mask = cv2.inRange(lower, (0, 0, 135), (180, 80, 255))
    lane_mask = cv2.morphologyEx(lane_mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    ys, xs = np.where(lane_mask > 0)
    if len(xs) < 40:
        return fallback_x

    left = xs[xs < w * 0.48]
    right = xs[xs > w * 0.52]
    if len(left) > 20 and len(right) > 20:
        return (float(np.median(left)) + float(np.median(right))) / 2.0
    if len(left) > 30:
        return float(np.clip(np.median(left) + w * 0.34, 0, w))
    if len(right) > 30:
        return float(np.clip(np.median(right) - w * 0.34, 0, w))
    return fallback_x


def _smooth(value, old_value, alpha):
    if old_value is None:
        return float(value)
    return float((alpha * value) + ((1.0 - alpha) * old_value))


def _rate_limit(value, old_value, max_delta):
    if old_value is None:
        return float(value)
    return float(np.clip(value, old_value - max_delta, old_value + max_delta))


def _largest_rect_blob(mask, min_area, x_range=None):
    """Return the largest rectangular/vehicle-like blob in a mask."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best, best_area = None, float(min_area)
    for c in cnts:
        area = cv2.contourArea(c)
        if area <= best_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 8 or bh < 8:
            continue
        aspect = bw / float(bh)
        if aspect < 0.35 or aspect > 4.5:
            continue
        M = cv2.moments(c)
        if M['m00'] == 0:
            continue
        cx = M['m10'] / M['m00']
        if x_range is not None and not (x_range[0] <= cx <= x_range[1]):
            continue
        best = (cx, M['m01'] / M['m00'], area, (x, y, bw, bh))
        best_area = area
    return best


def _plain_rear_vehicle_blob(hsv, roi_w, roi_h):
    """Fallback detector for non-police chasing cars in the rear camera."""
    lower_start = int(roi_h * 0.22)
    lower = hsv[lower_start:, :]
    if lower.size == 0:
        return None

    # A regular chasing car may not have police lights. Look for compact,
    # saturated/bright body or light regions in the lower rear view.
    colored = cv2.inRange(lower, (0, 65, 45), (180, 255, 255))
    mask = colored
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _kernel)

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best, best_area = None, float(VEHICLE_DANGER_AREA * 1.2)
    for c in cnts:
        area = cv2.contourArea(c)
        if area <= best_area:
            continue
        if area > roi_w * roi_h * 0.22:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < roi_w * 0.08 or bh < roi_h * 0.06:
            continue
        aspect = bw / float(max(1, bh))
        if aspect < 0.55 or aspect > 5.5:
            continue
        M = cv2.moments(c)
        if M['m00'] == 0:
            continue
        cx = M['m10'] / M['m00']
        cy = lower_start + M['m01'] / M['m00']
        best = (cx, cy, area, (x, lower_start + y, bw, bh))
        best_area = area
    return best


def _vehicle_threat(frame, prefer_lower=True, include_body=True):
    """Detect police/chasing-car cues from red/blue lights and bright car bodies."""
    if frame is None or frame.size == 0:
        return None

    h, w = frame.shape[:2]
    top = int(h * (0.34 if prefer_lower else 0.25))
    bot = int(h * 0.92)
    if bot <= top or w <= 0:
        return None

    roi = frame[top:bot, :]
    if roi.size == 0:
        return None

    vision = _enhance_for_vision(roi)
    if vision is None or vision.size == 0:
        return None

    hsv = cv2.cvtColor(vision, cv2.COLOR_BGR2HSV)

    red = cv2.bitwise_or(cv2.inRange(hsv, RED1_LO, RED1_HI),
                         cv2.inRange(hsv, RED2_LO, RED2_HI))
    blue = cv2.inRange(hsv, BLUE_LO, BLUE_HI)
    signal = cv2.bitwise_or(red, blue)
    mask = signal
    if include_body:
        white = cv2.inRange(hsv, (0, 0, 150), (180, 70, 255))
        saturated = cv2.inRange(hsv, (0, 75, 45), (180, 255, 255))
        body = cv2.bitwise_or(white, saturated)
        nearby_signal = cv2.dilate(signal, np.ones((21, 21), np.uint8), iterations=1)
        mask = cv2.bitwise_or(signal, cv2.bitwise_and(body, nearby_signal))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _wide_kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _kernel)

    threat = _largest_rect_blob(mask, VEHICLE_DANGER_AREA)
    if threat is None and include_body:
        threat = _plain_rear_vehicle_blob(hsv, w, roi.shape[0])
    if threat is None:
        return None

    cx, cy, area, rect = threat
    lower_weight = 1.0 + 0.6 * (cy / max(1.0, roi.shape[0]))
    score = (area / float(w * max(1, roi.shape[0]))) * lower_weight
    return {
        'x': float(cx),
        'y': float(top + cy),
        'area': float(area),
        'score': float(score),
        'rect': rect,
    }


def _front_scene_from_frame(front_frame):
    """Analyse the front camera once so Task 1/3/4 share the same view."""
    h, w = front_frame.shape[:2]
    img_cx = w / 2.0
    top, bot = int(h * ROI_TOP), int(h * ROI_BOT)
    roi = front_frame[top:bot, :]
    mean_light = float(np.mean(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)))
    vision_roi, hsv, green_mask, yellow_mask, red_mask, road_mask = _build_masks(roi)

    fallback_road_cx = _vision_state['road_cx'] if _vision_state['road_cx'] is not None else img_cx
    road_cx = _road_center(road_mask, fallback_road_cx)
    road_cx = _lane_center_from_lines(vision_roi, road_cx)
    road_cx = _smooth(road_cx, _vision_state['road_cx'], 0.18)
    _vision_state['road_cx'] = road_cx

    x_lo, x_hi = _road_bounds(road_mask, w)
    margin = w * 0.04
    on_road = (x_lo + margin, x_hi - margin)
    if on_road[1] <= on_road[0] + w * 0.20:
        on_road = (max(0.0, road_cx - w * 0.36), min(float(w), road_cx + w * 0.36))

    green_candidates = _token_blobs(green_mask, MIN_TOKEN_AREA, on_road)
    yellow_candidates = _token_blobs(yellow_mask, MIN_TOKEN_AREA, on_road)
    red_candidates = _token_blobs(red_mask, MIN_TOKEN_AREA, on_road)
    green = green_candidates[0] if green_candidates else None
    yellow = yellow_candidates[0] if yellow_candidates else None
    red = red_candidates[0] if red_candidates else None

    return {
        'w': w,
        'h': h,
        'roi_h': max(1, bot - top),
        'top': top,
        'bot': bot,
        'road_cx': road_cx,
        'on_road': on_road,
        'green': green,
        'yellow': yellow,
        'red': red,
        'green_candidates': green_candidates,
        'yellow_candidates': yellow_candidates,
        'red_candidates': red_candidates,
        'mean_light': mean_light,
        'darkness': mean_light < DARKNESS_LUMA,
        'timestamp': time.time(),
    }


def task1_police_car_light_monitor():
    """Task 1: front-camera police/light monitor and shared front-scene cache."""
    with data_lock:
        front_frame = shared_data['latest_front_frame']

    if front_frame is None:
        return

    scene = _front_scene_from_frame(front_frame)
    police_threat = _vehicle_threat(front_frame, prefer_lower=False, include_body=False)
    status = {
        'police_threat': police_threat,
        'darkness': scene['darkness'],
        'mean_light': scene['mean_light'],
        'timestamp': scene['timestamp'],
    }

    with data_lock:
        shared_data['front_scene'] = scene
        shared_data['police_light_status'] = status


def task2_chasing_car_monitor():
    """Task 2: back-camera chasing-car monitor."""
    with data_lock:
        back_frame = shared_data['latest_back_frame']

    chaser = _vehicle_threat(back_frame, prefer_lower=True, include_body=True)
    now = time.time()
    if chaser is not None:
        _vision_state['back_threat'] = chaser
        _vision_state['last_chaser_seen'] = now
    elif now - _vision_state['last_chaser_seen'] < CHASER_HOLD_TIME:
        chaser = _vision_state['back_threat']
    else:
        _vision_state['back_threat'] = None

    status = {
        'chaser': chaser,
        'timestamp': now,
    }

    with data_lock:
        shared_data['chasing_car_status'] = status


def task3_target_seeker():
    """Task 3: seek green coins and track red coins so they are never targeted."""
    with data_lock:
        scene = shared_data.get('front_scene')

    if scene is None:
        return

    green_candidates = scene.get('green_candidates', [])
    red = scene.get('red')
    road_cx = scene['road_cx']
    target_x = road_cx
    reason = 'road'
    selected_green = None

    green_target = _select_collectible_token(
        green_candidates,
        road_cx,
        _vision_state['target_x'],
        scene['w'],
        scene['roi_h']
    )
    if green_target is not None:
        bx, by, area, score = green_target
        y_norm = float(np.clip(by / max(1.0, scene['roi_h']), 0.0, 1.0))
        commit = 0.58 + 0.34 * y_norm
        target_x = road_cx * (1.0 - commit) + bx * commit
        selected_green = (bx, by, area)
        reason = 'green'

    status = {
        'target_x': float(target_x),
        'green': selected_green,
        'green_candidates': green_candidates,
        'red': red,
        'reason': reason,
        'timestamp': time.time(),
    }

    with data_lock:
        shared_data['target_coin_status'] = status


def _avoidance_from_blob(blob, road_cx, target_x, w, roi_h, area_threshold, side_margin, close_y):
    if blob is None:
        return None
    bx, by, area = blob
    y_norm = float(np.clip(by / max(1.0, roi_h), 0.0, 1.0))
    in_path = abs(bx - target_x) < w * HAZARD_PATH_WIDTH or abs(bx - road_cx) < w * 0.10
    large_and_approaching = area >= area_threshold * 2.2 and y_norm >= close_y - 0.14
    close_enough = y_norm >= close_y or large_and_approaching
    if not in_path or not close_enough:
        return None

    avoid_left = bx > road_cx
    avoid_x = bx - w * side_margin if avoid_left else bx + w * side_margin
    urgency = min(1.0, max(0.25, (0.55 * y_norm) + (area / float(area_threshold * 4.0))))
    return {
        'blob': blob,
        'avoid_x': float(avoid_x),
        'urgency': float(urgency),
    }


def _best_avoidance_from_blobs(blobs, road_cx, target_x, w, roi_h, area_threshold, side_margin, close_y):
    best, best_urgency = None, -1.0
    for blob in blobs or []:
        avoidance = _avoidance_from_blob(blob, road_cx, target_x, w, roi_h, area_threshold, side_margin, close_y)
        if avoidance is not None and avoidance['urgency'] > best_urgency:
            best = avoidance
            best_urgency = avoidance['urgency']
    return best


def task4_obstacle_avoidance():
    """Task 4: avoid yellow and red coins, with red treated as the stronger hazard."""
    with data_lock:
        scene = shared_data.get('front_scene')
        target_status = shared_data.get('target_coin_status')

    if scene is None:
        return

    w = scene['w']
    roi_h = scene['roi_h']
    road_cx = scene['road_cx']
    target_x = target_status['target_x'] if target_status is not None else road_cx
    red_avoid = _best_avoidance_from_blobs(
        scene.get('red_candidates', []), road_cx, target_x, w, roi_h,
        RED_DANGER_AREA, 0.30, HAZARD_CLOSE_Y
    )
    yellow_avoid = _best_avoidance_from_blobs(
        scene.get('yellow_candidates', []), road_cx, target_x, w, roi_h,
        YELLOW_DANGER_AREA, 0.22, HAZARD_CLOSE_Y + 0.08
    )

    chosen = red_avoid if red_avoid is not None else yellow_avoid
    status = {
        'avoidance': chosen,
        'red_avoidance': red_avoid,
        'yellow_avoidance': yellow_avoid,
        'timestamp': time.time(),
    }

    with data_lock:
        shared_data['obstacle_coin_status'] = status


def processing_task():
    """Fuse Task 1-4 outputs into steering and throttle commands."""
    with data_lock:
        front_frame = shared_data['latest_front_frame']
        scene = shared_data.get('front_scene')
        police_light = shared_data.get('police_light_status')
        chaser_status = shared_data.get('chasing_car_status')
        target_status = shared_data.get('target_coin_status')
        obstacle_status = shared_data.get('obstacle_coin_status')

    if front_frame is None or scene is None:
        return

    w = scene['w']
    img_cx = w / 2.0
    road_cx = scene['road_cx']
    on_road = scene['on_road']
    darkness = bool(police_light and police_light.get('darkness'))
    now = time.time()
    if darkness:
        if now - _vision_state['last_darkness_pulse'] >= DARKNESS_RETRY_SECONDS:
            _vision_state['darkness_pulse_until'] = now + DARKNESS_PULSE_SECONDS
            _vision_state['last_darkness_pulse'] = now
    else:
        _vision_state['darkness_pulse_until'] = 0.0
        _vision_state['last_darkness_pulse'] = 0.0

    target_x = target_status['target_x'] if target_status is not None else road_cx
    accel = DARKNESS_ACCEL if darkness else 0.95

    green = target_status.get('green') if target_status else None
    if darkness and green is not None and green[2] < 220 and abs(green[0] - road_cx) > w * 0.22:
        target_x = road_cx

    avoidance = obstacle_status.get('avoidance') if obstacle_status else None
    if avoidance is not None:
        target_x = avoidance['avoid_x']
        accel = min(accel, 0.62 if avoidance['urgency'] > 0.45 else 0.76)

    front_threat = police_light.get('police_threat') if police_light else None
    if front_threat is not None and front_threat['score'] > 0.010:
        target_x = front_threat['x'] - w * 0.30 if front_threat['x'] > road_cx else front_threat['x'] + w * 0.30
        accel = min(accel, 0.68)

    back_threat = chaser_status.get('chaser') if chaser_status else None
    chaser_active = False
    if back_threat is not None and back_threat['score'] > 0.003:
        chaser_active = True
        if abs(back_threat['x'] - img_cx) < w * 0.12 and now - _vision_state['last_evasion_swap'] > 1.0:
            _vision_state['evasion_side'] *= -1.0
            _vision_state['last_evasion_swap'] = now
        elif back_threat['x'] < img_cx:
            _vision_state['evasion_side'] = 1.0
        else:
            _vision_state['evasion_side'] = -1.0

        target_x += _vision_state['evasion_side'] * w * EVASION_BIAS
        accel = 1.0 if not darkness else max(accel, 0.88)

    if darkness and not chaser_active:
        target_x = _smooth(target_x, road_cx, 0.70)

    target_x = float(np.clip(target_x, on_road[0], on_road[1]))
    target_x = float(np.clip(target_x, road_cx - w * 0.38, road_cx + w * 0.38))
    target_x = float(np.clip(target_x, 0.0, float(w)))

    max_target_step = w * TARGET_RATE_LIMIT
    target_x = _smooth(target_x, _vision_state['target_x'], TARGET_SMOOTHING)
    target_x = _rate_limit(target_x, _vision_state['target_x'], max_target_step)
    _vision_state['target_x'] = target_x

    steering = (target_x - img_cx) / (w / 2.0)
    steering = float(np.clip(steering * STEER_GAIN, -1.0, 1.0))
    if abs(steering) < STEER_DEADBAND:
        steering = 0.0
    steering = _smooth(steering, _vision_state['steering'], STEER_SMOOTHING)
    steering = _rate_limit(steering, _vision_state['steering'], STEER_RATE_LIMIT)
    _vision_state['steering'] = steering
    turn_slowdown = DARKNESS_TURN_SLOWDOWN if darkness else TURN_SLOWDOWN
    min_accel = DARKNESS_MIN_ACCEL if darkness else MIN_ACCEL
    accel = float(np.clip(accel - turn_slowdown * abs(steering), min_accel, 1.0))

    if darkness and now < _vision_state['darkness_pulse_until']:
        steering = 0.0
        accel = -1.0
        _vision_state['steering'] = 0.0

    with data_lock:
        shared_data['steering_input'] = steering
        shared_data['acceleration_input'] = accel

    if DEBUG:
        dbg = front_frame.copy()
        top, bot = scene['top'], scene['bot']
        cv2.rectangle(dbg, (0, top), (w, bot), (255, 255, 0), 1)
        for blob, col in ((scene.get('green'), (0, 255, 0)),
                          (scene.get('yellow'), (0, 215, 255)),
                          (scene.get('red'), (0, 0, 255))):
            if blob is not None:
                cv2.circle(dbg, (int(blob[0]), top + int(blob[1])), 10, col, 2)
        if front_threat is not None:
            cv2.circle(dbg, (int(front_threat['x']), int(front_threat['y'])), 14, (255, 0, 0), 2)
        cv2.line(dbg, (int(target_x), top), (int(target_x), bot), (255, 0, 255), 2)
        cv2.putText(dbg, f"steer {steering:+.2f} accel {accel:.2f} dark {int(darkness)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imshow("AI view", dbg)
        cv2.waitKey(1)

def send_controls_task():
    #This is where you send the control commands to the car using the control_conn
    global control_conn
    if control_conn is None:
        return
    
    #these are the variables used to control the car
    #steering_input: -1.0 to 1.0 (left to right)
    #acceleration_input: -1.0 to 1.0 (reverse to forward)
    # Read the latest decision produced by processing_task().
    with data_lock:
        steering_input = float(shared_data.get('steering_input', 0.0))
        acceleration_input = float(shared_data.get('acceleration_input', 0.0))

    # Clamp to the valid range before sending, just in case.
    steering_input = max(-1.0, min(1.0, steering_input))
    acceleration_input = max(-1.0, min(1.0, acceleration_input))

    try:
        # Pack and send the control command
        data = struct.pack('ff', steering_input, acceleration_input)
        control_conn.sendall(data)
    except Exception as e:
        print(f"Control send error: {e}")
        control_conn = None


# ---------------------------------------------------------
# Main (Scheduler Initialization)
# ---------------------------------------------------------
if __name__ == '__main__':
    print("Initializing RTSE Sample Drive...")
    
    # Initialize network connections
    threading.Thread(target=setup_control_server, daemon=True).start()
    threading.Thread(target=setup_cameras, daemon=True).start()
    
    print("\n--- Starting Real-Time Tasks (awaiting connections dynamically) ---\n")
    
    # This is where you define tasks with explicit Scheduling parameters (Concurrency, Priority, Period)
    # Period refers to the period of execution of the task in seconds
    # Priority refers to the priority of the task, higher priority means higher priority
    # Concurrency refers to the number of instances of the task that can run at the same time
    t_front_camera = RTTask("ReadFrontCamera", period=0.005, priority=TaskPriority.HIGH, execute_func=read_front_camera_task)
    t_back_camera = RTTask("ReadBackCamera", period=0.005, priority=TaskPriority.HIGH, execute_func=read_back_camera_task)
    t_task1 = RTTask("Task1PoliceLightMonitor", period=0.02, priority=TaskPriority.HIGH, execute_func=task1_police_car_light_monitor)
    t_task2 = RTTask("Task2ChasingCarMonitor", period=0.05, priority=TaskPriority.MEDIUM, execute_func=task2_chasing_car_monitor)
    t_task3 = RTTask("Task3TargetSeeker", period=0.02, priority=TaskPriority.MEDIUM, execute_func=task3_target_seeker)
    t_task4 = RTTask("Task4ObstacleAvoidance", period=0.02, priority=TaskPriority.MEDIUM, execute_func=task4_obstacle_avoidance)
    t_processing = RTTask("DecisionFusion", period=0.005, priority=TaskPriority.MEDIUM, execute_func=processing_task)
    t_controls = RTTask("SendControls", period=0.005, priority=TaskPriority.HIGH, execute_func=send_controls_task)
    
    # Start tasks to run concurrently
    t_front_camera.start()
    t_back_camera.start()
    t_task1.start()
    t_task2.start()
    t_task3.start()
    t_task4.start()
    t_processing.start()
    t_controls.start()
    
    try:
        # You need this to keep the main thread alive, otherwise the program will exit immediately
        while is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nKeyboard Interrupt detected. Stopping system...")
        is_running = False

    # This is to make sure that the tasks are terminated cleanly
    t_front_camera.join()
    t_back_camera.join()
    t_task1.join()
    t_task2.join()
    t_task3.join()
    t_task4.join()
    t_processing.join()
    t_controls.join()
    
    # This is to close all the connections
    if front_camera_sock:
        front_camera_sock.close()
    if back_camera_sock:
        back_camera_sock.close()
    if control_conn:
        control_conn.close()
    cv2.destroyAllWindows()
    print("System terminated cleanly.")
