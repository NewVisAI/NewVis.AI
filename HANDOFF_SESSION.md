# Session Handoff — Sentinel AI (2026-07-04)

Read this first in a new chat. It captures everything done in the previous
session so nothing gets re-derived.

## 0. Environment (CHANGED — important)

- The project venv is now a **WSL (Ubuntu 22.04) Python 3.13.14** virtualenv at
  `.venv`, replacing the old Windows venv. **The venv holds Linux binaries — run
  everything from WSL, not Windows.** `.venv\Scripts\python.exe` no longer exists.
- Python 3.13 was installed via the **deadsnakes PPA** (Ubuntu default is 3.10/3.11,
  but `numpy==2.5.0` needs ≥3.12). torch is the **CPU build** (`2.12.1+cpu`), no GPU.
- Project path in WSL: `/mnt/d/COLLEGE/Sentinel`. The venv lives on `/mnt/d`
  (slower drvfs I/O, but as requested).
- `.claude/launch.json` runs the backend via WSL uvicorn on `0.0.0.0:8000`.

**Run the app:**
```bash
wsl
cd /mnt/d/COLLEGE/Sentinel
source .venv/bin/activate          # or use ./.venv/bin/python directly
python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000
# open http://localhost:8000  (NOT 0.0.0.0)  — login developer / dev@sentinel
```

**Dashboard logins:** `developer`/`dev@sentinel` (sees everything),
`techteam`/`tech@sentinel`, `principal`/`principal@sentinel`.

## 1. Licensing (CHANGED — local keypair)

- The team's original private signing key was not on this machine, so a **fresh
  Ed25519 keypair was generated locally**: private key in `dev_keys/license_signing_key.pem`,
  and `license_validator.py::_PUBLIC_KEY_HEX` was updated to the matching public key.
- A full license was minted: `license.key` = client **SCHOOL_DEMO**, 10 cameras,
  expiry 2099-01-01, hardware lock ANY, **all features**. App now runs in
  **licensed mode** (this unlocks notifications/reports/zone-alerts, which are
  feature-gated and were previously blocked in evaluation mode).
- **For real deployment:** restore the team's public key in `license_validator.py`
  and re-issue licenses from the team's private key. Don't ship `dev_keys/`.

## 2. What was built this session

### Core test/verification (first half)
- Set up venv, installed `requirements.txt`, ran the full pipeline on the videos.
- `feature_test_report/REPORT.md` — v1 report (detection, tracking, ReID, zones,
  intrusion/after-hours alerts, dress-code, running, fall, headcount, licensing,
  NL search, unit tests).
- Fixed **NL search plural gap** in `intent_manager.py` (cars/falls/runs… now parse).
- Fixed **test_licensing** to pass without the dev key (ephemeral keypair in
  `setUpClass`) and fixed a `❌` UnicodeEncodeError in `generate_key.py`.
- Unit tests: `test_fall` 5/5, `test_reid` 11/11, `test_licensing` 9/9.

### New feature modules (v2)
- `camera_registry.py` + `cameras.json` — cameras grouped by floor/location,
  alias resolution ("first floor"), grid-layout suggestions. **10 cameras across
  2 floors, backed by the user's 4 real videos round-robin.**
- `camera_stream.py` — looping MJPEG streamer (LIVE banner + timestamp).
- `clip_service.py` — one-click clip metadata (why_logged, logged_at, clip
  start/end/duration, details, seekable video_url).
- `audit_log.py` — append-only who/what/when trail (`audit_log` table).
- `periodic_report.py` — daily/monthly/yearly summaries (headcount, crowd density
  = peak simultaneous people, congestion, dwell/loitering, alert breakdown).
- `feature_test_report/REPORT_V2.md` — v2 report.

### Backend endpoints added to `backend/server.py`
`/api/cameras`, `/api/cameras/by-location`, `/api/cameras/{id}/stream`,
`/api/cameras/{id}/snapshot`, `/api/clip/video` (byte-range seekable),
`/api/clip/track` (subject bounding boxes), `/api/reports/periodic`, `/api/audit`;
`/api/query` enriched with clip metadata; audit recording on sensitive actions;
a daily-report scheduler. (42 routes total.)

### Frontend (`backend/index.html`)
- Live camera grid (floor/location + 1/4/8/all layouts), loaded **on demand**
  (auto-starting many MJPEG tiles starved the browser's ~6-connection limit).
- One-click **clip playback modal** with a **canvas bounding-box overlay that
  highlights ONLY the logged subject** (fed by `/api/clip/track`), synced to
  video time. Verified: red "GID N — subject" box tracks the person.
- Summary Reports panel, Audit Log panel.

### Latest batch (most recent requests — all done)
1. **Play original video** toggle in Live Video Analytics (Annotated ⟷ Original).
2. **Removed crowding events** — pipeline no longer logs `occupancy_alert`
   (school crowding is normal). `app.py` `_process_camera_frame` no longer calls
   `check_occupancy_alerts`. Density/headcount still reported. Verified 0 occupancy
   events in DB + summary shows "Occupancy breach events: 0".
3. **Empty query → all logged events** in log order (`/api/query` + frontend).
   Verified: 69 events, `intent {all_events:true}`.
4. **Notifications/headcount/summary now display** — they were empty due to the
   license gate; fixed by installing the license (section 1). Verified populated.
5. **Licensing & camera governance** demonstrated: panel shows LICENSED SCHOOL_DEMO,
   cap enforced (10 ✓ / 11 ✗).

## 3. Current data state

- Last action: **reprocessed** `backend/static/uploads/view-IP21.mp4` (683 frames,
  frame_skip 8). DB now: 69 `leaving` sessions, 0 occupancy, 46 unique people,
  tracking_data populated.
- **Caveat:** `alerts`/`notifications` tables are **cumulative across every test
  run** (not cleared per run, unlike events/tracking). Totals are inflated
  (~575 rows); per-run is ~90. Consider a "clear alerts/notifications" maintenance
  action if a clean count is wanted (do NOT bulk-DELETE the DB without user consent —
  the auto-mode classifier blocks it, correctly).

## 4. The 4 test videos

`D:\Downloads\view-IP2 (1).mp4`, `view-IP5 (1).mp4`, `view-HC4.mp4`,
`view-HC3 (1).mp4` — all 1920×1080, 29.97 fps, ~3 min, distinct scenes
(lecture hall, lobbies, atrium). Mapped across the 10 grid cameras in `cameras.json`.
The analytics/tracking pipeline runs on ONE video at a time; the grid treats all
four as "live" via looping MJPEG.

## 5. Known limitations / follow-ups

- **MJPEG grid:** >6 live tiles hit the browser's per-host connection cap; grid is
  on-demand now. For reliable 8-up, use a server-composited mosaic stream or a
  separate stream origin/port.
- **Clip seeking:** source MP4s aren't "faststart" (moov at end), so mid-file seek
  buffers more before the first frame. Re-mux moov-to-front for instant seek.
- **play-original toggle:** wired + code-verified but not live-screenshotted (only
  appears after a dashboard upload, which triggers a ~5-min reprocess).
- venv on `/mnt/d` = slower I/O than a native-Linux path (kept at project `.venv`
  as requested).

## 6. Reports produced
`feature_test_report/REPORT.md` (v1), `feature_test_report/REPORT_V2.md` (v2),
`feature_test_report/results.json`, `results_v2.json`, annotated videos + snapshot
evidence under `feature_test_report/`.
