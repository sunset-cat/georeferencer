"""Read-only browser check for concise fields and explicit evidence diagnostics."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe", headless=True)
    page = browser.new_page(viewport={"width":1440,"height":1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:8000")
    page.wait_for_function("documents.length > 0")
    page.evaluate("openDocument('default-apuradita-channel-sampling', false)")
    page.wait_for_function("selected?.id === 'default-apuradita-channel-sampling' && selected.result")
    page.get_by_role("tab", name="Entities & evidence", exact=True).click()
    page.locator(".location-item").filter(has_text="Oruro").click()
    assert page.locator("#location-detail dt").all_text_contents() == ["Role", "Type (verification)", "Coordinates", "Coordinate source"]
    detail = page.locator("#location-detail").inner_text()
    oruro = page.evaluate("selected.result.entities.find(r=>r.entity.name==='Oruro')")
    assert page.locator("#location-detail .evidence-quote").count() == len(oruro["entity"]["mentions"])
    assert not page.locator("#location-detail .evidence-quote").first.is_visible()
    page.locator("#location-detail .mentions-section > summary").click()
    assert page.locator("#location-detail .evidence-quote").first.is_visible()
    page.locator("#location-detail .evidence-quote button").first.click()
    if oruro["entity"]["mentions"][0]["start"] is not None:
        assert page.locator("#document-text mark").inner_text() == oruro["entity"]["mentions"][0]["quote"]
    if oruro["entity"]["role"] == "not_relevant":
        assert "not relevant" in detail
        assert oruro["status"] == "excluded" and not oruro["verification"]
        assert all(m["start"] is not None for m in oruro["entity"]["mentions"])
    else:
        assert "verification skipped" in detail
        assert "Prefix mismatch" in detail and "Suffix mismatch" in detail
        assert "Actual preceding text" in detail
    assert not page.locator("#location-detail details").filter(has=page.get_by_text("All metadata", exact=True)).get_attribute("open")
    page.select_option("#type-filter", "verify:geographic_place")
    assert page.locator(".location-item").filter(has_text="Oruro").count() == 1
    page.evaluate("openDocument('default-west-mcarthur-drilling', false)")
    page.wait_for_function("selected?.id === 'default-west-mcarthur-drilling'")
    page.get_by_role("tab", name="Entities & evidence", exact=True).click()
    geocoded = page.evaluate("selected.result.entities.find(r=>r.geometry_provenance?.workflow==='geocoder').entity.name")
    page.locator(".location-item").filter(has=page.get_by_text(geocoded, exact=True)).click()
    assert "Geocoder" in page.locator("#location-detail").inner_text()
    assert page.locator("#location-detail .metadata-grid a").count() == 1
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    browser.close()
print("Browser check passed: four fields, explicit context errors, verification filter, coordinate source link, mobile layout.")
