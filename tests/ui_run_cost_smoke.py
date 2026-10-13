"""Read-only live UI check; processing transitions mocked without paid reruns."""
import json
from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, mutations = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: mutations.append(request.url) if request.method != "GET" else None)
    identifier = "default-west-mcarthur-drilling"
    base = "http://127.0.0.1:8000"
    original = page.request.get(f"{base}/library/{identifier}").json()
    rows = page.request.get(f"{base}/library").json()
    assert not any(row["status"] == "processing" for row in rows), "Wait for actual jobs before smoke check"
    page.goto(base)
    page.wait_for_function("documents.length > 0")
    page.evaluate("id => openDocument(id, false)", identifier)
    page.wait_for_function("selected && selected.id === 'default-west-mcarthur-drilling'")
    cost = original["result"]["estimated_cost"]
    assert page.locator(".cost-metric").is_visible()
    assert "Estimated run cost" in page.locator(".cost-metric").inner_text()
    assert not page.locator("#cost-details").evaluate("node=>node.open")
    page.locator("#cost-details > summary").click()
    assert page.locator("#cost-breakdown .cost-call").count() == len(cost["calls"])
    assert "Locally reused responses" in page.locator("#cost-breakdown").inner_text()
    assert page.locator("#cost-breakdown a").last.get_attribute("href") == cost["pricing_source"]
    assert page.locator("#processing-message").is_hidden()
    assert page.locator("#processing-message .spinner").count() == 0

    # Mock server status so polling exercises the real UI rendering code.
    active = {"value": True}
    def library_reply(route):
        reply = json.loads(json.dumps(rows))
        for row in reply:
            if row["id"] == identifier and active["value"]:
                row["status"] = "processing"
        route.fulfill(json=reply)

    def document_reply(route):
        reply = json.loads(json.dumps(original))
        if active["value"]:
            reply["status"] = "processing"
        route.fulfill(json=reply)

    page.route(f"{base}/library", library_reply)
    page.route(f"{base}/library/{identifier}", document_reply)
    page.evaluate("refresh()")
    page.locator("#processing-message").wait_for(state="visible")
    assert page.locator("#processing-message .spinner").is_visible()
    assert page.locator(".document-card .badge.processing .spinner").count() == 1
    assert page.locator("#processing-message").get_attribute("role") == "status"
    assert "previous run" in page.locator("#processing-message").inner_text()
    assert "Previous run estimated cost" in page.locator(".cost-metric").inner_text()
    assert page.locator("#retry-button").is_disabled()
    assert page.locator("#retry-button").get_attribute("aria-busy") == "true"
    rotating = page.locator("#processing-message .spinner")
    assert rotating.evaluate("node=>getComputedStyle(node).animationName") == "run-spin"
    page.emulate_media(reduced_motion="reduce")
    assert rotating.evaluate("node=>getComputedStyle(node).animationName") == "none"
    page.emulate_media(reduced_motion="no-preference")

    # The scheduled refresh removes spinners automatically when run completes.
    active["value"] = False
    page.wait_for_function("selected.status !== 'processing'", timeout=6000)
    assert page.locator("#processing-message").is_hidden()
    assert page.locator(".spinner").count() == 0
    assert page.locator("#retry-button").is_enabled()
    assert "Previous run" not in page.locator(".cost-metric").inner_text()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    # No saved metadata: show unavailable, not a fabricated zero-cost result.
    page.evaluate("""() => {
        selected.result.estimated_cost = {...selected.result.estimated_cost,
            total_usd:null, status:'unavailable', calls:[], notes:['No saved model usage']};
        renderCost();
    }""")
    assert "Unavailable" in page.locator(".cost-metric").inner_text()
    assert "No saved model usage" in page.locator("#cost-breakdown").text_content()
    assert not errors, errors
    assert not mutations, mutations
    # Reading legacy estimates must not alter the stored run or recorded usage.
    after = page.request.get(f"{base}/library/{identifier}").json()
    assert after["result"] == original["result"]
    browser.close()
print("Browser check passed: saved run cost, per-call prices, free local cache, processing spinner, polling, reduced motion, mobile layout, no paid calls or mutations.")
