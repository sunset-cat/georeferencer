"""Read-only browser smoke check using synthetic shared-discovery metadata."""
from playwright.sync_api import sync_playwright


with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
    page=browser.new_page()
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8000')
    page.wait_for_function('documents.length>0')
    identifier=page.evaluate('documents[0].id')
    page.evaluate('(id)=>openDocument(id,false)',identifier)
    page.wait_for_function('(id)=>selected?.id===id',arg=identifier)
    page.evaluate('''() => {
      const record=selected.result.entities[0];
      const ids=[record.entity.id,'other-hole'];
      selected.result.raw_llm_responses=[{stage:'web_search',model:'browser fixture',status:'completed',
        entity_ids:ids,project_id:'project-alpha',project_name:'Alpha project',
        discovery_entities:[{entity_id:record.entity.id,name:record.entity.name},{entity_id:'other-hole',name:'DD-002'}],
        output_text:JSON.stringify({documents:[{url:'https://operator.example/report.pdf',title:'Shared technical report',entity_ids:ids}],candidates:[]})}];
      const section=locationWebSection(record);section.open=true;
      document.getElementById('location-detail').replaceChildren(section);setResultTab('entities');
    }''')
    panel=page.locator('#location-detail')
    assert 'Shared discovery group: Alpha project' in panel.inner_text()
    panel.get_by_text('Locations included in shared discovery',exact=True).click()
    assert 'DD-002' in panel.inner_text()
    panel.get_by_text('Documents proposed for the group (1)',exact=True).click()
    assert panel.get_by_role('link',name='Shared technical report',exact=False).count()==1
    assert not errors,errors
    browser.close()
print('Shared project, discovery membership and ranked documents displayed; no JavaScript errors or stored-data writes.')
