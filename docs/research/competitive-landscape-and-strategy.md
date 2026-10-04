# Sentinel AI — Competitive Landscape, Feature Gaps, Pricing and Go-to-Market

Prepared: 2026-10-04 · Scope: ~30 competitors across all domains; feature-by-feature comparison vs Sentinel; pricing and valuations; a cost-optimised build roadmap; India and international go-to-market.

> **How to read this report — sourcing caveat.** This consolidates nine parallel research runs. The cloud environment blocked outbound web page fetches for every vendor/news/government domain, and the shared web-search budget was exhausted partway through. So **almost every external figure comes from search-result excerpts or public GitHub mirrors, not from pages read in full.** Treat competitor features, prices, and funding as "reported, needs verification" before any go into a pitch, deck or investor doc. Where a fact was read in a primary text (e.g. the DPDP Rules, the GDPR, project source code, open-source LICENSE files) it is noted. Internal/code findings were verified directly against this repo and are reliable. To get a fully-sourced version, re-run from a machine with open web access, or widen this environment's Network settings and raise `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`.

---

## 1. Headline conclusions

1. **Sentinel cannot win on the feature list.** Natural-language video search, cross-camera "find this person," fall alerts and AI incident summaries — Sentinel's headline features — are already shipped by the well-funded leaders (Verkada, Genetec, Avigilon, Eagle Eye, Rhombus, Spot AI, Coram, Lumana) and are now built into Hikvision/Dahua recorders. Several are free (Eagle Eye search, Milestone's summary plug-in). Sentinel's versions are mostly **Beta**, and cross-camera Re-ID has **2 failing unit tests**.

2. **Sentinel's edge is delivery, sovereignty and existing cameras — in India specifically.** It runs on the cameras a school already owns (no proprietary camera/NVR/appliance), fully on-premise, with no face recognition by design and an audit log. That combination is genuinely rare among the profiled competitors, and it maps onto India's post-April-2026 reality (Chinese cameras barred from new sale, a large installed base that only vendor-neutral analytics can upgrade, DPDP children's-data rules, face-recognition backlash).

3. **The number-one blocker is not a feature — it's a licence.** Sentinel's detector and pose stack use Ultralytics YOLOv8 (**AGPL-3.0**). AGPL is, on a plain reading, incompatible with a closed-source product enforced by licence keys: it would oblige you to hand every school the full source, who could then legally strip the camera-cap and feature gating. **Resolve this before any client install** (installing on a customer's premises counts as conveying the software). This is the single most important finding in the whole exercise. Take legal advice; run the two-track remediation in §6.

4. **In India the real competitor is the "free" AI bundled with cameras/NVRs**, not a standalone startup. Buyers act because of compliance mandates, and no mandate requires analytics. Win on outcomes the bundled AI doesn't deliver (school-calendar-aware alerts, evidence clips in seconds, audit trail, multi-campus view, later cross-camera search) and on a **deterministic "CBSE compliance + camera-health" story that carries no accuracy risk**.

5. **Pricing:** US cloud software clusters around **$120–250 per camera per year**; India prices analytics as a **₹0 bundled hardware feature**. Sentinel's planning figure (₹2.5k–6.5k per camera, one-time) is structurally different (perpetual licence on customer hardware) and should be pitched as cheaper over 3–5 years — but in India the all-in cost is driven by **edge hardware, not the licence**.

---

## 2. The competitor field (≈30 companies, 6 groups)

| Group | Companies | How they sell AI | Relevance to Sentinel |
|---|---|---|---|
| Camera/NVR OEMs | Hikvision, Dahua, CP Plus, Axis, Hanwha, Matrix | AI bundled in camera/recorder, free or small one-time fee | **Sets the price floor.** Mostly tied to their own hardware. Hikvision/Dahua restricted in India since 1 Apr 2026 |
| VMS platforms | Milestone (+BriefCam), Genetec, Avigilon/Motorola, Eagle Eye | Platform licence + analytics add-ons, or cloud per-camera | Incumbent "system of record" at large sites. Sentinel plugs into them or replaces them |
| US cloud-native | Verkada, Rhombus, Spot AI, Coram | Recurring per-camera licence, usually + own appliance/camera | **Closest on features; furthest on business model.** Well funded |
| AI on existing cameras | Lumana, Ambient.ai, Vaidio, Actuate, Cloudastructure | Subscription per camera, usually + their appliance | **Closest business-model peers** |
| India-based | Videonetics, Staqu, Agrex.ai, Wobot.ai | Mix of on-prem licences and cloud plans | **Direct competitors in India.** Videonetics is the closest rival on a 300-camera campus deal (won IIT Delhi, ~300 cameras) |
| Vertical specialists | Veesion (retail), Intenseye/Protex/Voxel (factory safety), SafelyYou/Inspiren (elder care), ZeroEyes/Omnilert (guns) | Priced per outcome (theft, injury, falls, guns) | Set **high price points** and show how accuracy is proven in a niche |

**Note:** Intenseye ships response hardware literally branded **"Sentinel"** (Speaker/Beacon/Control). That is a trademark and positioning clash if Sentinel AI ever enters industrial safety.

---

## 3. Feature-by-feature scorecard

Key: **GA** = competitor ships it · **β** = Sentinel Beta · **P** = Sentinel Production · **—** = not found (not proof of absence) · "lead/behind/par" = Sentinel's standing.

### 3a. Where Sentinel is at parity or ahead

| Capability | Sentinel | Best competitor comparison | Standing |
|---|---|---|---|
| Runs on any RTSP/ONVIF camera, **no proprietary box** | P | Verkada/Rhombus need their own cameras or a paid connector; Spot AI/Coram/Lumana/Ambient/Eagle Eye need their own appliance/NVR | **Lead** (esp. India) |
| Fully **on-prem, internet-optional** | P (architecture) | All US cloud peers are cloud/hybrid; Milestone VLM is cloud pay-per-prompt | **Lead** — *but see caveat below* |
| **No face recognition, by design** | P (positioning) | Verkada, Rhombus, Coram, Hikvision, Dahua, Videonetics, BriefCam all ship face recognition | **Differentiator** under DPDP |
| School-calendar / holiday-aware after-hours alerts | P | Generic schedules exist widely; calendar-aware video alerts not found | **Lead** (niche) |
| Zones, intrusion, line-crossing, occupancy, loitering, dwell heatmap, headcount reports | P | Parity with everyone; OEMs/BriefCam go deeper (queues, mob, demographics) | **Par** |
| Alert → snapshot + jump-to-clip | P | Parity; Coram adds AI descriptions, others reach phones (Sentinel doesn't) | **Par on evidence, behind on delivery** |
| Investigation graph (cascades, recurring actors), running detection, violence heuristic, uploaded-file reprocessing | β / unlabelled | Not found in those exact forms | **Nominal lead, unproven** |

> **Caveat on two "lead" claims:** (1) NL search and AI summaries call the **Groq cloud LLM** (`llama-3.1-8b-instant`) when `GROQ_API_KEY` is set — so "data never leaves site" only holds with Groq off. (2) `backend/server.py:1773` hard-codes `"cross_camera_batching": True` in a customer-downloadable export, but `CLAUDE.md` says that lever is "not started." Both are credibility risks to fix before selling the on-prem/sovereignty story.

### 3b. Where Sentinel is behind (consolidated gap list, ranked for the India school buyer)

| # | Gap | Who has it | Why the buyer cares | Effort | Priority |
|---|---|---|---|---|---|
| 1 | **Alerts off the dashboard** (WhatsApp/SMS/push/email) | Nearly everyone | Principals/guards don't watch a console; India runs on WhatsApp | **S** — hook exists at `alerts.py` (`register_alert_callback`) | **High** |
| 2 | **Camera tamper / offline / blocked-view alerts** | Hikvision, Dahua, Verkada, Rhombus, Lumana, Videonetics | A dead camera at incident time is the classic complaint; CBSE needs working cameras + 15-day storage | **S** — extend ONVIF PullPoint listener in `motion_gate.py` | **High** |
| 3 | **Clip export + secure, expiring, redacted share links** | Verkada, Rhombus, Spot AI, Coram, Milestone | Hand evidence to parents/police with chain of custody | **S–M** — MP4 writer + Ed25519 signing already present | **High** |
| 4 | **Privacy masking / face blur on export** | Verkada, Intenseye, Lumana | DPDP children's-data duty; a clip shows other kids | **S–M** — body-mask using stored track boxes | **High** |
| 5 | **Alert triage: acknowledge / assign / resolve + true/false feedback** | Verkada, Coram | Accountability **and** generates the labelled data the Beta→Production gate needs | **S** — unused `acknowledged` column exists | **High** |
| 6 | **Appearance / attribute / natural-language search over footage** | Verkada, Rhombus, Coram, Lumana, Vaidio, Hikvision, Dahua | "Boy in red jacket at gate 2 at 3pm" is the core investigation | **M** — embed the ≤3 crops/track already saved | High (gated on Re-ID fix) |
| 7 | **Mobile app / installable PWA with push** | Verkada, Rhombus, Coram, Lumana, Hikvision, Matrix | Principals are phone-first | **M** (PWA); L (native) | High |
| 8 | **On-prem LLM** (remove Groq dependency) | n/a (it's a fix) | Removes a cross-border data flow under DPDP; works offline | **S** — llama.cpp/Ollama + Qwen3 | High |
| 9 | LPR/ANPR (gate, buses) | Hikvision, Dahua, CP Plus, Videonetics, Verkada, Rhombus, Vaidio | Gate vehicle logs, school buses | M–L (Indian plates hard) | Med |
| 10 | Documented public API + outbound webhooks | All VMS + US cloud | Integrators (your channel) and school ERPs | S | Med |
| 11 | Incident/case management | Milestone, Intenseye, Coram | Principals must document incidents | M | Med |
| 12 | MFA / SSO (Google/Microsoft) | Verkada, Rhombus, Eagle Eye, Lumana, Ambient | Schools on Google Workspace; protects child video | S–M | Med |
| 13 | Audio analytics (CBSE mandates A/V) | Hikvision, Dahua, Matrix, Staqu | Distress/aggression/alarm cues | M–L (no audio path today) | Med |
| 14 | Multi-site / central console | Milestone, Genetec, Eagle Eye, Lumana, Videonetics, Coram | School chains | L (single-node today) | Med |
| 15 | Abandoned object, queue length, tailgating | OEMs, BriefCam, Lumana, Ambient | Unattended bags; crowding | S–M | Med |
| 16 | Security certifications (SOC 2 / ISO 27001; STQC for India govt) | Verkada, Lumana, Ambient, ZeroEyes, Videonetics | Procurement checklists | L (process) | Med |
| 17 | Weapon detection | ZeroEyes, Omnilert, Coram, Vaidio, Ambient, Rhombus | High in US K-12; low in India | L | Low (Med for US) |
| 18 | Face recognition / attendance | Most OEMs + several US cloud | Most-requested India school use case **but** backlash + DPDP risk | L + policy | Deliberately excluded |

### 3c. How the specialists prove accuracy (the bar Sentinel must clear)

None of the eight vertical specialists publishes precision/recall. They sell **a number or an outside stamp plus human verification**: ZeroEyes puts its own 24/7 staffed centre between the AI and every alert (and holds a DHS SAFETY Act designation); SafelyYou reportedly uses clinicians; Veesion pushes a clip to store staff. **Sentinel's "advisory, human review" rule has no workflow behind it.** Building a verify/dismiss/assign queue that logs each operator decision (gap #5) both creates that workflow and produces a running precision figure from normal use — the cheapest path to the measured accuracy your own `FEATURE_STATUS.md` promotion gate demands. **Never publish an unmeasured accuracy claim** — the FTC's action against Evolv over school-scanner claims is the cautionary precedent.

---

## 4. Pricing and valuations

### 4a. Price ladder (per camera per year, normalised; FX ≈ ₹95.5/US$)

| Band | Offer | ≈ per camera/yr |
|---|---|---|
| Floor | Bundled OEM AI (CP Plus, Hikvision) | **₹0** (in hardware) |
| | Hikvision AI NVR spread over life | ₹600–2,390 one-time-equiv |
| | **Sentinel internal plan (licence only)** | **₹833–2,167** (≈$9–23) |
| | Sentinel all-in @300 cameras (licence + edge HW + power) | ₹3,058–5,081 (≈$32–53) |
| Mid | Wobot.ai list | $119.88 (₹11,449) |
| | Spot AI (Cullman school contract, base reading) | ~$120 (₹11,490) — *alt reading $24, disputed* |
| | Indian cloud analytics plans (low-trust) | ₹9,600–10,200 |
| | Rhombus Professional licence | $149 (₹14,230) |
| | Verkada licence (3/5/10-yr) | $220 (₹21,000) |
| | Verkada all-in w/ camera | $420–460 (₹40k–44k) |
| High | Cloudastructure | $420 · ZeroEyes ~$600 · Omnilert ~$1,000 · Protex ~$4,750 (yr 1, 20-cam site) |

**300-camera school, software only, per year:** Verkada ~$59.7k (₹56L) + ~$135k one-time connectors; Rhombus ~$44.7k (₹42L); Spot AI/Wobot ~$36k (₹34L); Indian cloud plans ₹29–31L; CP Plus AI NVRs ~₹1.4L one-time (basic AI only).

### 4b. Valuations / traction (reported)

| Company | Capital / valuation | Scale |
|---|---|---|
| Verkada | $5.8B valuation (Dec 2025); >$1B annualised bookings | 30,000+ orgs |
| Hikvision | ~RMB 92.5B revenue | — |
| CP Plus (Aditya Infotech) | ₹3,123cr FY25 revenue; listed Jul 2025 + ₹1,500cr QIP | 20.8%→~45% India share FY25→FY26 |
| Eagle Eye | $100M from SECOM (2023); **merged into Brivo Dec 2025** | bought Uncanny Vision (Bengaluru) |
| Ambient.ai | ~$75M; Securitas reseller deal (Mar 2026) | — |
| Coram | $66M total; revenue 4× YoY (claim) | 1,500+ sites |
| Lumana | $64M total ($40M Series A Jul 2025) | 50,000+ cameras (claim) |
| Rhombus | ~$100M total | — |
| ZeroEyes | ~$108M | — |
| Videonetics | ₹115cr PE (Florintree, 2023); ~₹61cr FY24 revenue | IIT Delhi, 80+ airports |
| Staqu / Wobot / Agrex | ~$2M / ~$5.5M / ~$110K | — |
| Irisity (listed) | revenue down 10%, still loss-making | a warning that standalone analytics software is a hard business |

### 4c. Recommended Sentinel price bands (hypotheses — Low confidence, validate with a pilot)

**India, per camera per year (ex-GST):** Basic ₹800–1,200 · Premium ₹1,200–2,000 · Pro ₹2,000–3,500 (discount Pro while Beta). Or a **one-time perpetual licence ≈2.5× annual + 18–20% AMC** for trusts that prefer capital spend (brackets the existing ₹2.5–6.5k plan). Per-school bundle (~64 cameras): **~₹1.1–1.95L/yr** including an edge box.
**International, software-only on customer hardware:** Basic $25–40 · Premium $45–70 · Pro $75–110 per camera/yr, site minimum ~$3,000/yr, 20–30% integrator discount. Prices below cloud peers because there's no cloud backup, no hardware supplied, and Pro is Beta.

> **Unresolved internal number that blocks pricing:** the compute cost for a 300-camera site is ₹25–40L in `SENTINEL_TECHNICAL_OVERVIEW.md` vs ~₹7.65L in the July cost report, and the overview's own line items add to ₹35.5–64.5L, not the ₹27–52L it states. Settle this before quoting Adiva — it decides whether Sentinel is cheap (~₹3–5k/camera) or near global SaaS (~₹10k/camera).

---

## 5. Internal findings to fix (verified against this repo — no research needed)

These surfaced repeatedly and are reliable because they were read in code:

1. **Ultralytics AGPL licence** — release blocker; see §6. (`detector.py`, `pose_verify.py` (on by default), `quantize.py`, `export_models.py`.)
2. **Alerts are console-only** — `alerts.py:289` ("Console output today…"); the fan-out hook exists but no WhatsApp/SMS/push/email.
3. **No camera tamper/offline alert** — only auto-reconnect; `motion_gate.py` ONVIF listener filters "motion" only.
4. **Fall detection safety gap** — pose verification runs *only after* the bounding-box rule fires, so it can't catch falls the box rule misses. By the test's own geometry a sideways fall-in-place drops the centroid ~0.33 body-heights, below the 0.5 threshold → **no alert**. Add a head-drop cue + candidate-gated RTMPose. Validate on staged falls.
5. **Re-ID** — 2 failing tests; OSNet may be running **ImageNet-pretrained** (not re-ID-trained) weights → identity fragmentation. Verify how `models/osnet_x1_0.onnx` was built.
6. **Groq cloud dependency** contradicts the on-prem claim (`llm_parser.py`, `summary_manager.py`).
7. **Export over-claims** `cross_camera_batching=True` vs `CLAUDE.md` "not started" (`server.py:1773`).
8. **`README.md` is stale** — describes the mock `web_server.py` dashboard, not the real backend.
9. **Licence enforcement** trusts the local clock (expiry) and revokes cameras on expiry — add an online time check + grace period before selling term licences to safety buyers.

---

## 6. Cheapest, smoothest way to close the gaps

**Architectural pattern that keeps every new feature cheap: trigger → burst → verify.** A per-camera ring buffer of the 640px JPEGs the reader thread *already encodes* gives pre-roll evidence clips, fall/fight verification bursts, and VLM checks at ~zero extra CPU. Learned models run only on triggers; each new stage adds a score and never suppresses an alert (the existing `POSE_VERIFY` pattern generalised); every feature stays behind an off-by-default flag with a promotion gate.

### 6a. The licence fix (do first, two tracks in parallel)
- **(a)** Get an Ultralytics **Enterprise** quote now (price not public); confirm it covers redistribution to customers' on-prem servers and edge boxes.
- **(b)** 2–3-week spike: an ONNX-Runtime detector backend returning the same `(x1,y1,x2,y2,conf,cls)` tuples + `names`. Permissive (Apache-2.0) candidates by hardware: **CPU/OpenVINO** → DAMO-YOLO or RTMDet-tiny; **NVIDIA** → D-FINE / DEIM / RT-DETRv2 (COCO-only weights); **Hailo** → DAMO-YOLO-T; **RK3588** → YOLOX-S / PP-YOLOE-S; **pose** → RTMPose-t/s. **Avoid** YOLOv9 (GPL), YOLOv10/BoxMOT (AGPL), YOLO-NAS & DEIMv2 weights (non-commercial). Acceptance gate: person recall ≥ YOLOv8n on the same labelled frames, latency within budget. Keep a model-provenance register (code licence, weight licence, training data) — many open weights carry non-commercial dataset terms.

### 6b. Build roadmap (cost-optimised; effort = estimate)

| Window | Items | Cash |
|---|---|---|
| **0–3 mo** | Licence remediation · fall-recall fix (head-drop cue + candidate-gated pose) · local LLM replacing Groq · ring-buffer evidence packs + redacted export · camera-health/tamper + **CBSE compliance report** · WhatsApp/push/email notifiers · alert-feedback loop · Re-ID weight audit + triage 2 failing tests | ~₹0 (+ licence fee if bought) |
| **3–6 mo** | Semantic/attribute search (SigLIP 2, multilingual, incl. Indian-language queries) · abandoned-object/queue/mustering/headcount-without-FR (Beta) · **edge-hardware benchmark on real streams** (Intel Core Ultra+OpenVINO vs Jetson Orin Nano Super) · event-gated fight stage + fall temporal model on consented staged footage · audio pilot (2–5 cameras) | ~₹0 + ~₹1L test HW |
| **6–12 mo** | ANPR at gates + gate safety · Milestone/Genetec plug-ins + ONVIF metadata output + tailgating · on-prem VLM alert verification (advisory) · full DPDP governance console · weapons only as opt-in human-verified Beta; PPE for labs/contractors | gate cameras + legal review |

### 6c. Edge hardware (estimates; prices VERIFY)
At 3 fps/camera, detector-only capacity ≈ 73 cameras (Jetson Orin Nano Super) or ~81 (Intel Core Ultra Arc iGPU); practical capacity is limited by **video decode and host CPU**, not the accelerator (≈16–48 cameras/box). Cost/camera: ₹1.1–2.3k (Orin Nano/RTX box), ₹1.3–3.8k (Intel Core Ultra mini-PC), ₹3.3–6.7k (Pi 5 + Hailo). **Intel Core Ultra + OpenVINO is the smoothest port** — same x86 stack, provider already in `inference_config.py`, QuickSync decode. Power matters: a 300W RTX box ≈ ₹21–26k/yr vs ~₹2k for a 25W Jetson.

### 6d. Honest limits on the hard detectors
- **Weapons:** CCTV gun detection is literally an "open problem" in the literature; training data is non-commercial; at scale, almost every alert is false without a human verifier. Keep as opt-in, human-verified Beta.
- **Violence:** F1 0.86–0.90 within a dataset collapses to 0.51–0.79 across datasets. Advisory only.
- **Audio "scream":** AudioSet has a "Children shouting" class — a playground will generate constant false alarms. Zone/time-scope heavily.
- **Semantic search:** best text-to-person retrieval is 60–73% top-1 on curated benchmarks, and **school uniforms strip out the colour signal**. Present results as ranked candidates for a human, never as answers.

---

## 7. Differentiators that could make Sentinel stand out

Few or none of the profiled competitors were found to offer these, and all fit the on-prem / no-face-recognition / school-first position (all ~₹0 cash, build on existing code):

- **D1 — CBSE/state CCTV compliance autopilot:** per-camera uptime + tamper log, coverage checklist against the CBSE clause, NVR retention check (≥15 days + backup), monthly signed PDF for inspections. Deterministic, no accuracy risk, maps directly onto why buyers spend. **This should be the lead product wedge.**
- **D2 — DPDP governance console:** data-map showing nothing leaves site, per-class retention + TTL on embeddings, reason-code for every clip view/search, redacted export, breach log, processor-agreement templates.
- **D3 — Governed, face-free cross-camera search:** deferred Re-ID only inside an incident case, two-person approval to "follow this person," expiring embeddings, bystanders masked on export.
- **D4 — Explainable evidence packs:** each alert ships pre+post-roll clip, the rule/thresholds that fired, confidence flags, model versions, a hash for chain of custody.
- **D5 — Offline-first, zero-cloud AI** (local LLM/VLM, signed offline updates).
- **D6 — Indian-language search & summaries** (SigLIP 2 multilingual + IndicTrans2 + Qwen3).
- **D7 — Attendance/headcount without face recognition** (door line-counts + occupancy vs timetable/roster).
- **D8 — Evacuation mustering** (live "unaccounted" count per building during a drill; auto-start from fire-alarm audio).
- **D10 — Alert-feedback learning loop** (one-click correct/false/unsure → per-camera tuning + auto-builds the labelled sets the promotion gate needs). **Highest leverage, smallest effort.**

---

## 8. Go-to-market — India

**Lead package: "Sentinel Campus Safe"** — Basic + Premium Production features + the new CBSE compliance report; Beta safety features switched on per site only after pilot measurement; "Investigate" (Re-ID/NL search) add-on only after Re-ID passes a stated bar.

- **Channel:** sell *through the CCTV system integrators already doing CBSE retrofits* — be a line item on their quote (edge box + annual licence + integrator margin + install training). CP Plus reports ~1,011 distributors / 2,100+ integrators; Matrix 2,500+. Sell direct only to chain/trust HQ. List on **GeM** (cheap to enter: ₹2,000 caution money, zero transaction fee under ₹10L) with integrators as authorised resellers; 2026 GeM bids already ask for AI video analytics.
- **Certifications by channel:** private schools need **no ER/BIS/STQC for software**. Do now (cheap): DPIIT startup recognition, Udyam, trademark, GeM brand approval. Defer STQC IoTSCS until a government/smart-city pipeline justifies it. For the appliance, buy BIS-registered OEM hardware rather than certifying a custom box.
- **Being Indian helps in public procurement:** Class-I local-supplier preference (≥50% local content), startup relaxations, and bidders from land-border countries (China) need prior clearance — i.e. the restriction squeezes Hikvision/Dahua-linked competitors, not you.
- **DPDP as a sales asset before 13 May 2027** (when children's-data obligations commence): ship the D2 governance console — 1-year audit/access-log retention (Rule 6), NTP sync (CERT-In), breach-support SLA aligned to Rule 7's 72 hours, a contractual safety-only purpose limit (the Fourth Schedule exempts school-safety monitoring), and the no-face-recognition commitment.
- **Messaging:** "Make the CCTV you installed for CBSE actually work for you — after-hours & restricted-area alerts, the exact clip in seconds, no face recognition, footage never leaves campus, DPDP-ready." Say "Beta" out loud for unmeasured features.
- **Pricing/pilot:** per-campus annual bands (§4c); a 6–8-week **paid pilot** (₹25–50k, credited on conversion) designed to produce numbers (staged fall/fight events with written consent, since real ones are rare).
- **Reality checks:** only the top fee decile of private schools can pay; government budgets are tiny (Haryana caps a school's whole CCTV system at ₹1.5L) and SI-led — treat government as a year-two play. **Open questions to answer with 15–20 integrator interviews + 15 principal interviews:** will integrators resell third-party analytics and at what margin; who signs (principal/trust/chain HQ); sales-cycle, payment terms and AMC norms; whether regional-language alerts matter to guards.

## 9. Go-to-market — International (launch later, months 12–24)

**Package: "Sentinel Investigate — VMS Edition"** — cross-camera appearance search, NL event search, incident summaries, plus Production zone/after-hours/loitering/line-crossing events pushed *into the customer's existing VMS*. Fall detection only after ≥90% recall is measured.

- **Why later:** the international package is mostly Pro features that are Beta today (incl. the 2 failing Re-ID tests). Gate it on measured accuracy from Indian sites first.
- **Cheapest credible channel = VMS ecosystems.** Milestone's App Center runs partner apps as Kubernetes/Helm containers on Linux (matches Sentinel's deployment); Genetec's Development Acceleration Program gives the SDK + a dev licence; ONVIF scene-description metadata over MQTT is a standard output. Then 2–3 integrator design partners, then a distributor (ADI) once a SKU + fixed reseller prices exist.
- **First market = US private K-12 / higher-ed already on Milestone or Genetec.** Privacy law rewards no-face-recognition (Illinois BIPA, state school-FR bans), and NDAA §889 / FCC rules are pushing Hikvision/Dahua out of publicly funded projects — but check whether 2 CFR 200.216 catches analytics running *on* installed covered cameras in grant-funded projects. Lead with **investigation (time-to-find), not prevention claims** (FTC v Evolv; efficacy doubts about hardened schools). Expect FERPA data-processing agreements, state student-privacy templates, HECVAT, and SOC 2/ISO 27001 asks.
- **GCC = opportunistic extension of the *India* package** (Indian-curriculum schools, Indian integrators, on-prem + data-residency preference, Dubai is Verkada's hub). It becomes the *first* international market **if** CBSE's CCTV clause binds CBSE schools abroad and a few hundred are reachable through 2–3 integrators.
- **Defer the EU** until counsel rules on whether Sentinel's whole-body Re-ID (OSNet 512-D embeddings + colour) is "biometric data" under the AI Act / GDPR. If it is, cross-camera Re-ID could be high-risk "remote biometric identification," the AI Act's profiling override would catch the per-person risk score, exam-hall monitoring is high-risk, and the Cyber Resilience Act adds duties for on-prem software.
- **Lessons from Indian peers abroad:** they went by acquisition (Uncanny Vision → Eagle Eye), hardware distribution (Matrix, 50+ countries), or USD-priced vertical SaaS (Wobot $9.99/cam/mo) — **none as a school specialist.** A credible open-source substitute (Frigate, MIT, 36k+ GitHub stars) exists at the low end; price and position above it on governance and support.

---

## 10. The one-paragraph answer

Sentinel is **feature-competitive on paper but behind on maturity and business model**, and its genuine, defensible edge is **on-prem + camera-agnostic + no-face-recognition + DPDP-ready, sold into Indian CBSE schools through the integrators already doing the retrofits.** Do three things before anything else: **(1) fix the Ultralytics AGPL licence** (release blocker), **(2) ship the cheap, deterministic wins** that need no research — WhatsApp alerts, camera-health/tamper, redacted evidence export, the CBSE compliance report, the alert-feedback loop — and **(3) make the Beta safety features honest** (fix the fall-detection gap, the Re-ID tests, and the Groq/on-prem contradiction) so you can publish measured accuracy instead of claims. Then price per campus in India, package as a Milestone/Genetec plug-in for the US, and keep weapons/violence/audio as opt-in human-verified Beta. Everything in this report is excerpt-sourced given the environment's network limits — verify the starred figures before they reach a customer.
