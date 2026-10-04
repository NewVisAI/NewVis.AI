# Research handoff — competitive / pricing / GTM work (2026-10-04)

This note orients a fresh Claude Code (or human) session on the competitive research done
in a cloud session on 2026-10-04, so it can be continued locally.

## What exists (read these first)
- `docs/research/competitive-landscape-and-strategy.md` — the consolidated report: feature
  scorecard vs ~30 competitors, pricing/valuations, build roadmap, differentiators, and
  India + international go-to-market. **Start here.**
- `docs/research/market.md` — India school CCTV analytics market sizing (TAM/SAM/SOM) and
  the first competitor pass.
- `.claude/agents/` — three reusable subagents built this session:
  - `research-analyst` — cited, evidence-based market/research. Output is cited and labelled.
  - `feature-scout` — give it company names; returns a full feature inventory + gap analysis
    vs this repo.
  - `security-auditor` — read-only security audit (project-agnostic).

## Critical caveat on the research
All competitor features, prices and funding were gathered in a cloud container whose **network
blocked web-page fetches**, and the **web-search budget ran out partway**. So most external
numbers are from search-result excerpts or public GitHub mirrors, **not pages read in full** —
treat anything externally-sourced as "verify before using in a pitch/deck/investor doc."
Internal/code findings were verified directly and are reliable.

**Running locally removes that limit** (no egress block, fresh search budget). To firm up the
thin spots, re-run the agents locally, e.g.:
- `feature-scout` on Protex AI, Voxel, Omnilert, Genetec, Avigilon, Hanwha (these came back
  partial), plus any quote-only pricing.
- `research-analyst` to verify the starred pricing (Verkada/Rhombus list prices, Cloudastructure
  10-K, Protex AWS listing) and the India items (GeM listings, Videonetics/Staqu/Agrex quotes).

## Top priorities the research surfaced (verified against this repo's code)
1. **Ultralytics YOLOv8 AGPL-3.0 licence = release blocker.** Imported in `detector.py`,
   `pose_verify.py` (on by default), `quantize.py`, `export_models.py`. On a plain reading
   incompatible with a closed-source, licence-key product. Get an Enterprise quote AND spike a
   permissive ONNX detector (D-FINE / RTMDet / DAMO-YOLO) + RTMPose for pose. Take legal advice.
2. **Alerts are console-only** (`alerts.py:289`); the fan-out hook exists — add WhatsApp/push/email.
3. **No camera tamper/offline alert** — extend the ONVIF PullPoint listener in `motion_gate.py`.
4. **Fall-detection safety gap** — pose verification only runs *after* the bbox rule fires, so it
   can't catch falls the box rule misses (a sideways fall-in-place is ~0.33 body-heights, below
   the 0.5 threshold → no alert). Add a head-drop cue + candidate-gated RTMPose; validate on
   staged falls.
5. **Re-ID** — 2 failing tests; verify whether `models/osnet_x1_0.onnx` is ImageNet-pretrained
   (would cause identity fragmentation).
6. **Groq cloud LLM** (`llm_parser.py`, `summary_manager.py`) contradicts the on-prem pitch —
   swap to a local LLM (llama.cpp/Ollama + Qwen3).
7. **Export over-claims** `cross_camera_batching=True` (`server.py:1773`) vs CLAUDE.md "not started".
8. **`README.md` is stale** (describes the mock `web_server.py`, not the real backend).

## Cheapest build pattern
trigger → burst → verify, using a ring buffer of the 640px JPEGs the reader already encodes.
Learned models run only on triggers; every stage adds a score and never suppresses an alert;
every feature stays behind an off-by-default flag with a promotion gate. Full roadmap (0–12 mo)
is in §6 of the consolidated report.

## Strategic one-liner
On-prem + camera-agnostic + no-face-recognition + DPDP-ready, sold into Indian CBSE schools
through the integrators already doing the retrofits. Lead with the deterministic CBSE
compliance autopilot (no accuracy risk). Package as a Milestone/Genetec plug-in for a later
US entry. Keep weapons/violence/audio as opt-in human-verified Beta.
