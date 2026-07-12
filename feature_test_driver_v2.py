"""
Sentinel AI — v2 feature test driver.

Repopulates the DB by running the real pipeline over the video, then exercises
the NEW features end-to-end through the actual FastAPI app (via TestClient, so
the real auth/audit/endpoint code runs):

  1. One-click jump-to-clip metadata on NL search results
  2. Location-based live multi-camera grid (list / by-location / snapshot / stream route)
  3. Audit log (who viewed what, when)
  4. Automated daily / monthly / yearly summary reports

Writes results to feature_test_report/results_v2.json.
"""

import io
import os
import sys
import json
import contextlib
from datetime import datetime

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.environ.setdefault("FRAME_SKIP", "8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feature_test_report")
os.makedirs(REPORT_DIR, exist_ok=True)
VIDEO = sys.argv[1] if len(sys.argv) > 1 else r"D:\Downloads\view-IP2 (1).mp4"

report = {"generated_at": datetime.now().isoformat()}


def log(msg):
    print(msg, file=sys.__stdout__, flush=True)


def repopulate_db():
    import feature_test_driver as ftd
    info = ftd.probe_video(VIDEO)
    log(f"[v2] pipeline re-run to repopulate DB (frame_skip={os.environ.get('FRAME_SKIP')})")
    res = ftd.run_pipeline(info)
    report["pipeline"] = {"processed_frames": res["processed_frames"], "video": info}
    log(f"[v2] pipeline done: {res['processed_frames']} frames")


def run_api_tests():
    from fastapi.testclient import TestClient
    import backend.server as srv

    results = {}
    with TestClient(srv.app) as client:
        # --- login ----------------------------------------------------------
        pw = os.environ.get("SENTINEL_PRINCIPAL_PASSWORD", "principal@sentinel")
        r = client.post("/api/login", json={"username": "principal", "password": pw})
        results["login_status"] = r.status_code
        token = r.json().get("token") if r.status_code == 200 else None
        auth = {"Authorization": f"Bearer {token}"} if token else {}

        # --- audit gating (no token should be rejected) ---------------------
        results["audit_requires_auth"] = client.get("/api/audit").status_code

        # --- 1. clip metadata on NL search ----------------------------------
        clip_samples = {}
        for q in ["intrusion", "loitering", "people in zone 2", "falls"]:
            rr = client.post("/api/query", json={"query": q}, headers=auth)
            body = rr.json()
            first = (body.get("results") or [{}])[0]
            clip_samples[q] = {
                "count": body.get("results_count"),
                "clip": first.get("clip"),
            }
        results["clip_metadata"] = clip_samples

        # pick a real clip to test byte-range playback
        clip = None
        for q in ["people", "loitering", "person", "intrusion", "after hours"]:
            rr = client.post("/api/query", json={"query": q}, headers=auth).json()
            for row in rr.get("results", []):
                if row.get("clip", {}).get("video_path"):
                    clip = row["clip"]
                    break
            if clip:
                break

        if clip:
            path = clip["video_path"]
            # byte-range request (what the browser <video> does when seeking)
            rng = client.get(f"/api/clip/video?path={path}&token={token}",
                             headers={"Range": "bytes=0-2047"})
            results["clip_video"] = {
                "path": os.path.basename(path),
                "range_status": rng.status_code,
                "content_range": rng.headers.get("content-range"),
                "content_length": rng.headers.get("content-length"),
                "why_logged": clip.get("why_logged"),
                "clip_window": [clip.get("clip_start_seconds"), clip.get("clip_end_seconds")],
                "clip_duration": clip.get("clip_duration_seconds"),
            }
            # unauthorized (no token) must be blocked
            results["clip_video"]["no_token_status"] = client.get(
                f"/api/clip/video?path={path}").status_code

        # --- 2. location-based multi-camera grid ----------------------------
        cams = client.get("/api/cameras", headers=auth).json()
        results["cameras_total"] = len(cams.get("cameras", []))
        results["floors"] = [f["id"] for f in cams.get("floors", [])]

        grid = {}
        for q in ["first floor", "second floor", "library", ""]:
            g = client.get(f"/api/cameras/by-location?q={q}", headers=auth).json()
            grid[q or "(all)"] = {
                "floor": g.get("floor"),
                "count": g.get("count"),
                "layout": g.get("layout"),
                "cameras": [c["name"] for c in g.get("cameras", [])],
            }
        results["grid"] = grid

        # single-camera snapshot (finite; proves the MJPEG source decodes)
        snap = client.get(f"/api/cameras/1/snapshot?token={token}")
        results["camera_snapshot"] = {
            "status": snap.status_code,
            "content_type": snap.headers.get("content-type"),
            "bytes": len(snap.content) if snap.status_code == 200 else 0,
        }
        if snap.status_code == 200:
            with open(os.path.join(REPORT_DIR, "evidence", "v2_camera1_snapshot.jpg"), "wb") as f:
                f.write(snap.content)
        # confirm the streaming route exists (do NOT call it — infinite stream)
        results["stream_route_registered"] = any(
            getattr(rt, "path", "") == "/api/cameras/{camera_id}/stream" for rt in srv.app.routes)

        # --- 3. periodic summary reports ------------------------------------
        periodic = {}
        for period in ["daily", "monthly", "yearly"]:
            pr = client.get(f"/api/reports/periodic?period={period}", headers=auth).json()
            periodic[period] = {
                "headcount": pr.get("headcount"),
                "peak_simultaneous_people": pr.get("peak_simultaneous_people"),
                "total_occupancy_breaches": pr.get("total_occupancy_breaches"),
                "occupancy_breaches": pr.get("occupancy_breaches"),
                "loitering_sessions": pr.get("loitering_sessions"),
                "alerts": pr.get("alerts"),
                "busiest_hour": pr.get("busiest_hour"),
                "report_text": pr.get("report_text"),
            }
        results["periodic_reports"] = periodic

        # --- 4. audit trail (should now be full of the actions above) -------
        audit = client.get("/api/audit?limit=100", headers=auth).json()
        entries = audit.get("entries", [])
        action_counts = {}
        for e in entries:
            action_counts[e["action"]] = action_counts.get(e["action"], 0) + 1
        results["audit"] = {
            "total_entries": len(entries),
            "action_counts": action_counts,
            "sample": entries[:12],
        }

    return results


def main():
    os.makedirs(os.path.join(REPORT_DIR, "evidence"), exist_ok=True)
    log("=" * 70)
    log("SENTINEL AI — V2 FEATURE TEST DRIVER")
    log("=" * 70)
    repopulate_db()
    report["api_tests"] = run_api_tests()

    out = os.path.join(REPORT_DIR, "results_v2.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    log(f"[v2] wrote {out}")
    log("[v2] DONE")


if __name__ == "__main__":
    main()
