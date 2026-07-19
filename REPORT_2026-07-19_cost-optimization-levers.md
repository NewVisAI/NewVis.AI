# Cost-Optimization Report — 4 Accuracy-Neutral Levers (2026-07-19)

**Scope:** apply four levers to the 300-camera school deployment and quantify resource savings,
deployment-cost reduction, feasibility, and accuracy impact:
① Sub-stream analytics · ② iGPU/hardware decode offload · ③ Camera-side motion triggering (ONVIF)
· ⑤ Cross-camera inference batching.

> **Status of numbers:** engineering estimates grounded in the current code + typical hardware
> behaviour. The one measurement that converts estimates → facts is the benchmark (decode-bound
> vs inference-bound). Two items need a 5-minute on-site check (marked ⚠️).

---

## What was verified in the code
| Finding | Evidence | Implication |
|---|---|---|
| **Sub-stream exists on Adiva cams** | Cam 1 = `rtsp://…/h264/ch1/**main**/av_stream` (Hikvision/Adiva convention) | ⚠️ Confirm `…/ch1/**sub**/av_stream` on-site (near-certain) |
| **No hardware decode today** | `camera_stream.py` sets `rtsp_transport;tcp` only, **no `hwaccel`** → CPU software decode | Enabling NVDEC/QuickSync is an untapped win (lever ②) |
| **No batching today** | `app.py:447` `detector.detect(frame)` — one camera at a time | Lever ⑤ is an architectural refactor |
| **No ONVIF today** | no `onvif` references in code | ⚠️ Confirm Adiva ONVIF motion events on-site (very likely) |

---

## Lever-by-lever

### ① Sub-stream analytics — analyze 720p sub-stream, record 1080p main
- **Mechanism:** decode cost scales with pixels. 1080p (2.07 MP) → 720p (0.92 MP) ≈ **2.25× fewer
  pixels to decode.** YOLO cost is ~unchanged (it resizes to a fixed input either way), so the
  saving is almost entirely **decode**.
- **Resource saved:** ~**55% less decode load**; ~**⅔ less network bandwidth** on the analytics path.
- **Feasibility:** **LOW effort / LOW risk.** Change the analytics source URL to `/sub/`; keep `/main/`
  for the evidence recording (on Adiva's NVR). *Code:* camera registry + `camera_stream.py`.
- **Accuracy:** **none** for person-scale detection at 720p (validate recall once to confirm).

### ② iGPU / hardware decode offload
- **Mechanism:** today decode runs on the CPU in software. Move it to the GPU's **NVDEC** engine
  (free — the GPU is already there), and optionally add the Intel iGPU **QuickSync** as a *second*
  parallel decoder. Two decode engines running at once.
- **Resource saved:** frees CPU cores entirely from decode; **~1.5–2× decode capacity/box** when
  NVDEC + QuickSync run together. Removes decode as the bottleneck.
- **Feasibility:** **MEDIUM.** NVDEC needs a CUDA-enabled OpenCV/FFmpeg + `hwaccel` options.
  QuickSync requires an **Intel CPU with iGPU** (our BOM used Ryzen → small BOM change, ~cost-neutral).
- **Accuracy:** **none** (decoding is lossless).

### ③ Camera-side motion triggering (ONVIF)
- **Mechanism:** the camera's own motion detection fires an ONVIF event; we **skip decode + inference
  entirely** on cameras with no activity, and only run full analysis on cameras that report motion.
- **Resource saved:** schools are idle-heavy (nights, weekends, holidays, empty rooms during class) —
  plausibly **60–80% of camera-hours are idle.** Big power cut + lets us safely over-commit cameras
  per box (not all hot at once).
- **Feasibility:** **MEDIUM.** Needs an ONVIF event listener + gating logic. ⚠️ Confirm Adiva exposes
  ONVIF motion events (very likely). *Code:* extends `live_analytics.py` gating.
- **Accuracy:** **none IF** paired with a periodic heartbeat check so a person who **falls and goes
  still** isn't missed. The existing adaptive-rate gate already provides the "keep watching after
  motion stops" behaviour — this is the safeguard that makes ③ safe.

### ⑤ Cross-camera inference batching
- **Mechanism:** instead of one `detect(frame)` per camera, bundle frames from many cameras into a
  single batched GPU call. GPUs are parallel — batch-8 is barely slower than batch-1.
- **Resource saved:** **~3–5× detector throughput** at small batch sizes (yolov8n is GPU-underutilised
  at batch-1) — but this only converts to more cameras/box when **inference-bound.**
- **Feasibility:** **MEDIUM–HIGH (hardest).** Real refactor: a shared batched-inference queue that
  collects frames from per-camera threads, runs one inference, routes results back. Adds latency/
  complexity. *Code:* `detector.py` + the per-camera call path in `app.py` / `live_analytics.py`.
- **Accuracy:** **none** (identical model and math).

---

## Combined resource savings (estimate)
| Resource | Baseline | With ①②③⑤ | Reduction |
|---|---|---|---|
| Cameras per box | ~37 | ~**60–70** | ~1.8× |
| Boxes for 300 cams (incl. 1 spare) | **9** | **6** (opt. 5) | **−33%** |
| Decode load | 100% | ~45% | −55% |
| Analytics-path bandwidth | 100% | ~35% | −65% |
| Power draw | 100% | ~50–55% | ~−45–50% |

---

## Deployment-cost impact

### One-time (CapEx)
| Item | Baseline | Optimized | Note |
|---|---|---|---|
| Compute boxes | 9 × ₹85k = 7,65,000 | 6 × ₹85k = 5,10,000 | fewer boxes |
| Network aggregation | 1,00,000 | 90,000 | sub-stream cuts bandwidth |
| UPS + rack + PDU | 90,000 | 70,000 | fewer boxes |
| Deployment & commissioning | 2,00,000 | 2,00,000 | ~same |
| Subtotal | 11,55,000 | 8,70,000 | |
| GST @18% | 2,07,900 | 1,56,600 | |
| **Total one-time** | **₹13,62,900** | **₹10,26,600** | **−₹3.36 L (~−25%)** |

### Recurring (OpEx / yr)
| Item | Baseline | Optimized | Note |
|---|---|---|---|
| Electricity | 1,40,000 | ~70,000 | fewer boxes + motion gating |
| Hardware AMC + spares | 60,000 | 45,000 | fewer boxes |
| Software support / AMC | 2,00,000 | 1,90,000 | ~same |
| Connectivity | 20,000 | 20,000 | |
| **Total / yr** | **₹4,20,000** | **₹3,25,000** | **−₹95k (~−23%)** |

### 3-year TCO
| | Baseline | Optimized |
|---|---|---|
| One-time | 13,62,900 | 10,26,600 |
| OpEx ×3 | 12,60,000 | 9,75,000 |
| **3-yr TCO** | **₹26.2 L** | **₹20.0 L** |
| **Savings** | | **~₹6.2 L (~24%)** |

> One-time **product engineering** to build the four features (esp. ⑤ batching) is a separate,
> amortised R&D cost — it's paid once, then every deployment benefits.

---

## Will it be feasible? Will accuracy drop?
**Feasible: yes**, in this order (easiest/highest-value first):
1. **① Sub-stream** — low effort, big decode win. Do first.
2. **③ Motion triggering** — biggest OpEx/power win on an idle-heavy school site.
3. **② Hardware decode (NVDEC → +QuickSync)** — removes the decode wall.
4. **⑤ Batching** — last; hardest, and only pays off if the benchmark says *inference-bound*.

**Accuracy: no drop**, protected by two gates:
- **①** validate person-detection recall at 720p vs 1080p (expected identical).
- **③** keep a periodic heartbeat check so a motionless fallen person is never missed.
- **② and ⑤** are mathematically lossless (decoding / identical model).

---

## The critical dependency + two on-site checks
1. **Run the benchmark** on Adiva's real streams (main + sub) → answers **decode-bound vs
   inference-bound**, which sets the true cameras/box and picks ② vs ⑤ priority. *(Needs the GPU box.)*
2. ⚠️ **Confirm the Adiva sub-stream URL** (`…/ch1/sub/av_stream`) on-site.
3. ⚠️ **Confirm Adiva ONVIF motion events** with an ONVIF tool on-site.

Once those three are in, the estimates above become measured numbers and I'll reissue the cost sheet
as a firm quote basis.
