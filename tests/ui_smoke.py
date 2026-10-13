"""Manual browser smoke test; run against a locally running server."""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--browser", default="C:/Program Files/Google/Chrome/Application/chrome.exe")
    args = parser.parse_args()
    output = Path("output")
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base_url)
        page.get_by_role("button", name="Try a synthetic example").click()
        page.wait_for_function("document.getElementById('location-count').textContent === '4'")
        page.get_by_role("tab", name="Entities & evidence").click()
        page.locator(".location-item").filter(has_text="Example Ridge").click()
        assert "document" in page.locator("#location-detail").inner_text()
        page.get_by_role("button", name="Show in document").first.click()
        assert "Example Ridge" in page.locator("mark").inner_text()
        page.locator("#type-filter").select_option("type:permit")
        assert page.locator(".location-item").count() == 1
        page.locator(".location-item").click()
        assert "external" in page.locator("#location-detail").inner_text()
        page.locator("#diagnostics > summary").click()
        page.get_by_role("button", name="View structured events").click()
        page.locator("#events").wait_for(state="visible")
        assert "source.request" in page.locator("#events").inner_text()
        page.get_by_role("button", name="Run again").click()
        page.wait_for_function("!document.getElementById('retry-button').disabled")
        page.locator("#type-filter").select_option("all")
        page.locator(".location-item").filter(has_text="Example Ridge").click()
        page.locator("#diagnostics").evaluate("node => node.open = false")
        page.locator(".document-text").evaluate("node => node.open = false")
        page.screenshot(path=str(output / "library-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "library-mobile.png"), full_page=True)
        page.locator("#file-input").set_input_files({"name": "broken.bin", "mimeType": "application/octet-stream", "buffer": b"\x00\xff"})
        page.wait_for_function("document.getElementById('selected-title').textContent === 'broken.bin' && document.getElementById('selected-summary').textContent.includes('failed')")
        assert "document import error" in page.locator("#issues").inner_text()
        assert not errors, errors
        browser.close()
    print("Browser smoke passed: demo, filtering, metadata, evidence, events, retry, mobile layout, failed upload.")


if __name__ == "__main__":
    main()
