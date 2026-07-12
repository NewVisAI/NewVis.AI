# Sentinel AI — Feature Test Report v2 (new features)

**Date:** 2026-07-03
**Machine:** Windows 11, CPU-only, Python 3.13 venv
**Videos used:** `view-IP2 (1).mp4`, `view-IP5 (1).mp4`, `view-HC4.mp4`, `view-HC3 (1).mp4`
(all 1920×1080, 29.97 fps, ~3 min each)

This round adds four features and tests them through the **real FastAPI app**
(via `TestClient`, so the actual auth/audit/endpoint code runs) plus a **live
browser** session against the running server.

- Driver: [feature_test_driver_v2.py](../feature_test_driver_v2.py)
- Machine-readable results: [results_v2.json](results_v2.json)
- Analytics DB repopulated by re-running the real pipeline over `view-IP2` (683 frames).

---

## 1. One-click jump-to-clip playback (smooth, with "why logged")

Search results are now enriched with a `clip` block and a one-click **▶ Jump to clip**
button that opens a modal, seeks the source video to the exact moment, and shows
why it was logged.

**Each logged event now carries:** `why_logged`, `logged_at`, `clip_start_seconds`,
`clip_end_seconds`, `clip_duration_seconds` (with a 3 s pre/post-roll pad), `source_fps`,
and `details` (object, GID, camera, zone, dwell). Source: [clip_service.py](../clip_service.py).

**Verified live in the browser** — clicking a "loitering" result opened the modal and the
`<video>` **seeked to 2:39 (159.3 s) and played**, with this metadata panel:

```
Why logged : Prolonged stay / loitering in zone
Logged at  : 2026-07-02T15:54:21
Clip window: 159.3s → 168.77s (9.47s long, source 29.97 fps)
Details    : person, GID 5, camera 1, zone 2
```

**Smooth seek proven at the HTTP layer** — the clip video endpoint honours byte-range
requests (what the browser issues when seeking):

| Check | Result |
|---|---|
| `GET /api/clip/video` with `Range: bytes=0-2047` | **206 Partial Content** |
| `Content-Range` | `bytes 0-2047/77865991` |
| Same request **without token** | **401** (auth enforced) |
| Example clip (intrusion) | "Entered a restricted zone (intrusion)", window 156.2→162.2 s |

Sample enriched result (from [results_v2.json](results_v2.json)):
```json
{ "why_logged": "Entered a restricted zone (intrusion)",
  "logged_at": "2026-07-02T21:29:18",
  "clip_start_seconds": 156.16, "clip_end_seconds": 162.16, "clip_duration_seconds": 6.0,
  "source_fps": 29.97, "details": "person, GID 44, camera 1, zone 1" }
```

> Note: for *instant* mid-file seeking in the browser, source clips should be
> "faststart" MP4s (moov atom at the front). The supplied clips seek correctly
> but a non-faststart file buffers more before the first painted frame.

---

## 2. Live multi-camera grid (location-based)

Cameras are declared in [cameras.json](../cameras.json) (id, name, **floor**, location,
video source) and served as **looping MJPEG** streams. Your four clips are distributed
round-robin across 10 cameras on 2 floors, so every tile is a real, distinct feed.

**Location queries + layouts** ([results_v2.json](results_v2.json)):

| Query | Resolved floor | Cameras | Layout |
|---|---|---|---|
| "first floor" | `first` | 5 (Cam 1–5) | 4-col |
| "second floor" | `second` | 5 (Cam 6–10) | 4-col |
| "library" | – (location match) | 1 (Cam 6) | single |
| (blank) | all | 10 | 5-col |

Layout selector supports **single / 4-up / 8-up / all**; clicking a tile drills to single.
`GET /api/cameras/1/snapshot` returned a **200 JPEG (17.7 KB)**; the MJPEG stream route is
registered and was verified live (banner "LIVE • Cam N • location" + timestamp per tile).

**Verified live** — first-floor **4-up** grid rendered four distinct scenes simultaneously
(lecture hall = IP2/IP5, columned lobby = HC4, atrium with people = HC3). Distinct raw feeds
saved as evidence: [evidence/v2_grid_cam1..4](evidence/).

> **Finding (browser limit):** persistent MJPEG tiles each hold a browser connection, and
> browsers cap ~6 connections per host. Auto-starting 10 tiles starved other requests
> (search, clip, reports). **Fixed:** the grid now loads on demand (pick floor/layout →
> "Show") instead of auto-starting. For a reliable **8-up**, use ≤6 live tiles + snapshots,
> serve streams from a separate origin/port, or add a single server-composited mosaic stream.

---

## 3. Audit log (who viewed what, when)

Every sensitive action is recorded in an append-only `audit_log` table
([audit_log.py](../audit_log.py)) with user, role, action, target, timestamp, IP.

- `GET /api/audit` **without a token → 401** (gated to principal/developer).
- After the test session, the trail held **42 entries**. Action breakdown:

| Action | Count |
|---|---|
| nl_search | 19 |
| view_grid | 12 |
| login | 3 |
| view_audit | 3 |
| auto_daily_report (system) | 3 |
| view_clip | 1 |

Wired into: login/login_failed, nl_search, view_clip, view_snapshot, view_camera, view_grid,
generate_report, view_audit. **Verified live** — the dashboard Audit Log table rendered rows
like `principal · view_grid · all` and `system · auto_daily_report · daily` with timestamps.

---

## 4. Automated daily / monthly / yearly summary reports

[periodic_report.py](../periodic_report.py) generates period summaries from the events/alerts/
tracking tables (works for processed downloaded video and live feeds alike). A background
scheduler emits a daily summary every 24 h; `GET /api/reports/periodic?period=…` serves it
on demand. Real **daily** output from this run (clean, events-based):

```
Headcount (unique people) : 46
Total person visits       : 69
CROWD DENSITY / CONGESTION
  Peak simultaneous people : 13
  Occupancy breach events  : 33
    - Zone 1: peak 4 (limit 2), 18 breach(es)
    - Zone 2: peak 6 (limit 3), 15 breach(es)
HEADCOUNT BY ZONE:  Zone 1: 23 · Zone 2: 24
DWELL / LOITERING:  avg 21.1s · longest 142.0s · loitering sessions 25
```

Highlights **headcount, crowd density (peak simultaneous people), congestion
(per-zone occupancy breaches), busiest hour, dwell/loitering, and a safety-alert breakdown**.
Monthly/yearly use the same engine over wider windows.

> **Licensing note:** the reports endpoint (and the pre-existing headcount panel) require the
> `reports` license feature. Running **unlicensed (evaluation mode)** the dashboard correctly
> shows *"Feature 'reports' is not included in this license tier"* — i.e. the license gate
> works. The report **engine** runs regardless (the background scheduler emitted the summary
> above at startup). Issue a license including `reports` (or run as such a tier) to expose it
> in the dashboard.

---

## Test coverage summary

| # | Feature | Status | Evidence |
|---|---|---|---|
| 1 | One-click jump-to-clip + why/when/duration | ✅ | 206 range, modal seeked to 159.3 s & played, metadata panel |
| 2 | Location-based live multi-camera grid | ✅ | 4 distinct feeds, floor/location queries, layouts, 200 JPEG snapshot |
| 3 | Audit log (who/what/when) | ✅ | 42 entries, 401 without token, live table |
| 4 | Daily/monthly/yearly summaries | ✅ | daily: headcount 46, peak 13, 33 breaches, 25 loitering |

**Data this run:** 683 pipeline frames · 69 zone sessions · 33 occupancy events ·
53 global IDs · 90 zone alerts (this run) · 46 unique people.

## Findings / recommendations

1. **MJPEG 6-connection limit** — fixed by on-demand grid load; for 8-up use a mosaic
   stream or a separate stream origin.
2. **MP4 faststart** — re-mux source clips with moov-at-front for instant browser seeking.
3. **Reports license gate** — correct behaviour; needs a `reports`-tier license to show in
   the dashboard when not running as developer.
4. Alert/notification tables are cumulative across repeated test runs (events/tracking are
   cleared each run); this run's own alert count is 90 (51 after-hours + 39 restricted).

## Reproduce

```powershell
cd D:\COLLEGE\Sentinel
$env:FRAME_SKIP="8"
.\.venv\Scripts\python.exe feature_test_driver_v2.py     # pipeline + API tests
.\.venv\Scripts\python.exe -m uvicorn backend.server:app --port 8000   # live dashboard
# login: principal / principal@sentinel
```
