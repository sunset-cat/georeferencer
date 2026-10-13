"""Read-only browser check against the live representative document library."""
import json
from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe", headless=True)
    page = browser.new_page(viewport={"width":1440,"height":1100})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8000")
    page.wait_for_function("documents.length > 0")
    assert page.evaluate("!documents.some(d=>d.id==='4cd1bfa7218c4f1e93cde7e5e5cc04c9')")
    page.evaluate("openDocument('default-west-mcarthur-drilling', false)")
    page.locator("#geojson-content").wait_for()
    page.wait_for_function("document.getElementById('geojson-content').textContent.length>0")
    assert page.get_by_role("tab", name="GeoJSON", exact=True).get_attribute("aria-selected") == "true"
    displayed = json.loads(page.locator("#geojson-content").inner_text())
    downloaded = page.request.get("http://127.0.0.1:8000/library/default-west-mcarthur-drilling/geojson").json()
    assert displayed == downloaded
    assert page.locator("#entities-panel").is_hidden()
    page.get_by_role("tab", name="GeoJSON", exact=True).focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("#entities-panel").is_visible()
    page.locator(".location-item").filter(has_text="Athabasca Basin").click()
    page.locator(".location-web-section > summary").click()
    detail = page.locator("#location-detail").inner_text()
    assert "verification" in detail.lower()
    page.get_by_role("tab", name="Entities & evidence", exact=True).focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("#raw-panel").is_visible()
    west = page.request.get("http://127.0.0.1:8000/library/default-west-mcarthur-drilling").json()
    if west["result"].get("raw_llm_responses"):
        assert page.locator("#raw-llm-content pre").first.text_content() == west["result"]["raw_llm_responses"][0]["output_text"]
    else:
        assert "Raw LLM output was not saved" in page.locator("#raw-llm-content").inner_text()
    page.get_by_role("tab", name="GeoJSON", exact=True).click()
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate("openDocument('default-bc-drillhole-database', false)")
    page.wait_for_function("selected.id==='default-bc-drillhole-database'")
    assert page.locator("#geojson-panel").is_visible()
    page.get_by_role("tab", name="Raw LLM output", exact=True).click()
    result = page.request.get("http://127.0.0.1:8000/library/default-bc-drillhole-database").json()
    responses = result["result"].get("raw_llm_responses", [])
    if responses:
        assert page.locator("#raw-llm-content pre").first.text_content() == responses[0]["output_text"]
    # Exercise rendering without a paid model call or changing stored results.
    raw = '{ "entities": [{"id":"e1","name":"MapPlace 2","subtype":"facility","role":"context_location"}] }'
    page.evaluate("raw => { selected.result.raw_llm_responses = [{output_text: raw, model:'browser-test', status:'completed'}]; renderRawLLM(); }", raw)
    assert page.locator("#raw-llm-content pre").first.text_content() == raw
    assert "MapPlace 2" in page.locator("#raw-llm-content table").inner_text()
    assert "context_location" in page.locator("#raw-llm-content table").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    browser.close()
print("Browser check passed: financial document exclusion, default GeoJSON, tab navigation, exact export, candidate explanation, mobile layout.")
