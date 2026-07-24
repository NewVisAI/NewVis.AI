# Review — AI Summary Feature + RTX 5060 GPU Benchmark (2026-07-17)

Reviewer: session pickup from `SESSION_HANDOFF_2026-07-16.md`. Covers the new commit your
friend pushed (`f9e606a`) and the GPU benchmark numbers he sent.

---

## 1. New commit: `f9e606a` — "Implement real-time AI summary manager and live UI feed overlay"

Author `Haronnk <haronk0604@gmail.com>`, Thu 2026-07-16. One commit ahead of local `main`
(`571b872`); **clean fast-forward** available (nothing to merge/resolve).

### What it does
Adds a per-camera, natural-language "AI Security Summary" that appears as an overlay on the
live analytics feed.

| File | Change |
|---|---|
| `summary_manager.py` (new, 91 lines) | `SummaryManager` — pulls recent alerts, turns them into a plain-English security summary |
| `backend/server.py` (+7) | New endpoint `GET /api/cameras/{id}/analytics/summary` (auth-gated) |
| `backend/index.html` (+59) | Overlay card on the video shell; polled while analytics runs |
| `SENTINEL_2.0_Fixed_Issues_Report.{docx,pdf}` | Doc artifacts (not code) |

### How it works
- **LLM path:** if `GROQ_API_KEY` is set, calls `LLMParser.summarize_incidents()`
  (Groq `llama-3.1-8b-instant`) on the last 10 incidents.
- **Fallback:** no key / LLM error → `_generate_rule_based_summary()` counts event types and
  flags HIGH RISK if any `Intrusion / Violence / Fall` or "restricted" alert is present.
- **UI:** `refreshAnalyticsStatus()` (every 3 s) fetches the summary and shows the overlay
  while the worker status is `running`; hidden when off/waiting/stopped.

### Correctness — verdict: **wired up correctly, will work**
Every dependency exists and the data shapes match:
- `alerts.get_recent_alerts(limit=50)` ✓ returns dicts with `camera_id`, `timestamp`,
  `alert_type`, `global_id`, `track_id`, `message`.
- `summary_manager` builds `{timestamp, global_id, type, description}` — exactly the keys
  `llm_parser.summarize_incidents()` reads (`inc['timestamp']`, `['global_id']`, `['type']`,
  `['description']`). ✓ No mismatch.
- Singleton pattern + lazy `import summary_manager` inside the endpoint — fine.

### ⚠️ Finding (efficiency, not a crash) — recommend fixing before it's a cost problem
**The summary is recomputed from scratch every 3 s per open camera view, with no caching.**
When `GROQ_API_KEY` is set, that's **one Groq API call every 3 seconds** for every viewer,
even when no new alert has arrived since the last poll. Consequences:
- Groq cost + rate-limit exposure scales with viewers × time, not with actual events.
- Each poll also re-queries the alerts table.

**Suggested fix (small):** cache the summary in `SummaryManager` keyed by `camera_id`, with
(a) a TTL of ~30–60 s and/or (b) invalidate only when the newest alert `id`/timestamp for
that camera changes. Return the cached string otherwise. Rule-based path is cheap, so this
mainly protects the Groq path.

**Minor:** docstring says "last 30 alerts" but the code fetches 50 then slices 10 — cosmetic.

---

## 2. RTX 5060 GPU benchmark (numbers your friend sent)

Environment: RTX 5060 Laptop GPU (Blackwell, compute 12.0 / sm_120), Python 3.13, PyTorch +
Torchvision **CUDA 13.0 nightly** (the nightly is what resolved the `sm_120` "no kernel image"
errors — Blackwell isn't in stable CUDA wheels yet).

| Mode | Throughput | Capacity @ 3 fps | Notes |
|---|---|---|---|
| `REID_DEFERRED=0` (OSNet live) | **429.4 FPS** | ~143 cams/machine | live cross-camera matching |
| `REID_DEFERRED=1` (deferred)   | **525.2 FPS** | ~175 cams/machine | **+22.3%** |

### Sanity check — the numbers are internally consistent
- 429.4 / 3 = 143.1 cams ✓ · 525.2 / 3 = 175.1 cams ✓ (capacity math is just FPS ÷ 3).
- Speedup 525.2/429.4 = **+22.3%** ✓.

### Is it "working better"? — **Yes, and it confirms the earlier prediction**
The 2026-07-16 handoff measured deferred Re-ID at only **~14% system-level speedup on CPU**,
and predicted *"on a GPU the OSNet share is larger."* The GPU result (**+22.3%**) is exactly
that: deferring OSNet helps more on GPU than on CPU, because on CPU the total was dominated by
non-GPU work (drawing/tracking) while on GPU the OSNet share is a bigger slice of the budget.

So the optimisation behaves as designed and the benchmark validates it on real Blackwell HW.

### Caveats to keep in mind (don't over-read the headline)
- These are **`gpu_benchmark.py` synthetic throughput** numbers (model inference rate), not a
  143-camera end-to-end soak test. Real capacity will be lower once RTSP decode, DB writes,
  drawing, and network are in the loop — treat 143/175 as an **upper bound**.
- The **+22.3% comes with the known trade-off**: `REID_DEFERRED=1` makes live cross-camera
  Global-IDs unreliable (matching moves to search-time). Per-camera events (fall/loiter/
  intrusion/running) stay live. That's the product decision still to be confirmed with the
  technical lead (per handoff).
- CUDA 13.0 **nightly** is not a stable base — pin the exact nightly build for reproducibility.

---

## 3. State & recommended next steps
- Local `main` is 1 commit behind `origin/main`; **fast-forward is clean** — safe to pull.
- The running server (up ~14 h) is still on the **old** code (`571b872`); the summary feature
  is **not live** until we merge and restart uvicorn (no `--reload`).
- Recommended order:
  1. Fast-forward `main` to `f9e606a`.
  2. Add summary caching (the efficiency finding above) before enabling the Groq path widely.
  3. Restart the server and smoke-test the overlay (rule-based path works with no API key).
