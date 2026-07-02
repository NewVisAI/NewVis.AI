import json
import os
from typing import Dict, List, Optional, Tuple

import cv2

ZONES_PATH = "zones.json"
ZONE_COLOR = (0, 128, 255)
PENDING_ZONE_COLOR = (255, 255, 0)
TEXT_COLOR = (255, 255, 255)
INFO_COLOR = (200, 200, 200)
ENTER_KEYS = {10, 13}


def _ensure_file_exists() -> None:
    if not os.path.exists(ZONES_PATH):
        with open(ZONES_PATH, "w", encoding="utf-8") as handle:
            json.dump({"zones": []}, handle, indent=2)


def _load_all_zones() -> List[Dict]:
    _ensure_file_exists()

    try:
        with open(ZONES_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError:
        return []

    return payload.get("zones", [])


def _persist_all_zones(zones: List[Dict]) -> None:
    with open(ZONES_PATH, "w", encoding="utf-8") as handle:
        json.dump({"zones": zones}, handle, indent=2)


def has_any_zones() -> bool:
    return len(_load_all_zones()) > 0


def get_all_zones() -> List[Dict]:
    return _load_all_zones()


def get_camera_zones(camera_id: int) -> List[Dict]:
    all_zones = _load_all_zones()
    return [z for z in all_zones if z.get("camera_id") == camera_id]


def _next_zone_id(existing_zones: List[Dict]) -> int:
    ids = [z.get("id", 0) for z in existing_zones]
    return max(ids, default=0) + 1


def _normalize_point(value: float, extent: int) -> int:
    if isinstance(value, float) and 0 <= value <= 1:
        return int(value * extent)
    return max(0, min(int(value), extent))


def _points_to_bbox(points: List[List[float]], frame_shape: Tuple[int, int, int]) -> Dict:
    h, w = frame_shape[:2]
    xs = [_normalize_point(p[0], w) for p in points]
    ys = [_normalize_point(p[1], h) for p in points]

    x1 = max(0, min(xs))
    x2 = min(w, max(xs))
    y1 = max(0, min(ys))
    y2 = min(h, max(ys))

    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _points_to_pixel_polygon(points: List[List[float]], frame_shape: Tuple[int, int, int]) -> List[Tuple[int, int]]:
    if not points:
        return []

    h, w = frame_shape[:2]
    polygon = []

    for x_val, y_val in points:
        x = _normalize_point(x_val, w)
        y = _normalize_point(y_val, h)
        polygon.append((x, y))

    return polygon


def build_pixel_zones(zone_defs: List[Dict], frame_shape: Tuple[int, int, int]) -> List[Dict]:
    pixel_zones = []

    for zone in zone_defs:
        points = zone.get("points")
        if not points:
            continue

        pixel_polygon = _points_to_pixel_polygon(points, frame_shape)
        if len(pixel_polygon) < 3:
            continue

        bbox = _points_to_bbox(points, frame_shape)
        bbox["id"] = zone.get("id")
        bbox["name"] = zone.get("name", f"Zone {zone.get('id')}")
        bbox["polygon"] = pixel_polygon
        bbox["max_occupancy"] = zone.get("max_occupancy", 3)
        bbox["restricted"] = bool(zone.get("restricted", False))
        bbox["active_hours"] = zone.get("active_hours")
        bbox["school_days"] = zone.get("school_days")

        pixel_zones.append(bbox)

    return pixel_zones


def persist_camera_zones(camera_id: int, zone_defs: List[Dict]) -> None:
    all_zones = [z for z in _load_all_zones() if z.get("camera_id") != camera_id]
    all_zones.extend(zone_defs)
    _persist_all_zones(all_zones)


def overwrite_zones(zone_defs: List[Dict]) -> None:
    _persist_all_zones(zone_defs)


def _find_zone_index(zones: List[Dict], camera_id: int, zone_id: int) -> Optional[int]:
    for index, zone in enumerate(zones):
        if zone.get("camera_id") == camera_id and zone.get("id") == zone_id:
            return index
    return None


def set_zone_alert_rules(
    camera_id: int,
    zone_id: int,
    restricted: Optional[bool] = None,
    active_hours: Optional[Dict[str, str]] = None,
    school_days: Optional[List[str]] = None,
) -> bool:
    zones = _load_all_zones()
    index = _find_zone_index(zones, camera_id, zone_id)
    if index is None:
        return False

    if restricted is not None:
        zones[index]["restricted"] = restricted
    if active_hours is not None:
        zones[index]["active_hours"] = active_hours
    if school_days is not None:
        zones[index]["school_days"] = school_days

    _persist_all_zones(zones)
    return True


WEEKDAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEFAULT_SCHOOL_DAYS = ["mon", "tue", "wed", "thu", "fri"]


def _prompt_zone_time(label: str) -> Optional[str]:
    value = input(f"    {label} (HH:MM, blank to skip): ").strip()
    if not value:
        return None
    try:
        hours, minutes = value.split(":")
        int(hours), int(minutes)
    except ValueError:
        print("    ⚠️ Invalid time format, skipping.")
        return None
    return value


def _prompt_school_days() -> List[str]:
    raw = input(
        "    School days this zone is active (comma-separated mon-sun, "
        "Enter for Mon-Fri, 'all' for every day): "
    ).strip().lower()

    if not raw:
        return DEFAULT_SCHOOL_DAYS
    if raw == "all":
        return list(WEEKDAY_CODES)

    days = [day.strip()[:3] for day in raw.split(",") if day.strip()]
    valid_days = [day for day in days if day in WEEKDAY_CODES]
    if not valid_days:
        print("    ⚠️ No valid days recognized, defaulting to Mon-Fri.")
        return DEFAULT_SCHOOL_DAYS
    return valid_days


def configure_zone_rules_interactive(camera_id: int) -> None:
    zones = get_camera_zones(camera_id)
    if not zones:
        print(f"⚠️ No zones found for camera {camera_id}.")
        return

    for zone in zones:
        zone_id = zone.get("id")
        print(f"\nZone {zone_id} - {zone.get('name', 'Unnamed')}")

        restricted_choice = input(
            "  Mark as restricted area? Alerts on ANY entry (y/n, blank = leave unchanged): "
        ).strip().lower()
        restricted = {"y": True, "n": False}.get(restricted_choice)

        active_hours = None
        school_days = None
        set_hours_choice = input(
            "  Set allowed hours/school days for this zone? Entries outside them "
            "(and on non-school days) will alert (y/n): "
        ).strip().lower()
        if set_hours_choice == "y":
            start = _prompt_zone_time("Start time")
            end = _prompt_zone_time("End time")
            if start and end:
                active_hours = {"start": start, "end": end}
                school_days = _prompt_school_days()

        if restricted is not None or active_hours is not None:
            set_zone_alert_rules(
                camera_id,
                zone_id,
                restricted=restricted,
                active_hours=active_hours,
                school_days=school_days,
            )
            print(f"  ✅ Updated alert rules for zone {zone_id}")


def _template_to_zones(camera_config: Dict, start_id: int) -> List[Dict]:
    templates = camera_config.get("zone_templates", [])
    camera_id = camera_config["camera_id"]
    zones: List[Dict] = []

    for template in templates:
        x1 = template.get("x1")
        y1 = template.get("y1")
        x2 = template.get("x2")
        y2 = template.get("y2")

        if x1 is None or y1 is None or x2 is None or y2 is None:
            continue

        points = [
            [min(x1, x2), min(y1, y2)],
            [max(x1, x2), min(y1, y2)],
            [max(x1, x2), max(y1, y2)],
            [min(x1, x2), max(y1, y2)],
        ]

        zone = {
            "id": start_id,
            "camera_id": camera_id,
            "name": template.get("name", f"Zone {start_id}"),
            "points": points,
        }

        zones.append(zone)
        start_id += 1

    return zones


class ZoneDrawer:
    def __init__(self, frame, camera_id, camera_name, start_id: int):
        self._origin_frame = frame.copy()
        self.camera_id = camera_id
        self.window_name = f"Draw Zones - {camera_name}"
        self.zones: List[Dict] = []
        self.drawing = False
        self.start_point = None
        self.current_rect = None
        self.pending_rect: Optional[Tuple[int, int, int, int]] = None
        self.next_id = start_id
        self.height, self.width = frame.shape[:2]

        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.window_name, self._mouse_callback)

    def _mouse_callback(self, event, x, y, *_) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.start_point = (x, y)

        elif event == cv2.EVENT_MOUSEMOVE and self.drawing and self.start_point:
            self.current_rect = (self.start_point[0], self.start_point[1], x, y)

        elif event == cv2.EVENT_LBUTTONUP and self.drawing and self.start_point:
            self.drawing = False
            self.pending_rect = self._normalize_rectangle(self.start_point, (x, y))
            self.current_rect = None
            self.start_point = None

    def _normalize_rectangle(self, start, end) -> Optional[Tuple[int, int, int, int]]:
        x1, y1 = start
        x2, y2 = end
        min_x, max_x = sorted((x1, x2))
        min_y, max_y = sorted((y1, y2))
        width = max_x - min_x
        height = max_y - min_y

        if width < 10 or height < 10:
            return None

        return min_x, min_y, max_x, max_y

    def _finalize_rectangle(self, rect: Tuple[int, int, int, int]) -> None:
        min_x, min_y, max_x, max_y = rect

        points = [
            [min_x / self.width, min_y / self.height],
            [max_x / self.width, min_y / self.height],
            [max_x / self.width, max_y / self.height],
            [min_x / self.width, max_y / self.height],
        ]

        zone = {
            "id": self.next_id,
            "camera_id": self.camera_id,
            "name": f"Zone {self.next_id}",
            "points": points,
        }

        self.next_id += 1
        self.zones.append(zone)
        self.pending_rect = None

        print(f"✅ Captured zone {zone['id']} for camera {self.camera_id}")

    def _draw_rectangle(self, canvas, rect, color, label: str) -> None:
        x1, y1, x2, y2 = rect
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            canvas,
            label,
            (x1, max(10, y1 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            TEXT_COLOR,
            1,
        )

    def _draw_zones(self, canvas) -> None:
        for zone in self.zones:
            bbox = _points_to_bbox(zone.get("points", []), canvas.shape)
            self._draw_rectangle(
                canvas,
                (bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]),
                ZONE_COLOR,
                zone.get("name", f"Zone {zone['id']}"),
            )

        if self.current_rect:
            self._draw_rectangle(canvas, self.current_rect, PENDING_ZONE_COLOR, "Drawing")

        if self.pending_rect:
            self._draw_rectangle(canvas, self.pending_rect, PENDING_ZONE_COLOR, "Pending save")

    def _draw_instructions(self, canvas) -> None:
        instructions = [
            "Left click + drag to draw a zone",
            "S save zone | Z delete last zone | Enter finish | Q/Esc cancel",
            f"Saved zones: {len(self.zones)}",
        ]

        for idx, text in enumerate(instructions):
            cv2.putText(
                canvas,
                text,
                (10, 20 + idx * 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                INFO_COLOR,
                1,
            )

    def run(self) -> List[Dict]:
        while True:
            canvas = self._origin_frame.copy()
            self._draw_zones(canvas)
            self._draw_instructions(canvas)
            cv2.imshow(self.window_name, canvas)
            key = cv2.waitKey(30) & 0xFF

            if key in (ord("s"), ord("S")) and self.pending_rect:
                self._finalize_rectangle(self.pending_rect)
                continue

            if key in (ord("z"), ord("Z")) and self.zones:
                removed_zone = self.zones.pop()
                print(f"🗑️ Removed zone {removed_zone['id']} from camera {self.camera_id}")
                continue

            if key in ENTER_KEYS:
                break

            if key in (27, ord("q"), ord("Q")):
                self.zones = []
                break

        cv2.destroyWindow(self.window_name)
        return self.zones


def draw_camera_zones(camera_config: Dict, frame, start_id: int) -> List[Dict]:
    drawer = ZoneDrawer(frame, camera_config["camera_id"], camera_config["name"], start_id)
    return drawer.run()


def ensure_camera_zones(camera_config: Dict, frame) -> List[Dict]:
    camera_id = camera_config["camera_id"]
    return build_pixel_zones(get_camera_zones(camera_id), frame.shape)
