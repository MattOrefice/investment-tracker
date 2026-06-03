"""
Keeps the public Streamlit Cloud demo awake by visiting it with a real
headless browser (Chromium via Playwright) and clicking the wake button
if the app has gone to sleep.

Why a real browser? Streamlit Community Cloud's free tier sleeps an app
after ~12h of inactivity. A plain HTTP GET does NOT wake it — the request
gets a 200 static shell while the Python backend stays asleep. Only a real
browser that runs the page JS and opens the app's WebSocket counts as
activity and resets the idle timer (and, if already asleep, exposes the
"Yes, get this app back up!" wake button).

This script is run on a cron schedule by .github/workflows/keep-awake.yml.

Usage (from repo root):
    python scripts/keep_awake.py

Requirements:
    pip install playwright
    python -m playwright install chromium

Exit codes:
    0  app is awake (was already awake, or we successfully woke it)
    1  genuine failure (navigation timeout, page never rendered)
"""

import asyncio
import sys

from playwright.async_api import async_playwright

APP_URL = "https://mattorefice-investment.streamlit.app/"

# A real desktop UA so Streamlit Cloud treats us like a normal browser and
# follows its auth redirect (303) cleanly.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

NAV_TIMEOUT_MS = 120_000   # a cold wake is slow
SETTLE_MS = 5_000          # let the page settle before looking for the button
WAKE_SPINUP_MS = 60_000    # after clicking wake, give the app time to spin up

WAKE_BUTTON_NAME = "Yes, get this app back up!"


async def keep_awake() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        try:
            await page.goto(
                APP_URL,
                wait_until="domcontentloaded",
                timeout=NAV_TIMEOUT_MS,
            )
        except Exception as exc:  # navigation never completed — real failure
            print(f"FAIL — navigation to {APP_URL} failed: {exc}")
            await browser.close()
            return 1

        # Let the page settle so the wake button (if any) has rendered.
        await page.wait_for_timeout(SETTLE_MS)

        wake_button = page.get_by_role("button", name=WAKE_BUTTON_NAME)

        # Tolerant check: only click if the button is actually present, so an
        # already-awake app doesn't raise.
        if await wake_button.count() > 0:
            await wake_button.first.click()
            # Give the container time to spin up, then confirm the app rendered.
            await page.wait_for_timeout(WAKE_SPINUP_MS)
            try:
                # Streamlit always mounts a <div data-testid="stApp"> once the
                # app is actually running. Wait for it as the render signal.
                await page.wait_for_selector(
                    '[data-testid="stApp"]',
                    timeout=NAV_TIMEOUT_MS,
                )
            except Exception as exc:
                print(f"FAIL — clicked wake button but app never rendered: {exc}")
                await browser.close()
                return 1
            print("WAKE — app was asleep, woke it")
            await browser.close()
            return 0

        print("OK — app already awake")
        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(keep_awake()))
