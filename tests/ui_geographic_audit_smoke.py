"""Read-only UI check; synthetic warnings exist only in browser memory."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
    page=browser.new_page()
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8000')
    page.wait_for_function('documents.length>0')
    page.evaluate("openDocument('default-west-mcarthur-drilling',false)")
    page.wait_for_function("selected?.id==='default-west-mcarthur-drilling'")
    page.evaluate('''() => {
      const r=selected.result.entities.find(r=>r.geometry);
      r.flagged=true;r.review_flags=[{reason:'Suspicious distance from project',semantic_unit:'fixture-unit'}];
      r.entity.entity_kind='subaltern_location';r.entity.semantic_unit='fixture-unit';
      selected.result.semantic_unit_extents=[{id:'fixture-unit',name:'Fixture project',point_count:2,bbox:[-110,50,-90,60],review:{status:'suspicious',summary:'Review the eastern point',findings:[{entity_id:r.entity.id,reason:'Suspicious distance from project'}]}}];
      selected.result.raw_llm_responses.push({stage:'geographic_audit',semantic_unit:'fixture-unit',model:'mock Sol',audit_input:{bbox:[-110,50,-90,60]},output_text:'{"status":"suspicious"}'});
      selectedEntity=r.entity.id;renderLocations();renderDetail(r);setResultTab('entities');
    }''')
    assert 'Flagged for review' in page.locator('#location-list').inner_text()
    panel=page.locator('#location-detail')
    assert 'Suspicious distance from project' in panel.inner_text()
    panel.get_by_text('Geographic review: Fixture project',exact=True).click()
    assert 'Review the eastern point' in panel.inner_text()
    panel.get_by_text('Audit input and exact response',exact=True).click()
    assert 'mock Sol' in panel.inner_text()
    assert 'Coordinates retained' in panel.inner_text()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors,errors
    browser.close()
print('Flagged tag, reason, group review, raw audit and mobile layout passed; no stored writes or model calls.')
