# HackathonPro Workspace Rules

Welcome to the HackathonPro workspace. This project is optimized for single-device CCTV object tracking.

## Technical Constraints & Guidelines
- **Single-Camera Only**: Do not restore multi-device / multi-view tracking canvas features. The application is refactored for direct rendering of a single camera stream.
- **Dependencies**: Uses `ultralytics` for object detection and `deep-sort-realtime` for tracking.
- **Local Dev**: Run live tracking mode using `python app.py` and query events using `python search_console.py`.
