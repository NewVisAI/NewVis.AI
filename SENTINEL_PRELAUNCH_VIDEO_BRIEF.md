# Sentinel AI — Prelaunch Video Brief

A single source for a video-generation MCP (Higgsfield.ai). Contains the product
positioning, the full feature list, verified stats, and a ready-to-shoot scene-by-scene
script with narration + on-screen visuals. Feed the whole file, or just the "Video Script"
section, to the generator.

---

## 1. Product at a glance
- **Name:** Sentinel AI (by Adiva)
- **Category:** AI video-analytics for school & campus CCTV
- **One-liner:** *Turn the cameras a school already has into a 24/7 safety intelligence system.*
- **Tagline options:**
  - "Every camera, watching for what matters."
  - "Safety that never blinks."
  - "See the incident before it becomes a headline."

## 2. Elevator pitch (voiceover-ready, ~15s)
> "Schools have hundreds of cameras — but no one can watch them all. Sentinel AI does. It
> understands every feed in real time: it spots a fall, an intruder, a fight, or a child in a
> restricted zone, and alerts staff the moment it happens — running on affordable hardware the
> school can actually afford."

## 3. Who it's for
Schools and campuses (the launch customer is a ~300-camera school). Buyers: administration,
security/facilities, principals. The promise: **more safety, less cost, no extra staff.**

## 4. The features (grouped for the video)

### A. Real-time safety detection
- **Fall detection** — flags a person who collapses or falls.
- **Intrusion / restricted-zone alerts** — someone enters a no-go area or after-hours.
- **Loitering detection** — a person lingering where they shouldn't.
- **Running detection** — sudden running (panic, chase, incident).
- **Violence / fight detection** — aggressive physical activity.
- **Line-crossing** — virtual tripwires across doorways, gates, perimeters.

### B. Intelligence & tracking
- **Cross-camera person tracking (Re-ID)** — follows the same person across cameras and floors,
  respecting a physical camera-topology map so hand-offs are realistic.
- **Zone-based rules** — draw zones on any camera and attach behaviours/alerts.
- **Movement heatmaps** — where people concentrate over time.
- **Live AI security summary** — plain-English summary of recent activity per camera, not raw logs.

### C. Alerts, reports & oversight
- **Instant notifications** to the principal/security inbox, with a snapshot of the exact moment
  and a jump-to-footage link.
- **Automated reports** — daily / monthly / yearly safety summaries.
- **Live multi-camera dashboard** — smooth real-time wall of every feed.

### D. Affordable, efficient deployment (the differentiator)
- **Runs on affordable RTX desktop boxes** — no datacenter, no per-camera cloud fees.
- **Edge + cloud ready** — process on-site, keep video local, send only what matters.
- **Cost-optimized engine** (all accuracy-preserving):
  - FP16 model quantization — faster inference at **zero accuracy loss**.
  - Sub-stream analytics — analyze low-res streams, record full-res.
  - Camera-side motion gating — skip work on idle cameras.
  - Hardware video decode (NVDEC) — offload decode off the CPU.
  - Adaptive-rate processing + deferred re-identification.

## 5. Verified stats (use as on-screen numbers)
- **0.999999** — cosine parity of the optimized (FP16) recognition model vs full precision → *no
  accuracy lost.*
- **98.8%** — person-detection recall retained on low-bandwidth sub-streams.
- **+22.3%** — more cameras per GPU from the deferred-recognition optimization (GPU benchmark).
- **~24%** — projected 3-year total-cost-of-ownership reduction from the efficiency stack.
- **300 cameras** — target school deployment on a handful of affordable boxes.
> Note: the throughput/capacity figures are from internal benchmarks; keep phrasing as
> "in our benchmarks" for accuracy.

## 6. Tone & visual style
- **Tone:** confident, calm, protective, modern. Not fear-mongering — *reassuring competence.*
- **Palette:** deep navy / near-black backgrounds, cyan-teal accents, clean white type, subtle
  green "safe" and amber "alert" highlights.
- **Motion:** smooth camera pushes, UI overlays animating in, detection boxes snapping onto people.
- **Music:** cinematic tech, building, hopeful resolve.
- **Length target:** 45–60 seconds.

---

## 7. Video Script (scene-by-scene)

**Scene 1 — The problem (0:00–0:08)**
- *Visual:* A wall of dozens of CCTV feeds in a dim control room; a single empty chair. Feeds
  flicker; no one is watching.
- *On-screen text:* "Hundreds of cameras. No one watching them all."
- *VO:* "A school's cameras see everything — but no human can watch them all."

**Scene 2 — Sentinel wakes up (0:08–0:16)**
- *Visual:* The feeds light up one by one with clean cyan detection boxes locking onto people;
  a "Sentinel AI" logo resolves.
- *On-screen text:* "Meet Sentinel AI."
- *VO:* "Sentinel AI watches every feed in real time — and understands what it sees."

**Scene 3 — Detection montage (0:16–0:32)**
- *Visual:* Fast cuts, each with an animated label:
  - a person stumbles and falls → red "FALL DETECTED"
  - a figure slips into a restricted corridor → amber "INTRUSION"
  - a shove between two students → red "VIOLENCE"
  - someone sprinting down a hall → "RUNNING"
  - a person crossing a virtual line at a gate → "LINE CROSSED"
- *VO:* "Falls. Intrusions. Fights. A child where they shouldn't be. Sentinel catches it the
  instant it happens."

**Scene 4 — Cross-camera intelligence (0:32–0:40)**
- *Visual:* A highlighted person walks from one camera view to another across a floor map; a
  glowing line connects the cameras; a phone buzzes with an alert + snapshot.
- *On-screen text:* "Tracks across every camera. Alerts in seconds."
- *VO:* "It follows a person across every camera — and alerts your staff in seconds, with the
  exact moment captured."

**Scene 5 — Affordable & efficient (0:40–0:50)**
- *Visual:* A single compact desktop box glowing, powering a grid of 300 camera thumbnails; a
  cost bar dropping; a green "NO ACCURACY LOST" checkmark.
- *On-screen text:* "Runs on affordable hardware. ~24% lower cost. Zero accuracy lost."
- *VO:* "And it runs on hardware a school can actually afford — cutting deployment cost by nearly
  a quarter, with no compromise on accuracy."

**Scene 6 — Close (0:50–0:60)**
- *Visual:* Sunlit school hallway, students safe; the Sentinel logo with a steady cyan "eye"
  pulse; tagline resolves.
- *On-screen text:* "Sentinel AI — Safety that never blinks."
- *VO:* "Sentinel AI. Safety that never blinks."
- *End card:* Adiva logo · "Now onboarding schools."

---

## 8. Asset checklist for the generator
- Product name text: **Sentinel AI**, sub-brand **by Adiva**
- Alert label styles: red (critical: fall/violence/intrusion), amber (warning: loiter/line-cross)
- Numbers to animate: 300 cameras · +22.3% · 98.8% · ~24% lower cost · zero accuracy lost
- Recurring motif: a calm cyan "eye"/scan pulse = Sentinel is watching
- Avoid: gore, distress, real children's faces, anything fear-based — keep it reassuring
