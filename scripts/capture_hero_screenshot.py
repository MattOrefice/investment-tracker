"""
Captures a hero screenshot of the Benchmark Attribution page from a
locally-running Streamlit demo instance. Saves to
docs/images/hero_benchmark_attribution.png.

Run manually when the page's visual centerpiece changes meaningfully.
Not part of CI — screenshots should not be regenerated on every push.

Usage (from repo root):
    python scripts/capture_hero_screenshot.py

Requirements:
    pip install playwright
    python -m playwright install chromium
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

OUTPUT_PATH = Path("docs/images/hero_benchmark_attribution.png")
STREAMLIT_PORT = 8502   # 8502 to avoid clashing with any running instance on 8501
SETTLE_DELAY_MS = 15000
VIEWPORT = {"width": 1920, "height": 1080}


def _wait_for_port(port: int, timeout: int = 40) -> bool:
    """Poll until localhost:port accepts connections or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


async def capture() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    env = {**os.environ, "TRACKER_MODE": "demo"}
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "app.py",
            "--server.headless=true",
            f"--server.port={STREAMLIT_PORT}",
            "--server.runOnSave=false",
            "--browser.gatherUsageStats=false",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        print(f"Starting Streamlit on port {STREAMLIT_PORT}...")
        if not _wait_for_port(STREAMLIT_PORT, timeout=40):
            raise RuntimeError("Streamlit failed to start within 40 seconds")
        # Extra settle time for Python module loading and DB init
        time.sleep(4)
        print("Streamlit ready. Launching browser...")

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(viewport=VIEWPORT)
            page = await context.new_page()
            await page.goto(
                f"http://localhost:{STREAMLIT_PORT}/Benchmark_Attribution",
                wait_until="load",
            )
            print(f"Page loaded. Waiting {SETTLE_DELAY_MS}ms for charts to render...")
            await page.wait_for_timeout(SETTLE_DELAY_MS)
            await page.screenshot(path=str(OUTPUT_PATH), full_page=False)
            await browser.close()

        size_kb = OUTPUT_PATH.stat().st_size // 1024
        print(f"Saved: {OUTPUT_PATH} ({size_kb} KB)")
        if size_kb < 50:
            print("WARNING: file is smaller than 50KB — page may not have rendered fully.")
            print("Increase SETTLE_DELAY_MS and re-run.")

    finally:
        proc.kill()
        proc.wait()


if __name__ == "__main__":
    asyncio.run(capture())
