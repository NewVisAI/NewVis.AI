# Sentinel AI — Weekly Progress Report

**Period:** 11–18 July 2026
**Product:** Sentinel AI — school/campus CCTV video-analytics platform
**Prepared for:** Project Manager

---

## Executive summary

This week we moved Sentinel from "works on a demo clip" toward "runs a real 300-camera
school." We **fixed the crashes** that made the live system unusable, **redesigned live
analytics** so the camera feed is smooth while AI runs in the background, **built and
measured GPU-efficiency optimizations** aimed at cutting the hardware cost of a 300-camera
deployment, and **validated the optimizations on real GPU hardware**. We also produced the
deployment, cost, and technical documentation needed for a technical-lead / bid review, and
began **field-testing with real Adiva cameras**.

---

## Headline achievements

1. **Stabilised the live system** — eliminated the crashes that took the whole dashboard
   down ("failed to fetch on all tabs"). The platform now runs continuously.
2. **Smooth live feed + background AI** — the camera view now streams in real time (~20 fps)
   while the heavy analytics run separately, logging events and pushing alerts to
   Notifications. New one-click "Run live analytics" control in the dashboard.
3. **GPU-cost optimizations built and measured** — two techniques to reduce the GPU needed
   per camera, so a 300-camera school costs less to run.
4. **Validated on real GPU hardware** — benchmarked on an RTX 5060 laptop: the optimizations
   deliver a measured **+20.7% capacity** and the system is confirmed inference-bound.
5. **Security hardening + documentation** — a full security/bug audit was completed, and we
   produced technical, deployment, and cost documents for review.

---

## What we delivered, by workstream

### 1. Reliability / stability (system now usable live)
Fixed the root causes of the recurring full-dashboard crashes:
- A worker-process crash in the live engine (illegal camera-read patch).
- A code bug that broke live processing on every frame.
- The web server decoding camera video directly and crashing on real streams.
- Slow startup that froze the server while loading AI models.
- A **database performance trap**: on the dev environment the database was ~1000× slower
  than it should be; relocating it made every dashboard action go from **3–7 seconds to
  under 0.8 seconds**.

**Outcome:** the platform now starts cleanly and stays responsive under load.

### 2. Live analytics — smooth feed, background processing
Redesigned so the **live camera feed is always smooth and real-time** (nothing heavy runs
on it), while the **AI pipeline processes in the background** (detection, tracking,
fall/loitering/intrusion/running detection) and **sends alerts to the Notifications column**.
Added a **"Run live analytics"** button in the Video Analytics tab with a live status
indicator. A teammate also added a **real-time AI security summary** overlay on the feed.

**Outcome:** operators get a fluid live view *and* automatic event logging at the same time.

### 3. GPU-cost optimizations (for 300-camera affordability)
The main cost driver at scale is GPU compute. We built two optimizations:
- **Adaptive-rate gate** — the AI runs fast when a person is present and slows to a trickle
  when a camera is empty (safely — a fallen/still person is still detected). Measured
  **~36% fewer AI runs on quiet cameras** (schools have many: hallways during class, night
  perimeter).
- **Deferred cross-camera identification** — the heaviest continuous GPU task
  (cross-camera "who is this person") is moved off the always-on path and run **only when
  someone searches**. Per-operation this is ~200× cheaper.

**Outcome:** fewer GPUs needed per school → lower deployment cost.

### 4. GPU hardware validation (real numbers)
Benchmarked on an **NVIDIA RTX 5060 laptop**:
- Confirmed the system is **inference-bound** (GPU compute is the bottleneck, not video
  decoding) — so our optimizations target the right thing.
- **Deferred identification gave +20.7% throughput** (244.7 → 295.3 frames/sec).
- **720p sub-stream analytics** retained **96.97% detection accuracy** (passes the 95%
  safety gate) while halving the data footprint.

**Outcome:** the optimization direction is validated on real GPU hardware, not just theory.

### 5. Security, licensing & documentation
- Completed a **security/bug hardening audit** (login rate-limiting, access-control fixes,
  database integrity, etc.) — captured in a formal "Fixed Issues Report."
- Verified the **licensing controls work**: the developer-set camera limit and per-feature
  restrictions are correctly enforced on the customer, and take effect immediately.
- Produced review-ready documents: **Technical Overview, Deployment Guide, Hosting/Cost
  Proposal, Feature Overview,** and a **GPU setup guide** for the team's GPU laptop.

### 6. Field testing (real cameras)
Connected real **Adiva IP cameras**. One camera works end-to-end (live feed + analytics
verified). A second camera would not join the network — after systematic diagnosis we ruled
out IP conflict, cabling, and router-port issues; it points to a **camera-side power/hardware
fault** (still being resolved on the hardware side).

---

## Measured results (this week)

| Metric | Result |
|---|---|
| Dashboard response time (after DB fix) | 3–7 s → **< 0.8 s** |
| Live feed frame rate | **~20 fps** (smooth), AI runs separately at ~3 fps |
| Adaptive gate GPU saving (quiet cameras) | **~36% fewer AI runs** |
| Deferred identification, per operation | **~200× cheaper** |
| GPU throughput gain (RTX 5060, measured) | **+20.7%** (≈ 82 → 98 cameras/machine, single-stream) |
| 720p sub-stream accuracy | **96.97%** person recall (passes 95% gate) |

---

## Deployment & cost picture (for a 300-camera school)
- **Recommended architecture:** edge AI servers on-site + a lightweight central cloud
  (keeps video on-premise, avoids huge bandwidth cost).
- **Compute:** roughly **10 GPUs** for 300 cameras (fewer with the optimizations above and a
  "smart mix" of live + on-demand cameras).
- **Indicative cost (existing cameras reused):** ~₹20–35 lakh one-time + ~₹4–7 lakh/year for
  a sensible configuration. Full detail in the Deployment Guide.

---

## Risks / open items
- **Camera hardware:** the second Adiva unit isn't connecting — likely a power/hardware
  fault on the camera; needs hardware-side resolution (not software).
- **GPU numbers are early:** the +20.7% was measured with a stand-in identification model
  (the exact model's library wasn't installed on the test laptop) and without TensorRT/FP16
  optimization — so the final numbers may shift, and the "cameras per machine" figure needs a
  real multi-camera test to confirm.
- **Licensing for a real sale:** the protection works, but needs production key management +
  online activation before commercial deployment.
- **Live engine at scale:** the older multi-camera engine still needs a fix to be
  production-safe; we are currently using the newer per-camera approach that works today.

---

## Next steps
1. Resolve the second camera (hardware) and re-test a two-camera cross-camera scenario.
2. Complete GPU validation with the real identification model + TensorRT/FP16, and a true
   multi-camera load test, to lock down the "cameras per GPU" number.
3. Finalise the live-engine approach for scale.
4. Prepare the pilot: one site, edge server + cloud, on the school's existing cameras.
5. Licensing hardening (production keys + online activation) ahead of any commercial deal.

---

*Supporting documents in the repository: `SENTINEL_TECHNICAL_OVERVIEW.md`,
`SENTINEL_DEPLOYMENT_GUIDE.md`, `Sentinel_Hosting_Proposal.docx`,
`GPU_LAPTOP_VALIDATION_REPORT.md`, `SENTINEL_2.0_Fixed_Issues_Report.pdf`.*
