import os
import asyncio
from typing import List, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from query_engine import QueryEngine
from event import register_event_callback

app = FastAPI(
    title="Sentinel AI CCTV Web Server",
    description="Web service wrapping the Sentinel AI Object Tracking & ReID surveillance engine",
    version="1.0.0"
)

# Enable CORS for frontend/mobile apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

query_engine = QueryEngine()

# Active WebSocket connections for real-time security alerts
active_websockets: Set[WebSocket] = set()

# Callback triggered whenever a new tracking event is finalized and stored
def on_new_event(event_data: dict):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_alert(event_data))
    except RuntimeError:
        # Loop not running in this thread, submit to main thread loop if possible
        pass

# Register callback with event.py
register_event_callback(on_new_event)

async def broadcast_alert(event_data: dict):
    if not active_websockets:
        return
    disconnected = set()
    for ws in active_websockets:
        try:
            await ws.send_json({
                "type": "alert",
                "data": event_data
            })
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        if ws in active_websockets:
            active_websockets.remove(ws)

class NLQueryRequest(BaseModel):
    query: str
    session_mode: str = "single"

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "Sentinel AI CCTV Engine",
        "docs": "/docs"
    }

@app.get("/api/stats")
def get_stats():
    """
    Returns the aggregated security statistics and zone metrics report.
    """
    report_text = query_engine.generate_security_report()
    return {
        "report": report_text
    }

@app.post("/api/query")
def run_natural_language_query(request: NLQueryRequest):
    """
    Translates natural language questions into SQL logs.
    """
    results = query_engine.run_query(user_query=request.query, session_mode=request.session_mode)
    return {
        "query": request.query,
        "session_mode": request.session_mode,
        "results_count": len(results),
        "results": results
    }

@app.get("/api/events")
def get_events(global_id: int = None, camera_id: int = None, zone_id: int = None, session_mode: str = "single"):
    """
    Queries tracking events logs from the database using direct filters.
    """
    filters = {}
    if global_id is not None:
        filters["global_id"] = global_id
    if camera_id is not None:
        filters["camera_id"] = camera_id
    if zone_id is not None:
        filters["zone_id"] = zone_id
        
    results = query_engine.run_query(filters=filters, session_mode=session_mode)
    return {
        "filters": filters,
        "session_mode": session_mode,
        "results_count": len(results),
        "results": results
    }

@app.websocket("/ws/alerts")
async def websocket_alerts_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for streaming real-time security intrusion and GID match events.
    """
    await websocket.accept()
    active_websockets.add(websocket)
    try:
        while True:
            # Keep connection alive, listen for client messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)
    except Exception:
        if websocket in active_websockets:
            active_websockets.remove(websocket)
