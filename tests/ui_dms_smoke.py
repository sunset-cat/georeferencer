"""Read-only UI check using ephemeral evidence; never rerun paid extraction."""
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, mutations = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: mutations.append(request.url) if request.method != "GET" else None)
    base = "http://127.0.0.1:8000"
    original = page.request.get(base + "/library/default-monte-do-carmo-serra-alta").json()
    page.goto(base)
    page.wait_for_function("documents.length > 0")
    page.evaluate("openDocument('default-monte-do-carmo-serra-alta', false)")
    page.wait_for_function("selected?.id === 'default-monte-do-carmo-serra-alta'")
    page.get_by_role("tab", name="Entities & evidence", exact=True).click()
    page.evaluate("""() => {
        const record = selected.result.entities.find(r => r.entity.name === 'Serra Alta deposit');
        selectedEntity = record.entity.id;
        const quote = `Serra Alta deposit is located at 10°45'4"S and 48°4'20"W.`;
        const evidence = {coordinate_format:'dms', coordinate_quote:quote,
            original_latitude:`10°45'4"S`, original_longitude:`48°4'20"W`,
            converted_longitude:'-48.07222222222222', converted_latitude:'-10.751111111111111',
            conversion:'Python: degrees + minutes/60 + seconds/3600; south/west negative. Datum unchanged.'};
        const candidate = {name:'Serra Alta deposit', source_record_url:'https://survey.example/report', evidence};
        record.verification = [{workflow:'web_search', status:'supported', identity:'supported',
            geometry:{coordinates:[-48.07222222222222,-10.751111111111111]}, selected_candidate:candidate},
            {workflow:'geocoder', status:'no_match', identity:'unverified', candidates:[]}];
        record.issues = [];
        selected.result.raw_llm_responses = [{stage:'web_search', entity_id:record.entity.id,
            model:'browser-test', status:'completed', sources:[], output_text:JSON.stringify({candidates:[{
                name:'Serra Alta deposit', url:candidate.source_record_url, coordinate_quote:quote,
                latitude:null, longitude:null, crs:'WGS84', meaning:'representative_point'}]})}];
        const returned = {name:'Serra Alta', category:'general_geographic', subtype:'settlement',
            source:'photon', source_record_id:'1', country_code:'BR', jurisdiction:'Santa Catarina'};
        const req = {event:'source.request', timestamp:'1', request_id:selected.result.request_id,
            entity_id:record.entity.id, workflow:'geocoder', source:'photon', attempt:2,
            query:'Serra Alta deposit, Tocantins', refined:true, query_reason:'No candidate passed the first query; append jurisdiction once'};
        const res = {...req, event:'source.response', timestamp:'2', candidates:[returned], candidate_count:1,
            candidate_assessments:[{source:'photon', source_record_id:'1', passes_filters:false,
                reasons:['Jurisdiction differs: expected Tocantins; returned Santa Catarina','Type differs: mineralized_zone versus settlement']}]};
        sourceEventCache.set(`${selected.id}:${selected.result.request_id}`, {loading:false, events:[req,res]});
        renderDetail(record);
    }""")
    page.locator(".location-web-section > summary").click()
    web = page.locator(".location-web-section").inner_text()
    assert "Original DMS:" in web
    assert "Python-converted decimal degrees: -48.07222222222222" in web
    assert "Datum unchanged" in web
    assert "Coordinate quotation returned" in web
    page.locator(".location-sources-section > summary").click()
    page.wait_for_function("document.querySelector('.source-attempt') !== null")
    page.locator(".source-records > summary").click()
    sources = page.locator(".location-sources-section").inner_text()
    assert "Query: Serra Alta deposit, Tocantins" in sources
    assert "append jurisdiction once" in sources
    assert "Rejected by deterministic identity filters" in sources
    assert "Jurisdiction differs: expected Tocantins" in sources
    assert "Type differs: mineralized_zone versus settlement" in sources
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    assert not mutations, mutations
    assert page.request.get(base + "/library/default-monte-do-carmo-serra-alta").json()["result"] == original["result"]
    browser.close()
print("Browser check passed: original DMS, converted coordinates, actual refined query and candidate rejection reasons; no paid calls or stored changes.")
