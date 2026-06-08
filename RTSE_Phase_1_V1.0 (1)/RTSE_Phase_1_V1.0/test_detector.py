import socket
import struct
import cv2
import numpy as np
import token_detector

def get_test_frame():
    # Attempt to connect to the simulator's front camera socket (port 8080)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(('127.0.0.1', 8080))
        length_bytes = sock.recv(4)
        if length_bytes:
            image_length = int.from_bytes(length_bytes, 'little')
            received_bytes = b''
            while len(received_bytes) < image_length:
                packet = sock.recv(image_length - len(received_bytes))
                if not packet:
                    break
                received_bytes += packet
            if len(received_bytes) == image_length:
                np_arr = np.frombuffer(received_bytes, np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    print("Successfully captured live frame from simulator camera.")
                    sock.close()
                    return frame
        sock.close()
    except Exception:
        pass

    # Fallback: Generate a synthetic test frame (640x480 gray background)
    print("Simulator not detected. Generating synthetic test frame...")
    frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
    # Draw Green token (BGR: 0, 255, 0 -> HSV: H=60, S=255, V=255)
    cv2.rectangle(frame, (100, 150), (150, 200), (0, 255, 0), -1)
    # Draw Red token (BGR: 0, 0, 255 -> HSV: H=0, S=255, V=255)
    cv2.rectangle(frame, (300, 150), (330, 180), (0, 0, 255), -1)
    # Draw Yellow token (BGR: 0, 255, 255 -> HSV: H=30, S=255, V=255)
    cv2.rectangle(frame, (450, 150), (510, 210), (0, 255, 255), -1)
    return frame

if __name__ == '__main__':
    frame = get_test_frame()
    brightness = token_detector.detect_brightness(frame)
    tokens = token_detector.detect_tokens(frame)

    print(f"Mean Brightness: {brightness:.2f} (Low Brightness Alert: {brightness < 60.0})")
    print(f"Detected Tokens ({len(tokens)}):")
    for idx, t in enumerate(tokens):
        lane = token_detector.get_lane(t['cx'], frame.shape[1])
        print(f"  [{idx}] Color: {t['color']}, x: {t['x']}, y: {t['y']}, area: {t['area']:.1f}, cx: {t['cx']}, lane: {lane}")

    annotated = token_detector.annotate_frame(frame, tokens)
    cv2.imshow("Test Frame Annotations", annotated)
    print("Displaying frame. Press any key or wait 3 seconds...")
    cv2.waitKey(3000)
    cv2.destroyAllWindows()
