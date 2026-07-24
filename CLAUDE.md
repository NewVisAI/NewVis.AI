# Sentinel AI — Working Notes

School/campus CCTV video-analytics platform. FastAPI backend + single-file HTML dashboard
(`backend/index.html`). Pipeline: YOLOv8n detect → ByteTrack → OSNet (torchreid) cross-camera
Re-ID → zones/behaviours → events/alerts.

Repo: `github.com/haronnk/SENTINEL-2.0` · Path: `D:\COLLEGE\Sentinel` = `/mnt/d/COLLEGE/Sentinel`

---

## Environment — read this before running anything

The `.venv` is a **WSL Ubuntu Python 3.13** venv (CPU-only torch). It is **not** a Windows venv —
`python` from PowerShell will not work. Always go through `wsl`:

```bash
wsl bash -lc 'cd /mnt/d/COLLEGE/Sentinel && ./.venv/bin/python <script>'
```

The Bash tool here is Git Bash, which mangles `/mnt/d` paths — that's why the `wsl bash -lc '...'`
wrapper is mandatory rather than stylistic.

### Start the server

```bash
wsl bash -lc 'cd /mnt/d/COLLEGE/Sentinel && DISABLE_AI_ENGINE=1 \
  SENTINEL_DB_PATH=/root/sentinel_data/cctv_logs.db \
  ./.venv/bin/python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000'
```

→ http://localhost:8000 · login `developer` / `dev@sentinel` · Video Analytics tab → pick camera →
**Run live analytics**.

Preview configs for this exist in [.claude/launch.json](.claude/launch.json) (`sentinel-backend`
on 8000, `sentinel-backend-preview` on 8010) — but they omit the two env vars above, so prefer the
command form until that's fixed.

### Both env vars are load-bearing

| Var | Why it is not optional |
|---|---|
| `SENTINEL_DB_PATH=/root/sentinel_data/...` | SQLite on `/mnt/d` (WSL drvfs) costs **~3 s per DB connection** → every `/api` call took 3–7 s. Moving the DB to native ext4 made it ~1000× faster. Never point the DB at `/mnt/d`. |
| `DISABLE_AI_ENGINE=1` | The pool engine is **fork-unsafe** and crash-loops its workers. Use per-camera `live_analytics.py` instead. Fixing this upstream is still open. |

### Other traps

- Uvicorn runs **without `--reload`** — code changes need a full restart.
  Kill with `pkill -9 -f 'uvicorn backend.server'`.
- Backgrounding: a foreground uvicorn started via the Bash tool's `run_in_background` survives.
  `wsl "... &"` does **not**.
- No GPU on this box (8 cores, CPU-only). Anything needing CUDA — TensorRT export, NVDEC decode,
  real throughput numbers — is deferred to the friend's RTX laptop. This machine proves
  *accuracy parity*; the GPU box proves *speed*.
- `license.key`, `dev_keys/`, `.venv`, `models/`, `reid_crops/`, `frames/` are gitignored.

---

## Current state — `feat/cost-optimizations`

Four commits ahead of `main`, all **flag-gated and off by default** so existing deployments are
unchanged. See [REPORT_2026-07-19_cost-optimization-levers.md](REPORT_2026-07-19_cost-optimization-levers.md)
for the full analysis and cost model (~24% 3-yr TCO reduction for the 300-camera school).

| Lever | Status | Flag |
|---|---|---|
| ① Sub-stream analytics (720p analyze, 1080p record) | done | `USE_SUBSTREAM=1` |
| ② Hardware decode offload (NVDEC / QuickSync / VAAPI) | done | `DECODE_BACKEND=nvdec\|qsv\|vaapi` |
| ③ Camera-side motion gating (ONVIF) | done | `MOTION_GATING=1`, `MOTION_WINDOW_S`, `IDLE_DECODE_FPS` |
| ⑤ Cross-camera inference batching | **not started** | — |
| Quantization (FP16) | tooling done, GPU numbers pending | `quantize.py`, `validate_quantization.py` |

**Three things block turning estimates into facts** — all need on-site or GPU access:
1. Run `gpu_benchmark.py` on real streams → answers **decode-bound vs inference-bound**, which
   decides whether ⑤ is even worth building.
2. Confirm the Adiva sub-stream URL (`…/ch1/sub/av_stream`) resolves.
3. Confirm Adiva actually emits ONVIF motion events.

Lever ② has a silent-failure mode worth remembering: the stock PyPI `opencv-python` wheel has no
hwaccel, so `DECODE_BACKEND=nvdec` falls back to software decode without erroring. Verify via the
`CAP_PROP_HW_ACCELERATION` line the reader logs.

### Uncommitted right now

`summary_manager.py` — adds a `SUMMARY_WINDOW_MINUTES` (default 15) recency filter, because the
AI summary was replaying the last 50 alerts *ever* for a camera and presenting stale ones as
current activity. `cameras.json` / `zones.json` are local live-data edits, not features.

---

## Configuration

[inference_config.py](inference_config.py) is the single source of truth for backend/model/tier
settings. Precedence: **env var > `deployment.json` > default**. Add new knobs there rather than
scattering `os.getenv` through the pipeline.

Notable existing flags: `REID_DEFERRED=1` (skip live OSNet, do cross-camera matching on-demand at
search time — 39 ms → 0.2 ms per Re-ID, but live Global-IDs become unreliable; this is a
**product decision**, not just an optimization), `SENTINEL_TIER`, `DETECTOR_WEIGHTS`,
`REID_SIMILARITY_THRESHOLD`.

---

## Tests

`unittest`, not pytest. No runner config — invoke directly:

```bash
wsl bash -lc 'cd /mnt/d/COLLEGE/Sentinel && ./.venv/bin/python -m unittest test_fall test_licensing test_reid'
```

`feature_test_driver.py` / `_v2.py` are end-to-end demo drivers, not unit tests.

---

## Conventions

- Every accuracy-affecting optimization ships **off by default** behind a flag, with a validation
  gate documented alongside it (see [QUANTIZATION.md](QUANTIZATION.md) for the pattern: prove
  parity on this box, defer throughput to the GPU box).
- Safety events (fall / intrusion / loiter / running) stay live and per-camera. Anything that
  could cause a **missed fall** needs an explicit safeguard — e.g. motion gating keeps a slow
  heartbeat decode so a person who falls and goes still is never skipped.
- Per-file design docs live in [docs/](docs/). [.agents/AGENTS.md](.agents/AGENTS.md) is **stale**
  (describes the old single-camera `python app.py` app) — ignore it.
