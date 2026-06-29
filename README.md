# 🎥 Sentinel AI CCTV Surveillance Engine

Sentinel AI is an intelligent, single-camera CCTV surveillance ecosystem that transforms traditional video streams into a searchable, metadata-indexed event database. It enables real-time multi-target tracking, cross-time identity retention (ReID), intrusion zone alerts, and natural language queries.

---

## ✨ Features & Architecture

```
[ CCTV Video / RTSP Feed ]
           │
           ▼
[ Object Detection ] (YOLOv8 / RT-DETR with Auto-GPU Acceleration)
           │
           ▼
[ Local Track Association ] (ByteTrack - Ultra-fast CPU tracking)
           │
           ▼
[ Neural Re-Identification ] (ResNet50 Deep Feature Extractor - 2048-dim)
           │
           ├─► (Lazy ReID Stride: Runs neural model only every 15 frames for active tracks)
           ▼
[ Global Identity Manager ] (Two-Stage Similarity Checking + Temporal Decay)
           │
           ├─► (Active Zone Polygon Intrusions check)
           ▼
[ Database & API Gateway ] (SQLite Metadata Index & FastAPI WebSockets Server)
```

*   **🧠 Hybrid Detection Model**: Interactive startup prompt allows choosing between **YOLOv8 Nano** (CPU/Edge optimized) and **RT-DETR Large** (high-accuracy Transformer). Supports automatic CUDA GPU acceleration.
*   **⚡ ByteTrack Local Tracking**: Uses state-of-the-art bounding box IoU association to track moving objects with high FPS and minimal ID switching.
*   **👤 ResNet50 Neural ReID**: Extracts robust 2048-dimensional appearance embeddings, making identity tracking invariant to lighting shifts and posture changes.
*   **📉 Lazy ReID Embedding Stride**: Only runs the ResNet50 network for new tracks or once every 15 frames, reducing CPU/GPU inference load by **over 90%** for active tracks.
*   **⏱ Adaptive Frame Skipping**: Automatically scales frame skips up to 15 frames on-the-fly based on processing latency to prevent feed lag on live RTSP streams.
*   **📶 RTSP Auto-Reconnection**: Automatically attempts to reconnect and re-initialize streams up to 5 times during network drops before termination.
*   **📊 Zone Analytics & Dwell Time Profiler**: Computes average dwell times, peak hours of day, and occupant counts per polygon zone.
*   **🌐 FastAPI Web Backend**: Exposes a REST API for natural language queries and a WebSocket channel (`/ws/alerts`) for real-time security alerts.

---

## 🚦 Quick Start Guide

### 1. Prerequisites
Ensure you have **Python 3.10+** installed on your system.

### 2. Installation
Clone this repository to your local system, navigate to the directory, and set up your virtual environment:

```bash
# Create a virtual environment
python -m venv .venv

# Activate the virtual environment
# On Windows:
.venv\Scripts\activate
# On Mac/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 🚀 Running the Applications

Ensure your virtual environment is **activated** before running any commands.

### A. Run Live Surveillance Mode
Process a video file or live RTSP stream. The program will prompt you to enter the video source path and choose a detection model (YOLOv8 or RT-DETR).

```bash
python app.py
```
*   **Controls**:
    *   `SPACE`: Pause / Play feed.
    *   `LEFT/RIGHT ARROWS`: Seek backwards / forwards.
    *   `Q` or `ESC`: Quit surveillance mode.

### B. Run natural Language Search Console
Search through recorded events using natural language (e.g. *"person entering restricted zone at night"* or *"car staying in zone 2"*).

```bash
python search_console.py
```

### C. Run Pipeline Benchmarking Profiler
Test your hardware capability. This runs the surveillance engine for 100 frames and prints a detailed latency breakdown (Ms spent in Decode vs. Detection vs. Tracking vs. ReID) and cache hit ratio statistics.

```bash
python benchmark_pipeline.py
```

### D. Start FastAPI Web Server Backend
Host the REST API endpoints and WebSocket alert stream locally:

```bash
python -m uvicorn backend.server:app --host 127.0.0.1 --port 8000
```
*   **Swagger API UI Docs**: Go to `http://127.0.0.1:8000/docs` in your web browser to interactively test endpoints.
*   **WebSocket Stream**: Connect to `ws://127.0.0.1:8000/ws/alerts` to stream real-time intrusion alarms.

---

## 🧪 Testing
Run the automated test suite to verify ReID threshold mappings and database integrity:

```bash
python test_reid.py
```
