"""
Camera registry + location model for the live multi-camera grid.

Cameras are declared in cameras.json, each tagged with a floor and a human
location. This module loads that registry and answers location queries like
"first floor" (with alias resolution) so the dashboard can render a grid of
just the cameras on a given floor/location, or one camera, or all of them.

Deliberately decoupled from the tracking pipeline: a camera here is just a
named video source (file path today, RTSP URL in a real deployment). Swapping
in real per-camera footage is a one-line edit per camera in cameras.json.
"""

import json
import os
from typing import Dict, List, Optional

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cameras.json")


import threading

# Thread lock to protect concurrent reads / writes to cameras.json
_registry_lock = threading.Lock()


def _load() -> Dict:
    if not os.path.exists(REGISTRY_PATH):
        return {"floors": [], "cameras": []}
    with _registry_lock:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as handle:
            try:
                data = json.load(handle)
            except json.JSONDecodeError:
                return {"floors": [], "cameras": []}
    data.setdefault("floors", [])
    data.setdefault("cameras", [])
    return data


def list_floors() -> List[Dict]:
    return _load().get("floors", [])


def get_topology() -> List[Dict]:
    """Adjacency edges between cameras, each with a plausible max transit time.
    Used to gate cross-camera re-identification (a person can't teleport)."""
    return _load().get("topology", [])


def transit_seconds(cam_a: int, cam_b: int) -> Optional[float]:
    """Max seconds a person could take moving between two cameras, or None if
    they're not adjacent (so no hand-off is allowed). Same camera -> 0."""
    try:
        a, b = int(cam_a), int(cam_b)
    except (TypeError, ValueError):
        return None
    if a == b:
        return 0.0
    for edge in get_topology():
        try:
            if {int(edge.get("a")), int(edge.get("b"))} == {a, b}:
                return float(edge.get("max_transit_seconds", 30))
        except (TypeError, ValueError):
            continue
    return None


def list_cameras() -> List[Dict]:
    """All cameras, minus internal-only fields, with a stream URL hint."""
    cams = _load().get("cameras", [])
    return [_public_view(c) for c in cams]


def get_camera(camera_id: int) -> Optional[Dict]:
    for cam in _load().get("cameras", []):
        if int(cam.get("id")) == int(camera_id):
            return cam
    return None


def _public_view(cam: Dict) -> Dict:
    return {
        "id": cam.get("id"),
        "name": cam.get("name"),
        "floor": cam.get("floor"),
        "location": cam.get("location"),
        "stream_url": f"/api/cameras/{cam.get('id')}/stream",
        "source_present": (
            bool(cam.get("source")) and (
                str(cam.get("source")).startswith(("rtsp://", "http://", "https://")) or
                os.path.exists(str(cam.get("source")))
            )
        ),
    }


def resolve_floor(query: str) -> Optional[str]:
    """Map free text ("first floor", "1st floor", "floor 1") to a floor id."""
    if not query:
        return None
    q = query.strip().lower()
    for floor in _load().get("floors", []):
        if q == str(floor.get("id", "")).lower() or q == str(floor.get("label", "")).lower():
            return floor.get("id")
        for alias in floor.get("aliases", []):
            if alias.lower() in q or q in alias.lower():
                return floor.get("id")
    return None


def cameras_for_query(query: Optional[str] = None, floor: Optional[str] = None) -> List[Dict]:
    """
    Cameras matching a location query. Precedence:
      explicit floor id  ->  floor resolved from free text  ->  location substring
      ->  (nothing matched) all cameras.
    """
    cams = _load().get("cameras", [])

    resolved_floor = floor or (resolve_floor(query) if query else None)
    if resolved_floor:
        matched = [c for c in cams if str(c.get("floor")) == str(resolved_floor)]
        if matched:
            return [_public_view(c) for c in matched]

    if query:
        q = query.strip().lower()
        matched = [c for c in cams if q in str(c.get("location", "")).lower()
                   or q in str(c.get("name", "")).lower()]
        if matched:
            return [_public_view(c) for c in matched]

    return [_public_view(c) for c in cams]


def suggested_layout(count: int) -> Dict:
    """Grid dimensions for N tiles (1 / 4 / 8 / more)."""
    if count <= 1:
        return {"tiles": max(count, 1), "cols": 1, "rows": 1}
    if count <= 4:
        return {"tiles": count, "cols": 2, "rows": 2}
    if count <= 8:
        return {"tiles": count, "cols": 4, "rows": 2}
    cols = 5
    rows = (count + cols - 1) // cols
    return {"tiles": count, "cols": cols, "rows": rows}
