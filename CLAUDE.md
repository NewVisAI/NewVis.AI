# Sentinel AI — Working Notes

School/campus CCTV video-analytics platform. FastAPI backend + single-file HTML dashboard
(`backend/index.html`). Pipeline: YOLOv8n detect → ByteTrack → OSNet (torchreid) cross-camera
Re-ID → zones/behaviours → events/alerts.

Repo: `github.com/haronnk/SENTINEL-2.0` · Path: `D:\COLLEGE\Sentinel` = `/mnt/d/COLLEGE/Sentinel`

**Release tiers (decided 2026-07-29):** [FEATURE_STATUS.md](FEATURE_STATUS.md) is the source of
truth for what we claim works. Deterministic features ship as Production; anything with
unquantified accuracy ships as **Beta** until labelled footage gives it a number.
[TEST_PLAN.md](TEST_PLAN.md) is the path to proving it.

---

## Environment — read this before running anything

**Primary env is now a NATIVE Windows venv `.venv-win`** (Python 3.13, CPU-only torch), run from
PowerShell — migrated off WSL on 2026-08-08. The old WSL `.venv` is kept as a fallback (see below).

```powershell
cd D:\COLLEGE\Sentinel
.\.venv-win\Scripts\python.exe <script>
```

The install manifest is [requirements-windows.txt](requirements-windows.txt) = `requirements.txt`
minus `tflite-runtime` (no Windows wheel; unused Edge-TPU path). **`torchreid` is intentionally not
installed** on Windows — it needs a C compiler for its Cython bits, which this box lacks. Re-ID runs
via `onnxruntime` on `models/osnet_x1_0.onnx` instead (set by `reid_onnx` in `deployment.json`);
`reid.py` tries the ONNX backend first, so it never touches torchreid. ONNX OSNet has ~0.999999
cosine parity with the torchreid OSNet — identity behaviour is unchanged. Recreate the env with
`python -m venv .venv-win; .\.venv-win\Scripts\python -m pip install -r requirements-windows.txt`.

### Start the server (native)

```powershell
cd D:\COLLEGE\Sentinel
$env:DISABLE_AI_ENGINE = "1"
.\.venv-win\Scripts\python.exe -m uvicorn backend.server:app --host 0.0.0.0 --port 8000
```

→ http://localhost:8000 · login `developer` / `dev@sentinel` · Video Analytics tab → pick camera →
**Run live analytics**. No `SENTINEL_DB_PATH` needed — natively the DB defaults to
`D:\COLLEGE\Sentinel\cctv_logs.db` on NTFS, which has none of the WSL drvfs slowness.

### Still load-bearing

| Thing | Why |
|---|---|
| `DISABLE_AI_ENGINE=1` | The multiprocessing pool engine is unsafe (fork on Linux / spawn on Windows) and crash-loops its workers. Use per-camera `live_analytics.py` (thread-based, cross-platform) instead. |
| Uvicorn runs **without `--reload`** | Code changes need a full restart. Kill natively with `Get-Process python \| Stop-Process` or `taskkill /F /IM python.exe`. |

### Data & DB note

The live SQLite DB is `D:\COLLEGE\Sentinel\cctv_logs.db` (migrated from the WSL
`/root/sentinel_data/cctv_logs.db` on 2026-08-08; the pre-migration D: copy is
`cctv_logs.db.pre-native-migration.bak`). On WSL the DB **must not** sit on `/mnt/d` (drvfs = ~3 s
per connection) — that's why the WSL path used `/root`; irrelevant natively.

### WSL fallback (still works)

```bash
wsl bash -lc 'cd /mnt/d/COLLEGE/Sentinel && DISABLE_AI_ENGINE=1 \
  SENTINEL_DB_PATH=/root/sentinel_data/cctv_logs.db \
  ./.venv/bin/python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000'
```

The Bash tool here is Git Bash (its `/root`, `/tmp` differ from WSL's, and it mangles `/mnt/d`) —
hence the `wsl bash -lc '...'` wrapper for the WSL venv. Kill with `pkill -9 -f 'uvicorn backend.server'`.

### Other traps

- **graphify** now runs natively from PowerShell (`graphify update .`, `graphify query "..."`) — it
  is tied to the native Python, which is why it was "command not found" inside WSL.
- No GPU on this box (8 cores, CPU-only). Anything needing CUDA — TensorRT export, NVDEC decode,
  real throughput numbers — is deferred to the friend's RTX laptop. This machine proves
  *accuracy parity*; the GPU box proves *speed*.
- `license.key`, `dev_keys/`, `.venv`, `.venv-win`, `models/`, `reid_crops/`, `frames/` are gitignored.

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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
