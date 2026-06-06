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

from token_detector import detect_tokens, get_lane, detect_brightness, annotate_frame

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
    'frame_count': 0
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
    
    if frame is not None:
        brightness = detect_brightness(frame)
        tokens = detect_tokens(frame)
        
        with data_lock:
            shared_data['brightness'] = brightness
            shared_data['tokens'] = tokens

def tap_steer(value, duration=0.05):
    with data_lock:
        shared_data["steering"] = value
    time.sleep(duration)
    with data_lock:
        shared_data["steering"] = 0.0

def decision_task():
    with data_lock:
        tokens = shared_data.get('tokens', [])
        brightness = shared_data.get('brightness', 255.0)
        frame = shared_data.get('latest_front_frame', None)
        
    if frame is None:
        return

    frame_width = frame.shape[1]
    
    if brightness < 60.0:
        with data_lock:
            shared_data["lights_on"] = True
            
    # Default: go straight, full speed
    target_steering = 0.0
    target_acceleration = 1.0
    action_decided = False
    
    red_token = next((t for t in tokens if t['color'] == 'red'), None)
    yellow_token = next((t for t in tokens if t['color'] == 'yellow'), None)
    green_token = next((t for t in tokens if t['color'] == 'green'), None)
    
    # Red token avoidance
    if red_token:
        lane = get_lane(red_token['cx'], frame_width)
        if lane == 1:
            target_steering = -0.6 if red_token['cx'] > frame_width / 2 else 0.6
            action_decided = True

    # Yellow token caution
    if not action_decided and yellow_token:
        target_acceleration = 0.8
        target_steering = -0.4 if yellow_token['cx'] > frame_width / 2 else 0.4
        action_decided = True

    # Green token chasing
    if not action_decided and green_token:
        lane = get_lane(green_token['cx'], frame_width)
        if lane == 1:
            target_acceleration = 1.0
            target_steering = 0.0
        elif lane == 0:
            target_steering = -0.6
        elif lane == 2:
            target_steering = 0.6
        action_decided = True

    # Update state
    with data_lock:
        shared_data['acceleration'] = target_acceleration

    # Apply tap steering logic if we have an active steer command
    if target_steering != 0.0:
        tap_steer(target_steering)
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
    
    threading.Thread(target=setup_control_server, daemon=True).start()
    threading.Thread(target=setup_cameras, daemon=True).start()
    
    t_front_camera = RTTask("ReadFrontCamera", period=0.005, priority=TaskPriority.HIGH, execute_func=read_front_camera_task)
    t_processing = RTTask("ProcessingTask", period=0.005, priority=TaskPriority.MEDIUM, execute_func=processing_task)
    t_decision = RTTask("DecisionTask", period=0.010, priority=TaskPriority.MEDIUM, execute_func=decision_task)
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
