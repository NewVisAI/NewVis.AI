# Sentinel AI — Deployment Guide

**Purpose:** everything needed to decide **how to deploy Sentinel** — architecture
options, hardware, networking, storage, sizing, costs, and the gap between today's
prototype and a production rollout. Written to take into a technical-lead review.

**Date:** 2026-07-15 · **FX for costs:** US$1 = ₹85 · figures are planning-grade (±30–40%).

---

## 0. TL;DR for the reviewer

- The **deciding constraint is network bandwidth**, not compute. 10k HD cameras ≈ **30 Gbps**
  of video — you cannot centralise that. → **Deploy inference at the edge (each site);
  send only events/clips/metadata to a central cloud.**
- **Compute:** full pipeline ≈ **15–30 cameras per GPU**. 300 cameras ≈ **~10 GPUs**;
  10,000 ≈ **~500 GPUs**.
- **Buy edge hardware, don't rent cloud GPUs** at scale (renting 500 GPUs ≈ ₹1.3–3.5 cr/month).
- **Today it runs CPU-only, single-node, SQLite.** Production needs: GPUs, PostgreSQL,
  hardened licensing, and the processing engine finalised (see §9). That's the real work.
- Two open decisions for the lead: **(a)** finalise the processing engine (fix the pool
  engine vs. standardise on the per-camera thread model), **(b)** buy-vs-rent + edge-vs-cloud.

---

## 1. What one camera costs (the unit economics of deployment)

Everything scales from these per-camera numbers:

| Resource | Per 1080p camera | Notes |
|---|---|---|
| **Bandwidth (video)** | ~2–4 Mbps H.264 | why video can't be centralised |
| **Decode** | ~0.05–0.1 CPU-core | H.264 decode before inference |
| **Inference (full stack)** | ~1/15 to 1/30 of a GPU | YOLO + OSNet Re-ID + heuristics @ 5–10 fps |
| **Event/metadata out** | a few KB/s | tiny — this is what goes to the cloud |
| **Storage (raw, 30 days)** | ~0.6–1 TB | on-site NVR, not central |
| **Storage (events+clips)** | ~MBs/day | central, cheap |

**Implication:** move the heavy part (decode+inference) to the edge; move only the light
part (events/clips) to the cloud.

---

## 2. Deployment architecture options

### Option A — Edge inference + Cloud control plane ⭐ recommended
Inference runs on GPU/NPU boxes **at each site**; a lean central cloud hosts the
dashboard, licensing, metadata DB, clip storage, cross-site search.

```
[Cameras]─RTSP(LAN)─►[Edge server @ site: decode + AI]──events/clips(WAN)──►[Cloud: UI, DB, licensing]
   (raw video stays on-site NVR)                         (a trickle of data)
```
**+** kills the 30 Gbps problem · scales site-by-site · lowest long-run cost · resilient to
WAN outage · video stays on-premise (privacy).
**–** upfront hardware capex · field install/maintenance at many sites.

### Option B — Fully centralised cloud GPUs
Stream all video to the cloud; ~500 rented GPUs process centrally.
**+** no capex, elastic, fastest to stand up.
**–** ~30 Gbps ingest (often infeasible) · **highest recurring cost** · central point of
failure · all video leaves premises.

### Option C — Central on-prem data centre
You own ~500 GPUs in your own/colocated DC; video aggregated there.
**+** full control · cheaper than cloud rental over 5 yr.
**–** still must transport ~30 Gbps to the DC · large capex + power/cooling · you run DC ops.

### Option D — Per-camera SaaS (no edge)
Fully hosted, per-camera fee.
**+** simplest commercial model, great for small sites / AI-capable cameras.
**–** inherits Option B's bandwidth/cost problems at scale.

**Decision matrix**

| | Bandwidth-safe | Capex | Recurring | Scales to 10k | Privacy |
|---|---|---|---|---|---|
| A Edge+Cloud ⭐ | ✅ | High | Low | ✅ | ✅ on-prem video |
| B Cloud GPUs | ❌ | Low | Very high | ⚠️ | ❌ |
| C On-prem DC | ⚠️ | Very high | Medium | ⚠️ | ✅ |
| D Per-cam SaaS | ❌ at scale | Low | High | ❌ | ❌ |

---

## 3. Edge inference hardware options (per site)

| Tier | Hardware | Throughput (full stack) | Fit |
|---|---|---|---|
| **GPU** | NVIDIA T4 / L4 / A10 + TensorRT/DeepStream | ~15–30 cams/GPU @ 5–10 fps | standard live |
| **NPU** | Intel iGPU/NPU via OpenVINO (ONNX export) | mid | cost-sensitive |
| **Edge-TPU** | Google Coral (~₹6k) via `_edgetpu.tflite` | detection offload; ReID needs INT8 TFLite | budget/forensic |
| **CPU** | existing office PCs | ~1 camera live (or many forensic/on-demand) | budget only |

The code supports all four via a pluggable inference layer (`inference_config.py` +
`export_models.py`); the OSNet ONNX export was verified to match the Torch model at
**0.9997 cosine**, so edge Re-ID stays consistent with GPU Re-ID.

**Reference edge node:** 1 server + 2 GPUs handles ~40–50 cams (light) / ~25–30 (full
Re-ID). ~₹4–6 lakh/node.

---

## 4. Sizing (how many GPUs)

Full stack = YOLO detect + OSNet Re-ID + behaviour heuristics, at surveillance fps.

| Config | Cams/GPU | GPUs for 300 | GPUs for 10,000 |
|---|---|---|---|
| Full, Re-ID on, ~10 fps | ~15 | ~20 | ~660 |
| Full, Re-ID on, ~5 fps | ~25–30 | ~10 | ~350 |
| Detection+track only (Re-ID off), 2–3 fps | ~50 | ~6 | ~200 |

**Levers to cut GPU count:** lower fps (biggest), turn Re-ID off where cross-camera ID
isn't needed, downscale inference resolution (640px), batch cameras on the GPU.

---

## 5. Processing engine — what actually runs the pipeline (important)

There are **two** live-processing designs; the reviewer should pick one to standardise on.

- **Pool engine (`backend_runner.py`)** — multiprocessing worker pools, N cameras per
  process. Designed for scale, but **currently fork-unsafe** (forks workers from the
  heavy web process → hangs/CPU-saturates) and had a `cap.read` crash (locally fixed).
  Needs `spawn` start method + `Manager().Lock()` to be production-safe.
- **Per-camera live analytics (`live_analytics.py`)** — thread-based, **works today**.
  Per camera: a reader thread streams the **raw feed** at full rate (smooth) + an
  analytics thread runs the pipeline at ~3 fps (events/alerts), fully decoupled.
  Measured: ~21 fps display + ~3 fps analytics per camera ≈ ~1.5 CPU cores.

**Deployment recommendation:** standardise on the per-camera thread model (proven) and
scale it horizontally across edge nodes, OR invest in making the pool engine spawn-safe
for higher per-node density. **This is the #1 engineering decision to confirm with the lead.**

---

## 6. Data, storage & database

- **Raw video:** stays on **on-site NVRs** (never centralised) — 30-day retention ≈
  0.6–1 TB/camera. This is existing CCTV infra, not new cost.
- **Events / tracking / clips / snapshots:** small; central.
- **Database:** SQLite (single-file, dev/single-site) **or PostgreSQL** (`DATABASE_URL`),
  same code. **Production = PostgreSQL** (central), one per region/site as needed.
  - ⚠️ **Deployment gotcha found this session:** SQLite on a slow filesystem (WSL drvfs /
    network mount) makes each DB connection ~3 s → every API call 3–7 s. Keep SQLite on
    **local fast storage** or use Postgres. (`SENTINEL_DB_PATH` overrides the path.)
- **Retention automation:** built-in loop prunes events/tracking + raw MP4s > 30 days,
  plus a disk-watchdog emergency purge under 10% free.

---

## 7. Networking & site requirements

- **Per site:** cameras on the LAN (RTSP), an edge server, a WAN uplink for events/UI.
- **Bandwidth:** LAN carries full video to the edge box; **WAN only carries events/clips**
  (KB/s), so even a modest site internet link suffices.
- **Cameras:** ONVIF/RTSP IP cameras (the platform decodes `rtsp://…`). Fixed IPs or DHCP
  reservations recommended (avoid same-default-IP clashes on identical cameras).
- **Ports:** dashboard 8000 (put behind TLS/reverse proxy in prod); MJPEG served over HTTP.
- **HA:** run 2 edge boxes per large site (active/standby); Postgres with replication;
  the pipeline auto-reconnects to cameras with backoff.

---

## 8. Cost of deployment (INR, planning-grade)

### 8.1 A 300-camera site (representative)
| Item | Cost |
|---|---|
| AI compute (~10 GPUs, edge) — capex | ₹25–40 lakh |
| Software licence (per site) | ₹7.5–19.5 lakh (₹2.5k–6.5k/camera) |
| Integration / install | ₹3–5 lakh |
| **One-time (AI; existing cameras reused)** | **~₹27–52 lakh** |
| **+ new 300-camera CCTV** (if not existing) | +₹22–25 lakh |
| Recurring: AMC + power + cloud + storage | **~₹5–9 lakh/yr** |

### 8.2 10,000 cameras (Edge + Cloud, recommended)
| Item | Cost |
|---|---|
| Edge GPU fleet (~500 GPUs across ~100 sites) — capex | ₹21–43 crore |
| Central cloud control plane | ₹2–15 lakh/month |
| Steady-state maintenance (power/ops/storage/AMC) | ~₹40–90 lakh/month |
| **Indicative 5-year TCO** | **~₹55–95 crore** |

### 8.3 Option comparison at 10k (5-year TCO)
| Option | Upfront | Monthly | 5-yr TCO |
|---|---|---|---|
| **A Edge + Cloud ⭐** | ₹21–43 cr | ₹40–90 lakh | **₹55–95 cr** |
| B Cloud GPUs (rent) | minimal | ₹1.3–3.5 cr | ₹110–200 cr |
| C On-prem DC | ₹42–50 cr | ₹25–70 lakh | ₹60–95 cr |
| D Per-camera SaaS | minimal | ≈ Option B | not advised |

### 8.4 One-time productisation (any option)
Turning the prototype into a fleet product — optimised multi-GPU pipeline
(TensorRT/DeepStream), DB-backed camera registry, hardened online licensing:
**6–10 engineers × 9–12 months ≈ ₹1.5–3 crore**; pilot site ~₹15–30 lakh.

---

## 9. Current state → production gap (be honest with the lead)

What works today vs. what must be built before a real deployment:

| Area | Today | Needed for production |
|---|---|---|
| Compute | CPU-only, single node | GPU/NPU edge nodes |
| Processing engine | per-camera thread model works; **pool engine fork-unsafe** | finalise one engine (see §5) |
| Database | SQLite | **PostgreSQL** (central), fast local storage |
| Licensing | Ed25519, but **honor-based + dev signing key** | production keypair (offline/HSM) + **online activation + heartbeat** |
| Config | flat JSON (`cameras.json`) | DB-backed camera registry |
| Behaviour detectors | heuristics | optionally trained models (accuracy) |
| Serving | HTTP:8000 | TLS + reverse proxy + auth hardening |
| Known bugs | 2 upstream bugs fixed **locally, uncommitted** (`FrameInjector`, `time`) | land them upstream |

---

## 10. Recommended deployment plan (phased)

1. **Pilot (1 site, ~100–300 cameras):** one edge GPU box + cloud control plane; prove
   per-camera live_analytics at scale; move DB to Postgres. (~₹15–30 lakh + a few GPUs.)
2. **Harden:** finalise the processing engine, TLS, online licensing, Postgres HA.
3. **Roll out site-by-site (Edge + Cloud):** buy edge hardware; each site is independent.
4. **Scale the cloud control plane** only as sites/metadata grow (cheap part).

---

## 11. Questions to ask the technical lead

1. **Edge vs. cloud vs. on-prem DC** — do we commit to Edge + Cloud (Option A)?
2. **Buy vs. rent GPUs** — capex edge hardware vs. cloud rental?
3. **Processing engine** — fix the pool engine (spawn-safe) for density, or standardise
   on the per-camera thread model and scale horizontally?
4. **Database** — PostgreSQL central, or Postgres-per-site? Retention policy?
5. **Licensing** — accept honor-based for pilots, but commit to online activation +
   production keypair before commercial sale?
6. **Camera fps / Re-ID** — what frame rate and is cross-camera Re-ID required everywhere?
   (These set the GPU count and therefore the cost.)
7. **Who owns edge ops** — us, the CCTV partner (Adiva), or the customer's IT?
8. **HA / uptime SLA** — target, and redundancy budget?

---

*Companion docs:* `SENTINEL_TECHNICAL_OVERVIEW.md` (full architecture),
`Sentinel_Hosting_Proposal.docx` (costed proposal), `EDGE_DEPLOYMENT.md` (edge/NPU),
`UPDATE_2026-07-15_stability-and-live-analytics.md` (recent fixes).
