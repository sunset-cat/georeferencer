"""Read-only check for entity-scoped, conditional, collapsible verification sections."""
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, mutations = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: mutations.append(request.url) if request.method != "GET" else None)
    page.goto("http://127.0.0.1:8000")
    page.wait_for_function("documents.length > 0")
    page.evaluate("openDocument('default-west-mcarthur-drilling', false)")
    page.wait_for_function("selected?.id==='default-west-mcarthur-drilling' && selected.result")
    before = page.evaluate("selected.result.request_id")
    assert page.get_by_role("tab").count() == 3
    page.get_by_role("tab", name="Entities & evidence", exact=True).click()
    page.locator(".location-item").filter(has_text="Athabasca Basin").click()
    panel = page.locator("#location-detail")
    web_response = page.evaluate("selected.result.raw_llm_responses.find(r=>r.stage==='web_search' && r.entity_id===selectedEntity)")
    assert panel.locator(".mentions-section").count() == 1
    assert panel.locator(".location-web-section").count() == 1
    assert panel.locator(".location-sources-section").count() == 1
    assert panel.locator("details[open]").count() == 0
    assert not page.evaluate("sourceEventCache.size")
    panel.locator(".location-web-section > summary").click()
    assert panel.locator(".web-response").count() == 1
    assert "App verification:" in panel.inner_text()
    assert web_response["output_text"] in panel.locator(".location-web-section pre").all_text_contents()
    assert "Pike Zone" not in panel.locator(".location-web-section .search-candidate").all_text_contents()
    panel.locator(".mentions-section > summary").click()
    assert panel.locator(".mentions-section .evidence-quote").first.is_visible()
    assert panel.locator(".location-web-section").get_attribute("open") is not None
    panel.locator(".location-sources-section > summary").click()
    page.wait_for_function("sourceEventCache.get(`${selected.id}:${selected.result.request_id}`)?.loading === false")
    source_text = panel.locator(".location-sources-section").inner_text()
    assert "Query sent: Athabasca Basin" in source_text or "Recorded entity name: Athabasca Basin" in source_text
    if "Recorded entity name:" in source_text:
        assert "actual query text was not captured" in source_text
    assert panel.locator(".location-web-section").get_attribute("open") is not None
    page.locator(".location-item").filter(has=page.get_by_text("Saskatchewan", exact=True)).click()
    assert panel.locator(".location-web-section").count() == 0
    assert panel.locator(".location-sources-section").count() == 1
    assert panel.locator("details[open]").count() == 0
    panel.locator(".location-sources-section > summary").click()
    page.wait_for_function("sourceEventCache.get(`${selected.id}:${selected.result.request_id}`)?.loading === false")
    page.wait_for_function("document.querySelector('#location-detail .location-sources-section')?.textContent.includes(': Saskatchewan')")
    source_text = panel.locator(".location-sources-section").inner_text()
    assert "photon" in source_text and ("Query sent: Saskatchewan" in source_text or "Recorded entity name: Saskatchewan" in source_text)
    assert "Query sent: Canada" not in source_text and "Recorded entity name: Canada" not in source_text
    assert "cache hit" in source_text or "source queried" in source_text
    assert panel.locator(".source-records").count() >= 1
    page.locator(".location-item").filter(has=page.get_by_text("Vancouver", exact=True)).click()
    assert panel.locator(".location-web-section, .location-sources-section").count() == 0
    assert "role policy" in panel.inner_text()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # Verify safe model text and exact output without changing stored documents.
    raw = json.dumps({"note": "<script>window.unsafeExecuted=true</script>", "candidates": [{
        "name": "<img src=x onerror=window.unsafeExecuted=true>", "url": "javascript:alert(1)",
        "longitude": None, "latitude": None, "identity_quote": "Quoted source text"}]})
    page.evaluate("raw => { selected.result.raw_llm_responses=[{stage:'web_search',entity_id:selectedEntity,output_text:raw}]; renderDetail(selected.result.entities.find(r=>r.entity.id===selectedEntity)); }", raw)
    panel.locator(".location-web-section > summary").click()
    assert panel.locator(".search-candidate a, img, script").count() == 0
    assert not page.evaluate("!!window.unsafeExecuted")
    assert panel.locator(".location-web-section pre").last.text_content() == raw
    page.evaluate("selected.result.raw_llm_responses=[]; renderDetail(selected.result.entities.find(r=>r.entity.id===selectedEntity))")
    assert panel.locator(".location-web-section").count() == 0
    after = page.request.get("http://127.0.0.1:8000/library/default-west-mcarthur-drilling").json()
    assert after["result"]["request_id"] == before
    assert not mutations, mutations
    assert not errors, errors
    browser.close()
print("Browser check passed: location-scoped collapsible sections, conditional search results, lazy query logs, safe output, mobile layout; no analysis triggered.")
