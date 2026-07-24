# Sentinel AI — Demo Brief for Project Manager (2026-07-17)

Talking points for the demo. Goal: show **what shipped since last week**, prove the
**deployment cost has come down with measured numbers**, and lay out **quantization** as the
next lever to cut cost further.

> Server is running at **http://localhost:8000** · login `developer` / `dev@sentinel` ·
> Video Analytics tab → pick a camera → **Run live analytics**.

---

## 1. One-line message for the PM
> "In the last week we made Sentinel **meaningfully cheaper to deploy per camera** — measured
> **+22% throughput** on a real GPU and **~36% less processing on idle cameras** — and we added
> a live **AI security summary**. Quantization is the next step and can push cost down further."

---

## 2. What's new since last week

| Area | What it does | Why the PM cares |
|---|---|---|
| **Deferred Re-ID** (`REID_DEFERRED=1`) | Skips the heavy cross-camera OSNet model in the live path; runs it on-demand at search time instead | **More cameras per GPU → fewer GPUs → lower hardware cost** |
| **Adaptive-rate gate** | Runs full analysis at 3 fps when a person is present, drops to 1 fps when the camera is empty (8 s hangover) | **~36% less compute on quiet cameras** (schools are idle much of the day/night) |
| **Live analytics (decoupled feed)** | Raw video streams smooth at ~20 fps; AI runs separately at ~3 fps | Smooth demo; display never stutters even under load |
| **AI Security Summary** (new this week) | Natural-language summary of recent alerts per camera, shown as a live overlay on the feed | **Operators read plain English, not raw event logs** — strong demo moment |
| **Stability + DB fix** | Removed crash sources; moved DB off the slow WSL mount | Every page now loads in **<0.1 s** (was 3–7 s) — visibly snappy |

**Demo flow suggestion:** open a camera → Run live analytics → point out (a) the smooth feed,
(b) events landing in Notifications, (c) the **AI Security Summary overlay** describing what
happened in words.

---

## 3. The cost-reduction story (with numbers)

**The dominant deployment cost is GPUs.** Anything that raises *cameras-per-GPU* cuts the
GPU count for a fixed camera fleet — that is the lever we moved.

### Measured on a real NVIDIA RTX 5060 (Blackwell) laptop GPU — your friend's benchmark:

| Mode | Throughput | Capacity @ 3 fps |
|---|---|---|
| Re-ID live (`REID_DEFERRED=0`) | 429.4 FPS | ~143 cameras/machine |
| **Re-ID deferred (`REID_DEFERRED=1`)** | **525.2 FPS** | **~175 cameras/machine** |
| **Gain** | **+22.3%** | **+32 cameras/machine** |

**What that means in plain terms:** deferring Re-ID lets **one GPU cover ~22% more cameras**.
For a fixed fleet, that is **~22% fewer GPUs** — the single biggest line item in the quote.

**On top of that**, the adaptive-rate gate cuts **~36% of processing on idle cameras**. Schools
are quiet overnight, during class, on weekends/holidays — so in a real deployment a large share
of camera-hours are idle, and that saving compounds with the Re-ID gain.

> **Honesty note (so the numbers hold up under questions):** 143/175 cameras are *synthetic
> throughput* figures (model inference rate). Real-world capacity is lower once RTSP decode, DB
> writes and networking are included — treat them as an **upper bound and a relative comparison**,
> not a hard capacity promise. The **+22.3% relative gain is the reliable takeaway.**

### Trade-off to state up front (don't let it surprise them later)
Deferred Re-ID moves **live cross-camera identity** to **search-time** (you query "where else did
this person appear?" on demand instead of it being maintained live). **Per-camera safety events —
fall, loitering, intrusion, running — stay fully live.** This is a deliberate product decision; the
saving is large and the lost capability is recoverable on demand.

---

## 4. Quantization — the next lever to cut cost further (proposal)

**Idea in one line:** run the AI models in lower numeric precision (FP16 or INT8) instead of
full FP32, so each camera costs less GPU compute and memory — **more cameras per GPU, and even
cheaper GPUs become viable.**

### Why it works
- Models today run in **FP32** (32-bit floats). GPUs run **FP16/INT8 far faster** and use less
  memory — often the same accuracy at a fraction of the cost.
- **We are already quantization-ready:** OSNet is exported to ONNX with **0.9997 cosine parity**
  (verified earlier), so plugging into ONNX Runtime / TensorRT INT8 is a natural next step.

### Expected gains (industry-typical — to be measured on our models, not promised yet)
| Precision | Typical throughput vs FP32 | Accuracy impact |
|---|---|---|
| **FP16 (half)** | ~1.5–2× faster | Negligible — usually the easy win |
| **INT8** (with calibration) | ~2–4× faster, lower VRAM | Small drop; must be validated |

Stacked on the +22% we already have, FP16 alone could push cameras-per-GPU **substantially**
higher, and INT8 can let us use **smaller/cheaper GPUs** (lower VRAM), cutting both the
**one-time hardware cost** and the **ongoing power bill**.

### The one caution to voice (it builds credibility)
This is **safety software** — we must not trade away detection recall (missing a fall or an
intrusion is unacceptable). So the plan is **measure-first**: quantize → validate YOLO detection
recall and OSNet cosine similarity against the FP32 baseline → only ship precision levels that
hold accuracy. **No accuracy regression ships.**

### Ask / next step for the PM
- Approve a short **quantization spike**: FP16 first (low risk), then INT8 with calibration,
  each benchmarked on our own footage with an accuracy gate.
- Deliverable: a table of **cameras-per-GPU and accuracy at FP32 vs FP16 vs INT8**, so cost
  savings are backed by measured numbers before we commit to hardware.

---

## 5. If asked "is it production-ready?" — the honest status
- **Working now:** live per-camera analytics, events/alerts, notifications, AI summary, licensing
  enforcement, fast UI. Great for demo and pilot.
- **Before a paid rollout:** move to PostgreSQL, make the multi-camera pool engine spawn-safe (or
  standardise on the current per-camera live path), and harden licensing (production keypair kept
  off-machine). These are known and scoped, not surprises.

---

## 6. Quick-reference numbers (memorise these three)
- **+22.3%** more cameras per GPU from deferred Re-ID (measured, RTX 5060).
- **~36%** less compute on idle cameras (adaptive-rate gate).
- **<0.1 s** page loads after the database fix (was 3–7 s).
- **Next:** quantization (FP16/INT8) → target further **1.5–4×** on model inference, measure-first.
