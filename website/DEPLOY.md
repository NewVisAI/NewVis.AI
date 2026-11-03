# Launching gensolve.in

**Gensolve** is the company and **NewVis AI** is the product. `gensolve.in` serves the NewVis marketing
site: one landing page, a privacy notice and the "Request a callback" form.

The site is self-contained in `website/`. It does **not** need the CCTV engine, torch, OpenCV or a
GPU. It is three Python packages, runs on the smallest VPS you can rent, and stores leads in SQLite.

```
website/
  index.html      landing page (also served by backend/server.py at / for local dev)
  privacy.html    privacy notice (DPDP Act 2023), linked from the form and the footer
  404.html
  server.py       FastAPI: pages, /api/leads, security headers, rate limit
  static/         favicon, robots.txt, sitemap.xml, marketing/ (optional hero screenshot)
    showcase/     tracking.jpg (pipeline frame) + dashboard/*.webp (product-tour screenshots)
  tools/capture_dashboard.py   retakes the dashboard screenshots from a running NewVis server
deploy/
  docker-compose.yml   site + Caddy (automatic HTTPS)
  Caddyfile            gensolve.in, www → apex redirect, HSTS
  .env.example         admin token + optional SMTP for lead emails
```

## 1. Run it locally first

```bash
pip install -r website/requirements.txt
uvicorn website.server:app --port 8080
```

Open http://localhost:8080, submit the form, then check that the lead was stored:

```bash
LEADS_ADMIN_TOKEN=test uvicorn website.server:app --port 8080
curl -H "Authorization: Bearer test" http://localhost:8080/api/leads
```

## 2. Get a server

Any Linux VPS with a public IPv4 works: 1 vCPU and 1 GB RAM is plenty. Hostinger, DigitalOcean
(Bangalore), AWS Lightsail (Mumbai) and Hetzner are all fine. Install Docker:

```bash
curl -fsSL https://get.docker.com | sh
```

Open ports **80** and **443** in the provider's firewall.

## 3. Point the domain at it (at your registrar's DNS panel)

| Type | Name  | Value                 |
|------|-------|-----------------------|
| A    | `@`   | your server's IPv4    |
| A    | `www` | your server's IPv4    |
| AAAA | `@`, `www` | IPv6, only if the server has one |

Delete any registrar "parking" or "forwarding" records on `@`/`www`, because they conflict with the
records above. Check that DNS has propagated before step 4:

```bash
dig +short gensolve.in
```

## 4. Deploy

```bash
git clone https://github.com/NewVisAI/NewVis.AI.git && cd NewVis.AI/deploy
cp .env.example .env
nano .env
docker compose up -d --build
```

In `.env`, set `LEADS_ADMIN_TOKEN` (generate it with `openssl rand -hex 32`) and, ideally, the SMTP
settings so that every lead is also emailed to you.

Caddy gets the HTTPS certificate on its own within about a minute. Check:

```bash
curl -I https://gensolve.in
curl -I https://www.gensolve.in
```

The first should return 200 and the second a 301 to the apex.

Update later with `git pull && docker compose up -d --build`. Leads live in the `leads` Docker volume
and survive rebuilds.

## 5. Reading leads

```bash
curl -H "Authorization: Bearer $TOKEN" https://gensolve.in/api/leads
curl -H "Authorization: Bearer $TOKEN" "https://gensolve.in/api/leads?format=csv" -o leads.csv
```

Back up the volume now and then:

```bash
docker compose cp site:/data/leads.db ./leads-backup.db
```

## Pre-launch checklist

- [ ] **Company email on the domain.** The site and the privacy notice still say
      `newvis.team@gmail.com`. Set up `hello@gensolve.in` (Zoho Mail has a free tier, or Google
      Workspace), add its MX/SPF/DKIM records, then replace the address in `index.html`,
      `privacy.html` and `deploy/Caddyfile`.
- [ ] **Have the privacy notice reviewed.** `privacy.html` is a sensible starting point, not legal
      advice. Confirm the 24-month retention period and add the registered company name/address.
- [ ] **Hero screenshot (optional).** Drop a real dashboard PNG at
      `website/static/marketing/dashboard-hero.png`. Until then the page shows an animated mock and
      the browser logs one harmless 404.
- [ ] **Social preview image (optional).** Add a 1200×630 PNG and an `og:image` tag; link previews
      on WhatsApp/LinkedIn are text-only until then.
- [ ] **Search Console.** Verify `gensolve.in` in Google Search Console and submit
      `https://gensolve.in/sitemap.xml`.
- [ ] **Uptime ping** on `https://gensolve.in/healthz` (UptimeRobot free tier).

## Refreshing the product-tour screenshots

The tour uses real screenshots of the NewVis dashboard in `website/static/showcase/dashboard/`.
The current set was taken from a demo instance running on test footage, with a sample day of
events. To replace them with your live system, start the engine as usual (see the main
`CLAUDE.md`), then run this from the repo root on the same machine:

```powershell
.\.venv-win\Scripts\python.exe -m pip install playwright
.\.venv-win\Scripts\python.exe -m playwright install chromium
.\.venv-win\Scripts\python.exe website\tools\capture_dashboard.py --base http://localhost:8000 --user developer --password "<password>"
```

It logs in, opens each module, and overwrites the nine `.webp` files with the same names, so the
page needs no edits. Two things to do every time:

- **Look at every image before you push.** Real camera frames show real people, so blur faces
  (or pick a moment with nobody close to the camera).
- **Pick a search that your data answers well** with `--search "..."`. AI Search is Beta and
  currently ignores time ranges.

## What is deliberately *not* public

The operator dashboard (`backend/index.html`, served at `/app` by `backend/server.py`) is **not**
deployed here. It uses demo credentials and talks to the on-site engine, so it belongs on the
customer's network, not on the marketing domain. If you later want a hosted demo, put it on a
separate subdomain (e.g. `demo.gensolve.in`) with real credentials. Do not put it on the main site.
