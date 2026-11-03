"""Capture the product-tour screenshots for gensolve.in from a running NewVis dashboard.

Point it at any running NewVis server (your live box with real data, or a demo
instance) and it logs in, opens each module and saves cropped, web-ready PNGs
into website/static/showcase/dashboard/. The website picks them up as-is.

    pip install playwright && python -m playwright install chromium
    python website/tools/capture_dashboard.py --base http://localhost:8000 \
        --user developer --password "<password>"

Options:
    --search "<query>"   the AI Search question to show (pick one your data answers well)
    --zone-cam <id>      camera to show in the zone editor (default: first camera)
    --chromium <path>    use an existing Chromium binary instead of Playwright's own

Before publishing, look at every image: the camera frames show real people.
"""

import argparse
import os
import sys

from PIL import Image, ImageStat
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "..", "static", "showcase", "dashboard")

# Hide things that are noise in a marketing screenshot: toasts, the websocket
# status pill (and the mock "Zone N triggered" toasts from initRealtimeUpdates),
# entry animations, and the "ambient animation" in index.html that
# periodically puts filter:blur() on the whole <body>.
CLEAN_CSS = """
body { filter: none !important; }
#toasts, #toast-container, .ws-pill { display: none !important; }
*, *::before, *::after { animation-duration: 0s !important; animation-delay: 0s !important;
                         transition-duration: 0s !important; }
"""


def finish(png_path, max_h=None, max_w=1400):
    """Trim flat empty rows off the bottom, cap the height, shrink, save as WebP."""
    im = Image.open(png_path).convert("RGB")
    w, h = im.size
    bottom = h
    while bottom > h // 2 and ImageStat.Stat(im.crop((0, bottom - 4, w, bottom))).stddev[0] < 6:
        bottom -= 4
    im = im.crop((0, 0, w, min(bottom + 8, h)))
    if max_h and im.height > max_h:
        im = im.crop((0, 0, im.width, max_h))
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    out = os.path.splitext(png_path)[0] + ".webp"
    im.save(out, "WEBP", quality=84, method=6)
    os.remove(png_path)
    return out


def card(page, view, heading):
    """The .card inside a view whose header contains the given text."""
    return page.locator(f"#view-{view} .card").filter(
        has=page.locator(".card-h", has_text=heading)).first


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--user", default="developer")
    ap.add_argument("--password", required=True)
    ap.add_argument("--search", default="who entered the restricted zone")
    ap.add_argument("--zone-cam", default="")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--chromium", default=os.getenv("CHROMIUM_PATH", ""))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    def shot(loc, name, max_h=None):
        path = os.path.join(a.out, name)
        loc.screenshot(path=path)
        print("saved", os.path.relpath(finish(path, max_h=max_h)))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**({"executable_path": a.chromium} if a.chromium else {}))
        page = browser.new_context(viewport={"width": 1440, "height": 900},
                                   device_scale_factor=1.5).new_page()
        page.goto(a.base.rstrip("/") + "/app")
        page.fill("#login-user", a.user)
        page.fill("#login-pass", a.password)
        page.click("#login-btn")
        page.wait_for_selector("#login-screen", state="hidden", timeout=15000)
        page.add_style_tag(content=CLEAN_CSS)

        def go(view, wait=2500):
            page.evaluate("v => navigate(v)", view)
            page.wait_for_timeout(wait)

        # 1. Live camera grid
        go("cameras", 1500)
        page.click("#grid-start")
        page.wait_for_timeout(6000)
        shot(card(page, "cameras", "Live Camera Grid"), "live-grid.png")

        # 2. Notifications inbox + 3. the snapshot behind one alert
        go("notifications")
        shot(page.locator("#view-notifications .card").first, "alerts-inbox.png")
        # Expand the snapshot inline on the newest restricted-area alert (or the newest alert).
        items = page.locator("#view-notifications div").filter(has=page.locator("button[data-alert]"))
        target = items.filter(has_text="Restricted area entry")
        target = (target if target.count() else items).last
        target.locator("button[data-alert]").first.click()
        page.wait_for_timeout(2500)
        shot(target, "alert-snapshot.png")

        # 4. Zone editor with a polygon being drawn + 5. the rules table
        go("zones", 3000)
        if a.zone_cam:
            page.select_option("#zone-draw-cam", a.zone_cam)
            page.wait_for_timeout(2500)
        page.click("#btn-edit-zone")
        page.wait_for_timeout(500)
        box = page.locator("#zone-canvas").bounding_box()
        for fx, fy in [(0.07, 0.32), (0.46, 0.27), (0.47, 0.62), (0.07, 0.68)]:
            page.mouse.click(box["x"] + fx * box["width"], box["y"] + fy * box["height"])
            page.wait_for_timeout(150)
        page.mouse.move(box["x"] + 0.30 * box["width"], box["y"] + 0.80 * box["height"])
        shot(card(page, "zones", "Draw a zone"), "zones-draw.png")
        shot(card(page, "zones", "Zones & Alert Rules"), "zones-rules.png", max_h=800)

        # 6. AI search
        go("search", 800)
        page.fill("#search-input", a.search)
        page.click("#search-btn")
        page.wait_for_timeout(3000)
        shot(page.locator("#view-search"), "search.png")

        # 7. Reports charts + 8. heatmap
        go("reports", 3500)
        shot(card(page, "reports", "Headcount over time"), "reports-headcount.png")
        shot(card(page, "reports", "Zone flow"), "reports-zoneflow.png")
        go("heatmap", 3500)
        shot(card(page, "heatmap", "Spatial Heatmap"), "heatmap.png")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
