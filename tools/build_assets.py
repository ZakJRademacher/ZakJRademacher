"""Build the profile README images.

Renders the banner (light + dark) and one framed preview per project from
HTML templates with headless Chromium, then writes compressed WebP/PNG files
into ../assets.

    python tools/build_assets.py            # banner and cards
    python tools/build_assets.py --cards    # cards only

Each card is named for the project version it shows (card-<slug>-<version>.webp,
from shots/<slug>-<version>.webp), so a new version gets a new file and GitHub's
image cache never serves an old picture.

Requires: playwright (with Chromium, or CHROMIUM_PATH pointing at Chrome or
Edge) and Pillow. Inter and JetBrains Mono load from Google Fonts.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "shots"
OUT = ROOT.parent / "assets"

ACCENT = "#3ecf9b"

# slug -> (host shown in the window's address bar, project version the shot shows)
PROJECTS = {
    "workbench": ("Windows app · data-workbench", "3.0.0"),
    "perigee": ("perigee.zakjrademacher.workers.dev", "1.5.0"),
    "mag7": ("zakjrademacher.github.io/magnificent-seven-dashboard", "1.0.0"),
    "stringlab": ("string-lab.zakjrademacher.workers.dev", "1.0.1"),
    "commerce": ("zakjrademacher.github.io/commerce-delivery-review", "1.0.0"),
    "monthly": ("zakjrademacher.github.io/monthly-sales-review", "1.0.0"),
    "hotel": ("zakjrademacher.github.io/hotel-booking-cancellation-analysis", "1.0.0"),
    "marketing": ("zakjrademacher.github.io/marketing-campaign-performance", "1.0.0"),
}

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800'
         '&family=JetBrains+Mono:wght@400;500;600&display=block">')

THEMES = {
    "dark": dict(
        bg="#0b0d10", fg="#eef1f4", muted="#8b949e", faint="#5b6470",
        dot="rgba(255,255,255,.07)", border="#1f242b",
        term="#11151a", term_border="#232a32",
    ),
    "light": dict(
        bg="#fbfbfa", fg="#0e1116", muted="#57606a", faint="#8c959f",
        dot="rgba(13,17,23,.08)", border="#e4e6e8",
        term="#0f1318", term_border="#0f1318",
    ),
}

BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{background:transparent}
body{font-family:'Inter',sans-serif;-webkit-font-smoothing:antialiased;
     font-feature-settings:'ss01','cv11','tnum'}
.mono{font-family:'JetBrains Mono',monospace}
"""


def banner_html(t: dict) -> str:
    rows = [
        ("orders", "99,441"),
        ("item lines", "112,650"),
        ("late deliveries", "6,534"),
        ("on-time rate", "93.2%"),
    ]
    row_html = "".join(
        f'<div class="row"><span>{k}</span><span class="dots"></span><b>{v}</b></div>'
        for k, v in rows
    )
    stack = " <i>/</i> ".join(
        ["Python", "SQL", "DuckDB", "FastAPI", "JavaScript", "Power BI", "Tableau"]
    )
    return f"""<!doctype html><html><head>{FONTS}<style>{BASE_CSS}
.b{{width:1280px;height:320px;border-radius:18px;background:{t['bg']};
   border:1px solid {t['border']};position:relative;overflow:hidden;display:flex;
   align-items:center;padding:0 64px}}
.grid{{position:absolute;inset:0;
   background-image:radial-gradient({t['dot']} 1.2px,transparent 1.2px);
   background-size:22px 22px;
   -webkit-mask-image:linear-gradient(90deg,transparent 30%,#000 75%)}}
.glow{{position:absolute;right:-120px;top:-160px;width:620px;height:620px;border-radius:50%;
   background:radial-gradient({ACCENT}22,transparent 62%)}}
.l{{position:relative;flex:1}}
.eyebrow{{font-size:14px;letter-spacing:.18em;color:{t['muted']};display:flex;align-items:center;gap:12px}}
.eyebrow:before{{content:'';width:28px;height:2px;border-radius:2px;background:{ACCENT}}}
h1{{font-family:'Inter Display','Inter';font-weight:600;font-size:66px;letter-spacing:-.035em;
   color:{t['fg']};margin:18px 0 10px;line-height:1}}
p{{font-size:23px;color:{t['muted']};letter-spacing:-.01em}}
.stack{{margin-top:30px;font-size:16px;color:{t['faint']};letter-spacing:.02em}}
.stack i{{font-style:normal;color:{t['border']};margin:0 4px}}
.term{{position:relative;width:390px;background:{t['term']};border:1px solid {t['term_border']};
   border-radius:12px;padding:18px 22px 20px;color:#c9d1d9;font-size:14px;
   box-shadow:0 30px 60px -30px rgba(0,0,0,.55)}}
.bar{{display:flex;gap:7px;align-items:center;margin-bottom:16px}}
.bar s{{width:10px;height:10px;border-radius:50%;background:#2d333b}}
.bar span{{margin-left:auto;font-size:11.5px;color:#6e7681}}
.cmd{{color:#6e7681;margin-bottom:12px}} .cmd b{{color:#e6edf3;font-weight:400}}
.row{{display:flex;align-items:baseline;gap:8px;line-height:27px;color:#8b949e}}
.dots{{flex:1;border-bottom:1px dotted #30363d;transform:translateY(-4px)}}
.row b{{color:#e6edf3;font-weight:500}}
.ok{{margin-top:12px;color:{ACCENT}}}
</style></head><body>
<div class="b"><div class="grid"></div><div class="glow"></div>
 <div class="l">
  <div class="eyebrow mono">PYTHON DEVELOPER · DATA ANALYST</div>
  <h1>Zak Rademacher</h1>
  <p>I build data tools and reports that check their own work.</p>
  <div class="stack mono">{stack}</div>
 </div>
 <div class="term mono">
  <div class="bar"><s></s><s></s><s></s><span>commerce-delivery-review</span></div>
  <div class="cmd">$ <b>python src/verify_snapshot.py</b></div>
  {row_html}
  <div class="ok">✓ totals reconcile to source</div>
 </div>
</div></body></html>"""


def card_html(shot_b64: str, host: str) -> str:
    return f"""<!doctype html><html><head>{FONTS}<style>{BASE_CSS}
.w{{width:960px;border-radius:14px;overflow:hidden;background:#0d1117;
   border:1px solid #2a3038}}
.top{{height:44px;display:flex;align-items:center;padding:0 16px;gap:8px;
   background:#161b22;border-bottom:1px solid #2a3038;position:relative}}
.top s{{width:11px;height:11px;border-radius:50%;background:#30363d}}
.url{{position:absolute;left:50%;transform:translateX(-50%);max-width:640px;
   height:26px;padding:0 14px;border-radius:7px;background:#0d1117;color:#8b949e;
   font-size:12.5px;display:flex;align-items:center;gap:8px;white-space:nowrap}}
.url:before{{content:'';width:7px;height:7px;border-radius:50%;background:{ACCENT}}}
.img{{width:960px;height:720px;background:url(data:image/webp;base64,{shot_b64})
   center top/100% auto no-repeat}}
</style></head><body>
<div class="w"><div class="top"><s></s><s></s><s></s>
<div class="url mono">{host}</div></div><div class="img"></div></div>
</body></html>"""


def shoot(page, html: str, selector: str, scale: int) -> Image.Image:
    page.set_content(html)
    page.evaluate("document.fonts.ready")
    png = page.locator(selector).screenshot(omit_background=True, scale="device")
    return Image.open(io.BytesIO(png))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the profile README images.")
    parser.add_argument("--cards", action="store_true", help="rebuild the project cards only")
    cards_only = parser.parse_args().cards
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        # CHROMIUM_PATH lets you point at a preinstalled browser.
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_page(device_scale_factor=2, viewport={"width": 1400, "height": 900})

        for name, theme in ({} if cards_only else THEMES).items():
            img = shoot(page, banner_html(theme), ".b", 2)
            img.save(OUT / f"banner-{name}.png", optimize=True)

        page_1x = browser.new_page(device_scale_factor=1, viewport={"width": 1100, "height": 900})
        for slug, (host, version) in PROJECTS.items():
            b64 = base64.b64encode((SHOTS / f"{slug}-{version}.webp").read_bytes()).decode()
            img = shoot(page_1x, card_html(b64, host), ".w", 1)
            img.save(OUT / f"card-{slug}-{version}.webp", quality=86, method=6)

        browser.close()
    for f in sorted(OUT.iterdir()):
        print(f"{f.name:28} {f.stat().st_size/1024:7.1f} KB")


if __name__ == "__main__":
    main()
