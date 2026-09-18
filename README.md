# NewVis AI — Analytics Dashboard

Premium dark neon analytics dashboard UI for **NewVis AI**, featuring real-time camera monitoring, smart alerts, zone management, and comprehensive reporting for AI-powered video surveillance systems.

**Single-file frontend** (`backend/index.html`) — no build step, no framework, no bundler. Lightweight FastAPI mock backend (`web_server.py`) demonstrates API endpoints and UI interactions. 100% self-contained demo dashboard ready for integration with your backend.

---

## 🚀 Quick Start (3 Steps)

### ✅ Prerequisites
Before starting, make sure you have:
- **Python 3.9+** installed (check: `python --version` or `python3 --version`)
- **Git** installed (check: `git --version`)
- **pip** (comes with Python)

**Don't have Python?** Download from [python.org](https://www.python.org/downloads/) (Windows: choose "Add Python to PATH" during install)

### 📋 Step 1: Clone the Repository

**Windows (Command Prompt or PowerShell):**
```bash
git clone https://github.com/NewVisAI/NewVis.AI.git
cd "NewVis.AI"
```

**macOS/Linux (Terminal):**
```bash
git clone https://github.com/NewVisAI/NewVis.AI.git
cd NewVis.AI
```

### 🔧 Step 2: Create Virtual Environment & Install Dependencies

**Windows (Command Prompt):**
```bash
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

**Windows (PowerShell):**
```bash
python -m venv venv
venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

**macOS/Linux (Terminal):**
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 🎯 Step 3: Run the Dashboard

**All platforms (after activating venv):**
```bash
uvicorn web_server:app --host 0.0.0.0 --port 8002
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8002
INFO:     Application startup complete
```

📊 **Open in browser:** http://localhost:8002

To stop the server, press `Ctrl+C`

---

## ✅ Testing & Usage

### 1. Login to Dashboard

Navigate to http://localhost:8002

**Demo Credentials (any one of these):**
| Role | Username | Password |
|------|----------|----------|
| Developer | `developer` | `dev@sentinel` |
| Tech Team | `techteam` | `tech@sentinel` |
| Principal | `principal` | `principal@sentinel` |

Enter any of these credentials and click "Sign in"

### 2. Dashboard Features

Once logged in, you'll see:

- **Live Analytics**: Real-time camera monitoring and detection statistics
- **Smart Alerts**: Alert system with filtering and history
- **Zone Management**: Camera zones and restricted area configuration
- **Reports**: Analytics and incident reporting
- **User Management**: Role-based access control
- **Settings**: System configuration and preferences

### 3. Testing the UI

Explore these key sections:
- **Top Navigation**: Dark-themed header with branding and logout
- **Sidebar**: Navigation menu with icon labels
- **Camera Grid**: Live camera feed mockup with detection overlays
- **Stats Cards**: Real-time metrics with glowing effects
- **Alert List**: Scrollable alert history with timestamps
- **Modals**: Click buttons to see form modals and pop-ups

---

## 🛠️ System Requirements

| Component | Requirement |
|-----------|------------|
| **Python** | 3.9 or higher (3.10+ recommended) |
| **pip** | Latest version |
| **OS** | Windows, macOS, Linux |
| **RAM** | 512MB minimum |
| **Disk** | ~30MB (dependencies only) |
| **Port** | 8002 (configurable via `--port` flag) |
| **Browser** | Chrome, Firefox, Safari, Edge (modern version) |

---

## 📦 Dependencies

All dependencies are listed in `requirements.txt`:

```
fastapi>=0.110        # Web framework
uvicorn[standard]>=0.29  # ASGI server
pydantic>=2.0        # Data validation
```

Install all at once:
```bash
pip install -r requirements.txt
```

---

## 🔧 Advanced Configuration

### Running on a different port
```bash
uvicorn web_server:app --host 0.0.0.0 --port 8003
# Then visit: http://localhost:8003
```

### Running with auto-reload (development)
```bash
uvicorn web_server:app --host 0.0.0.0 --port 8002 --reload
```

### Custom configuration via environment variables
```bash
# Windows (Command Prompt)
set DEBUG=true
uvicorn web_server:app --port 8002

# Windows (PowerShell)
$env:DEBUG="true"
uvicorn web_server:app --port 8002

# macOS/Linux
export DEBUG=true
uvicorn web_server:app --port 8002
```

---

## 🐛 Troubleshooting

### ❌ "Address already in use" or "Port 8002 is already in use"

**Problem:** Another program is using port 8002.

**Solution 1: Use a different port**
```bash
uvicorn web_server:app --host 0.0.0.0 --port 8003
# Then visit: http://localhost:8003
```

**Solution 2: Kill the process using port 8002**

**Windows (Command Prompt, run as Admin):**
```bash
netstat -ano | findstr :8002
taskkill /PID <PID> /F
```

**Windows (PowerShell, run as Admin):**
```bash
Get-Process -Id (Get-NetTCPConnection -LocalPort 8002).OwningProcess
```

**macOS/Linux:**
```bash
lsof -i :8002
kill -9 <PID>
```

---

### ❌ "ModuleNotFoundError: No module named 'fastapi'"

**Problem:** Dependencies not installed or virtual environment not activated.

**Solution:**
1. Verify you're in the project directory: `cd <project-path>`
2. Activate virtual environment:
   - **Windows:** `venv\Scripts\activate`
   - **macOS/Linux:** `source venv/bin/activate`
3. Install dependencies: `pip install -r requirements.txt`
4. Verify: `pip list` should show `fastapi` and `uvicorn`

---

### ❌ "python: command not found" (macOS/Linux)

**Problem:** Python not installed or not in PATH.

**Solution:**
- Check if Python is installed: `python3 --version`
- Use `python3` instead of `python` for all commands:
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

---

### ❌ "'venv' is not recognized" (Windows)

**Problem:** Virtual environment not created or wrong path.

**Solution:**
1. Delete existing venv: `rmdir /s venv` (or `rm -r venv` in PowerShell)
2. Recreate: `python -m venv venv`
3. Activate: `venv\Scripts\activate`
4. Verify prompt shows `(venv)` at the start

---

### ❌ Browser shows "localhost refused to connect"

**Problem:** Server isn't running or isn't listening on port 8002.

**Solution:**
1. Check terminal output — look for: `Uvicorn running on http://0.0.0.0:8002`
2. If not present, restart server: `uvicorn web_server:app --host 0.0.0.0 --port 8002`
3. Try again: http://localhost:8002
4. If still failing, check for errors above "Port already in use"

---

### ❌ Login credentials don't work

**Problem:** Wrong credentials or server error.

**Solution:**
1. Use exact credentials from the table above (case-sensitive)
2. Check that server is running (see "Browser shows localhost refused" above)
3. Open browser DevTools (`F12`) → **Console** tab — look for error messages
4. Try a different role (e.g., use `developer` / `dev@sentinel`)

---

### ❌ Getting "Permission denied" errors

**Problem:** Virtual environment or file permissions issue.

**Solution (Windows):**
```bash
# Open Command Prompt as Administrator
# Then run activation and pip commands
venv\Scripts\activate
pip install -r requirements.txt
```

**Solution (macOS/Linux):**
```bash
# Add execute permission
chmod +x venv/bin/activate
source venv/bin/activate
pip install -r requirements.txt
```

---

### ✅ Still Stuck?

1. **Check terminal output:** Look for error messages above the "Uvicorn running" line
2. **Verify file structure:** You should have: `backend/index.html`, `web_server.py`, `requirements.txt`, `Dockerfile`, `README.md`
3. **Try fresh install:**
   ```bash
   deactivate  # Exit virtual environment
   rm -r venv  # Delete virtual environment
   python -m venv venv  # Create fresh one
   venv\Scripts\activate  # (Windows) or source venv/bin/activate (Mac/Linux)
   pip install -r requirements.txt
   uvicorn web_server:app --host 0.0.0.0 --port 8002
   ```

---

## 🎨 Design & Customization

### Project Layout

| Path | What it is |
| ---- | ---------- |
| `backend/index.html` | The entire dashboard frontend — markup, styles, and JS in one file |
| `web_server.py` | FastAPI mock backend: serves the dashboard, provides demo API endpoints |
| `requirements.txt` | Python dependencies (FastAPI, Uvicorn) |
| `Dockerfile` | Container image for production deployment |
| `README.md` | This file |

### Design System

The dashboard uses a **premium dark OLED theme** with neon accents:

**Color Palette**
```css
--bg:               #020617   /* Navy OLED background */
--purple:           #D946EF   /* Primary neon purple */
--cyan:             #00D9FF   /* Secondary neon cyan */
--text:             #F8FAFC   /* Light text on dark */
--text-dim:         #CBD5E1   /* Secondary text */
```

**Visual Effects**
- **Glassmorphism**: `backdrop-filter: blur(40px)` on all cards
- **Glow**: `box-shadow: 0 0 40px rgba(0, 217, 255, 0.3)` (cyan)
- **Animation Easing**: `cubic-bezier(0.16, 1, 0.3, 1)` (fluid bounce)

### Customizing the UI

1. Open `backend/index.html` in a text editor
2. Find the `<style>` block at the top (lines ~1-500)
3. Edit CSS custom properties:
   - Change colors: `--purple: #D946EF` → `--purple: #FF1493`
   - Adjust spacing: `--spacing: 16px`
   - Modify fonts: `--font-ui: 'Inter', sans-serif`
4. Save and refresh browser at http://localhost:8002

All UI elements use these tokens — edit once, update everywhere.

### Modifying Content

**Page Title:** Search for `NewVis AI — Campus Surveillance` in `backend/index.html`
**Branding:** All "NewVis AI" text can be replaced globally (Ctrl+H Find/Replace)
**Camera Zones:** Update the zone names in the HTML

---

## 🚢 Deployment

### Option 1: Docker (Easiest)

**Prerequisites:** Docker installed ([get Docker](https://www.docker.com/products/docker-desktop))

**Build and run:**
```bash
docker build -t newvis-dashboard .
docker run -p 8002:8002 newvis-dashboard
```

Then visit: http://localhost:8002

**To stop:** Press `Ctrl+C` or run `docker stop <container-id>`

### Option 2: Docker Compose (Both Services)

If you want to run **both website and dashboard** together:

**From the parent directory:**
```bash
docker-compose up -d
```

This starts:
- Website: http://localhost:8000
- Dashboard: http://localhost:8002

### Option 3: Production Server (Linux/macOS)

For production with multiple workers:
```bash
uvicorn web_server:app --host 0.0.0.0 --port 8002 --workers 4
```

See `DEPLOYMENT.md` (in parent directory) for advanced production setup (Nginx, systemd, cloud).

---

## 🔌 API Endpoints

The mock backend provides these endpoints for frontend development:

- `GET /` — Returns dashboard HTML
- `POST /api/login` — Login (returns mock token)
- `POST /api/logout` — Logout
- `GET /api/cameras` — List cameras (mock data)
- `GET /api/alerts` — Get alerts (mock data)
- `GET /api/reports/summary` — Report summaries (mock data)
- `GET /api/license` — License info (mock data)
- `WS /ws/alerts` — WebSocket for live alerts (mock stream)

Full API docs available at: http://localhost:8002/docs (when running with FastAPI docs enabled)

---

## 🛠️ Integrating with Real Backend

To connect this dashboard to your actual backend:

1. **Update API endpoints** in the HTML/JS to point to your server
2. **Modify authentication** in `web_server.py` or your actual backend
3. **Replace mock data** with real API calls
4. **Add error handling** for real network scenarios

See comments in `web_server.py` for integration points.

---

## 📋 Notes & Guidelines

### Git Ignore
- `venv/` — Virtual environment. Never commit.
- `.env` — Environment variables. Never commit.
- `__pycache__/` — Python cache. Never commit.

### Browser Support
- ✅ Chrome/Edge (full support)
- ✅ Firefox (full support)
- ✅ Safari (graceful degradation: plain blur instead of refraction)
- ⚠️ IE11 — Not supported

---

## 📞 Support & Feedback

- **Issues:** https://github.com/NewVisAI/NewVis.AI/issues
- **Discussions:** https://github.com/NewVisAI/NewVis.AI/discussions
- **Email:** support@newvis.ai

---

## 📄 License

NewVis AI — Premium Video Analytics Platform. All rights reserved.
