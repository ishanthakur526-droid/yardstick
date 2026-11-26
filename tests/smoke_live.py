"""Run by hand against the live link (not part of pytest):

    python tests/smoke_live.py https://<your-app>.streamlit.app [--mobile]

Opens the page like a stranger would (no sign-in, no cookies), walks every section, and fails loudly if anything is
missing, shows an error, or asks for a login. Needs Playwright (dev-only, not in requirements.txt).
"""

import sys

from playwright.sync_api import sync_playwright

SECTIONS = {
    "See it": ["Arrived from vendors", "Where it piled up", "All 16 items at a glance"],
    "Plan it": [
        "Average idle stock",
        "What the planner says, one month at a time",
        "Download the plan (Excel)",
        "What the saving depends on",
        "How good is the forecast?",
    ],
    "Requisition": [
        "Items needed",
        "To buy",
        "Where Lumino's ratios and the site disagree",
        "Download the buy list (Excel)",
    ],
    "Match the sets": ["agree with what the site really used", "Some items only work as a set"],
    "About the data": ["Where the numbers come from"],
}


def main(url: str, mobile: bool) -> int:
    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 900})
        page = ctx.new_page()
        # Streamlit Community Cloud shows the app inside an iframe served from /~/+/. Open that route so the app's own
        # elements can be read; if the app asked for a sign-in, this route would show it instead of the app.
        target = url.rstrip("/") + "/~/+/" if "streamlit.app" in url and "/~/+/" not in url else url
        page.goto(target, wait_until="domcontentloaded", timeout=120000)
        try:
            page.wait_for_selector('[data-testid="stMetric"]', timeout=180000)
        except Exception:
            failures.append("the first screen never finished loading (or the app asked for a sign-in)")
        page.wait_for_timeout(4000)  # the lower parts of the first screen draw after the first numbers
        body = page.inner_text("body")
        for must in ("Yardstick", "of 16 items", "48,991"):
            if must not in body:
                failures.append(f"first screen is missing: {must!r}")
        for bad in ("Traceback", "This app has no project data", "Sign in", "You do not have access"):
            if bad in body:
                failures.append(f"first screen shows: {bad!r}")

        for name, expected in SECTIONS.items():
            if name != "See it":
                page.locator('[data-testid="stButtonGroup"] button', has_text=name).first.click()
                try:
                    page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=60000)
                except Exception:
                    pass
                page.wait_for_timeout(2500)
            try:  # wait for the last thing on the screen, so a slow first calculation is not mistaken for a missing one
                page.get_by_text(expected[-1]).first.wait_for(timeout=60000)
            except Exception:
                pass
            text = page.inner_text("body")
            for must in expected:
                if must not in text:
                    failures.append(f"{name}: missing {must!r}")
            if "Traceback" in text or page.locator('[data-testid="stException"]').count():
                failures.append(f"{name}: shows an error")
            print(f"{name}: {'ok' if not any(f.startswith(name) for f in failures) else 'PROBLEM'}")

        page.locator('[data-testid="stButtonGroup"] button', has_text="Plan it").first.click()
        page.wait_for_timeout(2500)
        with page.expect_download(timeout=30000) as dl:
            page.get_by_text("Download the plan (Excel)").first.click()
        name = dl.value.suggested_filename
        if not name.endswith(".xlsx"):
            failures.append(f"Excel download has an odd name: {name}")
        print("Excel download:", name)

        page.locator('[data-testid="stButtonGroup"] button', has_text="Requisition").first.click()
        page.wait_for_timeout(2500)
        with page.expect_download(timeout=30000) as dl2:
            page.get_by_text("Download the buy list (Excel)").first.click()
        name2 = dl2.value.suggested_filename
        if not name2.endswith(".xlsx"):
            failures.append(f"Buy list download has an odd name: {name2}")
        print("Buy list download:", name2)
        browser.close()

    if failures:
        print("\nFAILED:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nAll good: the public link works from start to end.")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else "http://localhost:8501", "--mobile" in sys.argv))
