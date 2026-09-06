from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os
import json
from datetime import datetime

app = FastAPI(title="Sentinel AI Dashboard")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index():
    """Serve the main dashboard"""
    return FileResponse("backend/index.html", media_type="text/html")

@app.post("/api/login")
async def login(request_data: dict):
    """Handle login"""
    username = request_data.get("username")
    password = request_data.get("password")

    # Demo credentials
    users = {
        "developer": "dev@sentinel",
        "techteam": "tech@sentinel",
        "principal": "principal@sentinel"
    }

    role_map = {
        "developer": "developer",
        "techteam": "tech",
        "principal": "principal"
    }

    if username in users and users[username] == password:
        return {
            "token": f"token_{username}_{datetime.now().timestamp()}",
            "username": username,
            "role": role_map[username]
        }
    return JSONResponse({"detail": "Invalid credentials"}, status_code=401)

@app.post("/api/logout")
async def logout():
    """Handle logout"""
    return {"status": "logged out"}

@app.get("/healthz")
async def healthz():
    """Health check"""
    return {"status": "ok"}

@app.get("/api/cameras")
async def get_cameras():
    """Get camera list"""
    return {
        "cameras": [
            {"id": 1, "name": "Main Entrance", "status": "online", "floor": "Ground"},
            {"id": 2, "name": "Corridor A", "status": "online", "floor": "Ground"},
            {"id": 3, "name": "Cafeteria", "status": "online", "floor": "Ground"},
            {"id": 4, "name": "Parking Lot", "status": "online", "floor": "Parking"},
        ]
    }

@app.get("/api/reports/summary")
async def get_summary(period: str = "all"):
    """Get summary stats"""
    return {
        "headcount": 245,
        "events_today": 12,
        "alerts": 3,
        "cameras": 4
    }

@app.post("/api/query")
async def query_events(request_data: dict):
    """Query events"""
    return {
        "results_count": 0,
        "results": []
    }

@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    """Get alerts"""
    return {"alerts": []}

@app.get("/api/license")
async def get_license():
    """Get license info"""
    return {
        "mode": "evaluation",
        "client_id": "DEMO",
        "cameras_configured": 4,
        "max_cameras": 10
    }

@app.websocket("/ws/alerts")
async def websocket_alerts(websocket):
    """WebSocket for live alerts"""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "ping"})
    except:
        pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="info")
