import cv2
import numpy as np

def prepare_frame(frame):
    """
    Masks out regions that cause false detections:
    - Bottom 22% of frame: our own red car body
    - Top-left HUD corner: colored score numbers (green/red/yellow "0")
    - Top-right HUD corner: distance counters
    Returns a copy with masked regions blacked out. Original coordinates are preserved.
    """
    masked = frame.copy()
    h, w = masked.shape[:2]
    # Black out bottom 22% — our red car body
    masked[int(h * 0.78):, :] = 0
    # Black out top-left HUD (score counters with colored text)
    masked[0:int(h * 0.17), 0:int(w * 0.16)] = 0
    # Black out top-right HUD (distance counters)
    masked[0:int(h * 0.14), int(w * 0.77):] = 0
    return masked

def detect_tokens(frame):
    """
    Converts the BGR frame to HSV, detects Green, Red, and Yellow tokens 
    with defined HSV thresholds, rejects contours with area < 800 pixels,
    and returns a list of dictionaries representing detected tokens sorted by area descending.
    """
    if frame is None:
        return []

    # Mask out car body, HUD, and road edges before detection
    masked = prepare_frame(frame)

    hsv = cv2.cvtColor(masked, cv2.COLOR_BGR2HSV)
    tokens = []

    # HSV thresholds definitions
    # Green: H [35-85], S [80-255], V [80-255]
    green_lower = np.array([35, 80, 80])
    green_upper = np.array([85, 255, 255])
    
    # Red: Two ranges combined
    red_lower1 = np.array([0, 120, 70])
    red_upper1 = np.array([10, 255, 255])
    red_lower2 = np.array([170, 120, 70])
    red_upper2 = np.array([180, 255, 255])
    
    # Yellow: H [20-35], S [100-255], V [100-255]
    yellow_lower = np.array([20, 100, 100])
    yellow_upper = np.array([35, 255, 255])

    # Masks
    mask_green = cv2.inRange(hsv, green_lower, green_upper)
    
    mask_red1 = cv2.inRange(hsv, red_lower1, red_upper1)
    mask_red2 = cv2.inRange(hsv, red_lower2, red_upper2)
    mask_red = cv2.bitwise_or(mask_red1, mask_red2)
    
    mask_yellow = cv2.inRange(hsv, yellow_lower, yellow_upper)

    color_masks = [
        ("green", mask_green),
        ("red", mask_red),
        ("yellow", mask_yellow)
    ]

    for color_name, mask in color_masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < 800:
                continue
            x, y, w, h = cv2.boundingRect(c)
            if w < 20 or h < 20:
                continue
            aspect = w / float(h)
            if aspect > 4.0 or aspect < 0.25:
                continue
            
            cx = int(x + w // 2)
            tokens.append({
                "color": color_name,
                "x": int(x),
                "y": int(y),
                "w": int(w),
                "h": int(h),
                "area": area,
                "cx": cx
            })

    # Sort by area descending
    tokens.sort(key=lambda item: item["area"], reverse=True)
    return tokens

def get_lane(cx, frame_width, num_lanes=3):
    """
    Divides the frame width into num_lanes equal zones and returns 
    the index of the zone (e.g., 0 for left, 1 for center, 2 for right) where cx falls.
    """
    if frame_width <= 0:
        return 0
    lane_width = frame_width / num_lanes
    lane = int(cx / lane_width)
    return max(0, min(lane, num_lanes - 1))

def detect_brightness(frame):
    """
    Converts BGR frame to grayscale and returns the mean pixel value as a float (0.0 to 255.0).
    """
    if frame is None:
        return 0.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(np.mean(gray))

def annotate_frame(frame, tokens):
    """
    Draws bounding boxes, horizontal centers, and color labels on the frame 
    for visual debugging. Returns the annotated frame.
    """
    if frame is None:
        return None
    
    annotated = frame.copy()
    for token in tokens:
        color_name = token["color"]
        x, y, cx = token["x"], token["y"], token["cx"]
        # Find coordinates for bounding box. We can estimate size by area or compute it.
        # But we only stored x, y, cx, area.
        # To draw a precise rectangle, we could recalculate or estimate. Let's make sure
        # we can estimate size or just use a default bounding rect size, or wait:
        # Should we save w and h in the detect_tokens dict to draw them exactly?
        # The prompt says: Return a list of dicts: {"color": "green", "x": int, "y": int, "area": float, "cx": int}
        # Since we must return exactly those fields (or at least those fields), we can add "w" and "h" as optional fields
        # or we can compute an estimated side: side = int(np.sqrt(area))
        # Let's add 'w' and 'h' to the dicts in detect_tokens so that annotate_frame can draw them precisely,
        # but keep color, x, y, area, cx as requested.
        # Let's estimate side length from area or store w/h. Let's do side = int(np.sqrt(area)) or just store them.
        # Wait, storing w and h in the dict is extremely clean and doesn't violate the schema (which specifies those 5 fields).
        # Let's use the actual bounding box width and height if we have them, or estimate them.
        # Let's check: "For each valid contour, compute bounding rect and return a list of dicts: {"color": "green", "x": int, "y": int, "area": float, "cx": int}"
        # If we calculate side = int(np.sqrt(token["area"])), it might not match w and h if they are rectangles, 
        # but it works fine for display. However, let's keep track of w and h, or just estimate. 
        # Actually, let's just add 'w' and 'h' keys to the dict returned by detect_tokens, or compute them.
        # Let's read: "For each valid contour, compute bounding rect and return a list of dicts..."
        # If we return 'w' and 'h' too, it doesn't hurt. But we can also compute the box width and height.
        # Let's check: can we just save 'w' and 'h' in the dictionary? Yes! That makes annotation precise.
        # Let's adjust the detect_tokens to include 'w' and 'h'.
        w = token.get("w", int(np.sqrt(token["area"])))
        h = token.get("h", int(np.sqrt(token["area"])))
        
        # Determine color representation in BGR
        if color_name == "green":
            bgr_color = (0, 255, 0)
        elif color_name == "red":
            bgr_color = (0, 0, 255)
        elif color_name == "yellow":
            bgr_color = (0, 255, 255)
        else:
            bgr_color = (255, 255, 255)
            
        cv2.rectangle(annotated, (x, y), (x + w, y + h), bgr_color, 2)
        cv2.circle(annotated, (cx, y + h // 2), 3, (255, 0, 0), -1)
        label = f"{color_name.capitalize()} (A:{int(token['area'])})"
        cv2.putText(annotated, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, bgr_color, 1)
        
    return annotated
