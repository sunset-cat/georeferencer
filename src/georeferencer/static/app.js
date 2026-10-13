const $ = id => document.getElementById(id);
let documents = [], selected = null, selectedEntity = null, loadingDocument = 0, resultTab = 'geojson';
const RESULT_TABS = ['geojson', 'entities', 'raw'];
const human = value => (value === 'incidental' ? 'not_relevant' : value || 'unknown').replaceAll('_', ' ');
const el = (tag, text, cls) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; };
const verificationTypes = entity => entity.verification_classes?.length ? entity.verification_classes : ['unknown'];
const verificationTypeLabel = entity => verificationTypes(entity).map(human).join(', ');
const spinner = () => { const node = el('span', undefined, 'spinner'); node.setAttribute('aria-hidden', 'true'); return node; };
const badge = status => { const node = el('span', undefined, `badge ${status}`); if (status === 'processing') node.append(spinner()); node.append(document.createTextNode(human(status))); return node; };
const link = (text, url) => { try { const parsed = new URL(url); if (!['http:', 'https:'].includes(parsed.protocol)) return null; const a = el('a', text + ' ↗', 'source-link'); a.href = parsed.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; } catch { return null; } };
function notice(message, error = false) { $('notice').hidden = false; $('notice').textContent = message; $('notice').className = error ? 'notice error' : 'notice'; }
async function api(path, options = {}) { const response = await fetch(path, options); let data; try { data = await response.json(); } catch { throw new Error(`Server returned HTTP ${response.status}`); } if (!response.ok) throw new Error(data.detail || data.issues?.map(i => i.message).join('; ') || data.errors?.map(e=>e.message).join('; ') || `HTTP ${response.status}`); return data; }
async function refresh() { try { documents = await api('/library'); renderDocuments(); if (selected) await openDocument(selected.id, false); } catch (error) { notice(error.message, true); } }
function renderDocuments() { $('document-count').textContent = documents.length; $('nav-count').textContent = documents.length; $('empty-library').hidden = documents.length > 0; $('documents').replaceChildren(); for (const doc of documents) { const card = el('button', undefined, `document-card ${selected?.id === doc.id ? 'active' : ''}`); card.type = 'button'; card.setAttribute('aria-pressed', selected?.id === doc.id ? 'true' : 'false'); const top = el('div', undefined, 'card-top'); const format = doc.format === 'text' ? 'TXT' : doc.format.toUpperCase(); top.append(el('span', format, `file-icon ${doc.format}`), badge(doc.status)); card.append(top, el('h3', doc.name), el('div', new Date(doc.created_at).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}), 'card-info')); const bottom = el('div', undefined, 'card-bottom'); bottom.append(el('span', `${doc.entity_count} locations`), el('span', doc.status === 'processing' ? 'Processing…' : `${doc.feature_count} resolved`)); card.append(bottom); card.addEventListener('click', () => openDocument(doc.id)); $('documents').append(card); } }
async function openDocument(id, scroll = true) { const serial = ++loadingDocument; try { const doc = await api(`/library/${id}`); if (serial !== loadingDocument) return; const changed = selected?.id !== id; selected = doc; if (changed) { resultTab = 'geojson'; selectedEntity = null; $('events').hidden = true; $('history').replaceChildren(); $('type-filter').value = 'all'; } renderDocuments(); renderExplorer(); if (scroll) $('explorer').scrollIntoView({behavior:'smooth',block:'start'}); } catch(error) { notice(error.message,true); } }
function renderExplorer() { $('explorer').hidden = false; $('selected-title').textContent = selected.name; $('selected-summary').textContent = `${human(selected.status)} · ${selected.entity_count} extracted locations · ${selected.feature_count} with geometry`; renderProcessing(); $('retry-button').disabled = selected.status === 'processing'; $('original-link').href = `/library/${selected.id}/original`; $('geojson-link').href = `/library/${selected.id}/geojson`; $('geojson-link').hidden = !selected.result; $('document-text').textContent = selected.text || 'No extracted text is available yet.'; const usage = selected.result?.usage; $('run-metrics').replaceChildren(); if (usage) { for (const [label,value] of [['Model calls',usage.model_calls],['Input tokens',usage.input_tokens],['Output tokens',usage.output_tokens],['Source calls',usage.source_calls],['Issues',selected.result.issues.length]]) { const metric = el('span'); metric.append(el('strong',Number(value).toLocaleString()),document.createTextNode(label)); $('run-metrics').append(metric); } if (usage.model_calls_without_usage) $('run-metrics').append(el('span','Token totals incomplete: usage unavailable for some calls')); } renderCost();
  const filter = $('type-filter'), previous = filter.value;
  filter.replaceChildren(new Option('All verification types','all'));
  const entities = selected.result?.entities || [];
  for (const type of [...new Set(entities.flatMap(r => verificationTypes(r.entity)))].sort()) filter.add(new Option(human(type), `verify:${type}`));
  if ([...filter.options].some(o=>o.value===previous)) filter.value=previous;
  renderGeoJSON(); renderRawLLM(); setResultTab(resultTab); renderLocations(); renderIssues(); if (selectedEntity) { const resolution = entities.find(r=>r.entity.id === selectedEntity); if (resolution) renderDetail(resolution); } else renderPlaceholder(); }
function renderProcessing() {
  const active = selected.status === 'processing';
  const message = $('processing-message');
  const text = selected.result ? 'A new run is processing. Results and cost below belong to the previous run.' : 'Processing this document. Results and estimated cost will appear automatically.';
  message.hidden = !active;
  $('retry-button').setAttribute('aria-busy', String(active));
  if (active && message.dataset.message !== text) message.replaceChildren(spinner(), document.createTextNode(text));
  if (!active) message.replaceChildren();
  message.dataset.message = active ? text : '';
}
const money = value => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', minimumFractionDigits:2, maximumFractionDigits:4}).format(value);
function renderCost() {
  $('run-metrics').querySelector('.cost-metric')?.remove();
  const cost = selected.result?.estimated_cost;
  const details = $('cost-details');
  details.hidden = !cost;
  $('cost-breakdown').replaceChildren();
  if (!cost) return;
  const metric = el('span', undefined, 'cost-metric');
  const amount = cost.total_usd == null ? 'Unavailable' : `~${money(cost.total_usd)}${cost.status === 'incomplete' ? ' (partial)' : ''}`;
  const label = selected.status === 'processing' ? 'Previous run estimated cost' : 'Estimated run cost';
  metric.append(el('strong', amount), document.createTextNode(label));
  $('run-metrics').append(metric);
  const body = $('cost-breakdown');
  body.append(el('p', `Tokens: ${money(cost.token_cost_usd)} · Web search fees: ${money(cost.web_search_cost_usd)} · Locally reused responses: ${cost.local_cache_hits}`));
  for (const [stage, label] of [['extraction', 'Extraction'], ['web_search', 'Web discovery'], ['web_verification', 'Source verification'], ['geographic_audit', 'Geographic review'], ['geocoder_refinement', 'Geocoder query refinement']]) {
    const calls = (cost.calls || []).filter(call => call.stage === stage);
    const tokens = calls.reduce((sum, call) => sum + (call.token_cost_usd || 0), 0);
    const fees = calls.reduce((sum, call) => sum + (call.web_search_cost_usd || 0), 0);
    body.append(el('p', `${label}: ${money(tokens + fees)} · tokens ${money(tokens)} · search fees ${money(fees)}`));
  }
  body.append(el('p', `USD list prices checked ${cost.pricing_as_of}. Standard processing assumed when no tier was recorded. Taxes, account discounts, hosting and third-party charges are excluded. This is an estimate of this run, not the document's cumulative spend.`, 'detail-subtitle'));
  if (selected.status === 'processing') body.append(el('p', 'These costs belong to the previous run. The current estimate will be available when processing finishes.'));
  if (cost.local_cache_hits) body.append(el('p', 'Local cache replays cost $0 in this run. Provider prompt-cache reads and writes are priced separately.'));
  for (const note of cost.notes || []) body.append(el('p', note, 'cost-warning'));
  for (const [index, call] of (cost.calls || []).entries()) {
    const card = el('div', undefined, 'cost-call');
    card.append(el('strong', `${index + 1}. ${call.model} · ${human(call.stage)}${call.correction ? ' correction' : ''}`));
    card.append(el('p', `Tokens: ${call.token_cost_usd == null ? 'not priced' : money(call.token_cost_usd)} · ${call.web_search_calls} completed search actions priced: ${money(call.web_search_cost_usd)}`));
    if (call.tokens) {
      card.append(el('p', `Tokens — input: ${call.tokens.input.toLocaleString()}, cached input: ${call.tokens.cached_input.toLocaleString()}, cache writes: ${call.tokens.cache_write.toLocaleString()}, output (including reasoning): ${call.tokens.output.toLocaleString()}`));
      const rates = call.rates_per_million_usd;
      card.append(el('p', `USD per million — input: ${rates.input}, cached input: ${rates.cached_input}, cache writes: ${rates.cache_write}, output: ${rates.output}${call.long_context ? ' (long-context rates)' : ''}`));
    }
    body.append(card);
  }
  const source = link('Published model and tool prices', cost.pricing_source);
  if (source) body.append(source);
}
function filteredEntities() { const filter = $('type-filter').value; return (selected.result?.entities || []).filter(r => filter === 'all' || verificationTypes(r.entity).some(type => filter === `verify:${type}`)); }
function renderLocations() { const entities = filteredEntities(); $('location-count').textContent = entities.length; $('location-list').replaceChildren(); if (!entities.length) $('location-list').append(el('div',selected?.status === 'processing' ? 'Waiting for extraction…' : selected?.status === 'ready' ? 'Document ready for review. Choose Analyze document to extract locations.' : 'No locations match this selection. Check run diagnostics if extraction failed.','inline-empty')); for (const r of entities) { const item = el('button',undefined,`location-item ${selectedEntity === r.entity.id ? 'active' : ''}`); item.type='button'; item.setAttribute('aria-pressed',selectedEntity===r.entity.id?'true':'false'); const content = el('div',undefined,'location-item-content'); if(r.flagged) content.append(el('span','Flagged for review','badge flagged')); content.append(el('strong',r.entity.name),el('small',`${verificationTypeLabel(r.entity)} · ${human(r.entity.role)}`)); item.append(el('span','⌖','location-pin'),content,badge(r.status==='unresolved' ? (r.resolution_state||r.status) : r.status)); item.addEventListener('click',()=>{selectedEntity=r.entity.id;renderLocations();renderDetail(r)}); $('location-list').append(item); } }
function renderPlaceholder() { const box=el('div',undefined,'detail-placeholder'); box.append(el('span','⌖'),el('h3','Follow a location’s evidence'),el('p','Select a location to inspect its coordinates, mentions, and verification sources.')); $('location-detail').replaceChildren(box); }
function mentionFailure(mention) {
  const compact = value => (value || '').replace(/\s+/g, ' ');
  const text = compact(selected.text), quote = compact(mention.quote);
  if (!quote) return 'The model returned an empty evidence quote.';
  const positions = []; let offset = text.indexOf(quote);
  while (offset >= 0) { positions.push(offset); offset = text.indexOf(quote, offset + 1); }
  if (!positions.length) return 'The evidence quote does not occur in the document, even after whitespace normalization.';
  const valid = positions.filter(pos => (!mention.prefix || text.slice(0,pos).endsWith(compact(mention.prefix))) && (!mention.suffix || text.slice(pos+quote.length).startsWith(compact(mention.suffix))));
  if (valid.length > 1) return `The quote matches ${valid.length} occurrences; the supplied context does not identify one occurrence.`;
  if (!valid.length) {
    const pos = positions[0], end = pos + quote.length;
    const failures = [];
    if (mention.prefix && !text.slice(0,pos).endsWith(compact(mention.prefix))) failures.push(`Prefix mismatch. Supplied: ${JSON.stringify(mention.prefix)}. Actual preceding text: ${JSON.stringify(text.slice(Math.max(0,pos-compact(mention.prefix).length),pos))}.`);
    if (mention.suffix && !text.slice(end).startsWith(compact(mention.suffix))) failures.push(`Suffix mismatch. Supplied: ${JSON.stringify(mention.suffix)}. Actual following text: ${JSON.stringify(text.slice(end,end+compact(mention.suffix).length))}.`);
    return `The quote exists in the document, but its context is invalid. ${failures.join(' ')}`;
  }
  return 'Evidence has not been accepted in this saved run; run again to validate it.';
}
function renderDetail(r) {
  const panel=$('location-detail'); panel.replaceChildren();
  const title=el('div',undefined,'detail-title'); title.append(el('h3',r.entity.name),badge(r.status==='unresolved' ? (r.resolution_state||r.status) : r.status)); if(r.flagged) title.append(el('span','Flagged for review','badge flagged')); panel.append(title);
  renderGeographicReview(panel,r);
  const metadata=el('dl',undefined,'metadata-grid');
  const coords=r.geometry?.coordinates, provenance=r.geometry_provenance;
  for (const [label,value] of [['Role',human(r.entity.role)],['Type (verification)',verificationTypeLabel(r.entity)],['Coordinates',coords?`${coords[0]}, ${coords[1]} (longitude, latitude)`:'Not resolved']]) {
    const pair=el('div');pair.append(el('dt',label),el('dd',value));metadata.append(pair);
  }
  const source=el('div'), sourceValue=el('dd'); source.append(el('dt','Coordinate source'),sourceValue);
  if (!coords || !provenance) sourceValue.textContent=coords?'Source not recorded':'None - no accepted coordinates';
  else {
    const label=provenance.origin==='document'?'Found in document':provenance.workflow==='geocoder'?`Geocoder${provenance.source?' - '+provenance.source:''}`:provenance.workflow==='web_search'?'Web search':human(provenance.workflow);
    sourceValue.append(el('span',label));
    if(provenance.origin==='document') {const a=el('a','View document','source-link');a.href=`/library/${selected.id}/original`;a.target='_blank';a.rel='noopener';sourceValue.append(el('br'),a);}
    else {const a=link('View source',provenance.source_record_url||provenance.source);if(a)sourceValue.append(el('br'),a);}
  }
  metadata.append(source);panel.append(metadata);
  if (coords && provenance?.crs_status === 'assumed') panel.append(el('p','WGS84 assumed; source datum unspecified.','coordinate-assumption'));
  if (provenance?.crs_status === 'transformed') panel.append(el('p', `Converted from ${provenance.source_crs} to WGS84. Transformation accuracy: ${provenance.transformation?.accuracy_metres ?? 'unknown'} metres.${provenance.transformation?.best_available===false ? ' A more precise grid transformation is unavailable locally.' : ''}`, 'detail-subtitle'));
  const projected=(r.entity.coordinate_claims||[]).filter(c=>c.coordinate_format==='projected');
  if(projected.length){const details=el('details',undefined,'detail-section');details.append(el('summary','Original coordinate evidence'));for(const c of projected){details.append(el('p',`Easting ${c.easting} m ? Northing ${c.northing} m ? ${c.crs}`),el('blockquote',c.evidence.quote,'evidence-quote'));if(c.crs_evidence)details.append(el('blockquote',c.crs_evidence.quote,'evidence-quote'));}panel.append(details);}
  const invalidMentions=(r.entity.mentions||[]).filter(m=>m.start==null);
  if (!coords && (r.reason || r.issues?.length || invalidMentions.length)) {
    const debug=el('div',undefined,'issue location-debug');
    debug.append(el('strong',invalidMentions.length&&!r.verification.length?'Evidence validation failed - verification skipped':'Resolution details'));
    const identitySource=(r.verification||[]).find(check=>['supported','confirmed'].includes(check.identity)&&check.selected_candidate);
    if(identitySource && r.status!=='conflicting'){debug.append(el('p','Entity identity supported by a source; no acceptable coordinates were established.'));const a=link('Identity source',identitySource.selected_candidate.source_record_url);if(a)debug.append(a);}
    else if(r.reason)debug.append(el('p',r.reason));
    for(const mention of invalidMentions)debug.append(el('p',mentionFailure(mention)));
    for(const issue of r.issues||[])debug.append(el('p',`${human(issue.code)}: ${issue.message}`));
    panel.append(debug);
  }
  const mentions=el('details',undefined,'detail-section mentions-section');
  mentions.append(el('summary',`Mentions in document (${r.entity.mentions.length})`));
  const documentChars=Array.from(selected.text||'');
  for(const mention of r.entity.mentions){
    const quote=el('blockquote',undefined,'evidence-quote');
    if(mention.start!=null && mention.end!=null){
      quote.append(document.createTextNode(documentChars.slice(Math.max(0,mention.start-100),mention.start).join('')),el('strong',mention.quote),document.createTextNode(documentChars.slice(mention.end,mention.end+100).join('')));
    }else quote.append(document.createTextNode(mention.quote));
    const button=el('button',mention.start!=null?'Show in document ?':'View document (unanchored)','text-button');
    button.type='button';button.addEventListener('click',()=>highlightMention(mention));quote.append(button);mentions.append(quote);
  }
  panel.append(mentions);
  const web=locationWebSection(r); if(web)panel.append(web);
  const deterministic=locationSourceSection(r); if(deterministic)panel.append(deterministic);
  const raw=el('details',undefined,'detail-section');raw.append(el('summary','All metadata'),el('pre',JSON.stringify(r,null,2)));panel.append(raw); }
function highlightMention(mention){const text=$('document-text');text.replaceChildren();const chars=Array.from(selected.text||'');if(mention.start!=null&&mention.end!=null){text.append(document.createTextNode(chars.slice(0,mention.start).join('')),el('mark',chars.slice(mention.start,mention.end).join('')),document.createTextNode(chars.slice(mention.end).join('')));}else text.textContent=selected.text;$('document-text').closest('details').open=true;text.querySelector('mark')?.scrollIntoView({behavior:'smooth',block:'center'});}
function renderIssues(){updateRunState();const issues=selected.result?.issues||[];$('issue-count').textContent=`${issues.length} issues · ${selected.result?.request_id||'awaiting run'}`;$('issues').replaceChildren();if(!issues.length)$('issues').append(el('p','No issues recorded.'));for(const issue of issues){const box=el('div',undefined,'issue');box.append(el('strong',human(issue.code)),el('p',issue.message),el('small',`${issue.stage}${issue.entity_id?' · '+issue.entity_id:''}${issue.retryable?' · retryable':''}`));$('issues').append(box);}if(selected.status==='failed'||selected.status==='interrupted')$('diagnostics').open=true;if(selected.status==='interrupted')$('issues').prepend(el('p','The server stopped during processing. Run again to recover this document.'));}
async function importDocument(path,options){try{const result=await api(path,options);notice('Document added to your library. Processing has started.');documents=await api('/library');await openDocument(result.id,false);}catch(error){notice(error.message,true);}}
async function upload(file){if(!file)return;if(file.size>10000000){notice('Document must be at most 10 MB.',true);return;}const body=new FormData();body.append('file',file);await importDocument('/library/upload',{method:'POST',body});$('file-input').value='';}
$('file-input').addEventListener('change',e=>upload(e.target.files[0]));$('drop-zone').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('file-input').click();}});for(const name of ['dragenter','dragover'])$('drop-zone').addEventListener(name,e=>{e.preventDefault();$('drop-zone').classList.add('dragging');});for(const name of ['dragleave','drop'])$('drop-zone').addEventListener(name,e=>{e.preventDefault();$('drop-zone').classList.remove('dragging');if(name==='drop')upload(e.dataTransfer.files[0]);});
$('url-form').addEventListener('submit',async e=>{e.preventDefault();$('url-submit').disabled=true;await importDocument('/library/url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('url-input').value})});$('url-submit').disabled=false;});$('demo-button').addEventListener('click',()=>importDocument('/library/demo',{method:'POST'}));$('refresh-button').addEventListener('click',refresh);$('type-filter').addEventListener('change',()=>{if(!filteredEntities().some(r=>r.entity.id===selectedEntity)){selectedEntity=null;renderPlaceholder();}renderLocations();});$('retry-button').addEventListener('click',async()=>{if(!selected)return;try{await api(`/library/${selected.id}/retry`,{method:'POST'});notice('A new run has started. Earlier runs remain in history.');await refresh();}catch(error){notice(error.message,true);}});$('events-button').addEventListener('click',async()=>{if(!selected)return;const id=selected.id;try{const [events,history]=await Promise.all([api(`/library/${id}/events`),api(`/library/${id}/runs`)]);if(selected.id!==id)return;$('events').textContent=JSON.stringify(events,null,2);$('events').hidden=false;$('history').replaceChildren(el('strong',`${history.length} saved runs`));for(const run of history){const details=el('details');details.append(el('summary',`${new Date(run.created_at).toLocaleString()} · ${run.result.status} · ${run.request_id}`),el('pre',JSON.stringify(run.result,null,2)));$('history').append(details);}}catch(error){notice(error.message,true);}});
function updateRunState() {
  const progress=selected.result?.progress || {};
  const limits=selected.result?.limits || {};
  $('run-progress').textContent = selected.result ? `Resolved: ${progress.resolved ?? selected.feature_count} ? Pending: ${progress.pending || 0} ? Unresolved: ${progress.unresolved ?? 0} ? Workflow: ${selected.result.workflow_version || 'older saved result; run again to revalidate'}${limits.estimated_cost_usd!=null ? ` ? Per-run spending threshold: $${limits.estimated_cost_usd}` : ''}` : '';
  $('continue-button').hidden = !selected.result || selected.status==='processing' || !(progress.pending || selected.result.entities?.some(r=>r.status==='unresolved'));
  $('continue-button').disabled = selected.status==='processing';
  $('cached-refresh-button').hidden = !selected.result;
  $('cached-refresh-button').disabled = selected.status==='processing';

  const corpus = selected.corpus_metadata;
  $('corpus-description').hidden = !corpus;
  $('corpus-description').replaceChildren();
  if (corpus) {
    $('corpus-description').append(document.createTextNode(`${human(corpus.kind)} ? ${corpus.publisher} ? `));
    const source = link('Source document', selected.source_url);
    if(source) $('corpus-description').append(source);
    if(corpus.analysis_note) $('corpus-description').append(document.createTextNode(` ? ${corpus.analysis_note}`));
    if(corpus.download_error) $('corpus-description').append(document.createTextNode(` ? Download unavailable: ${corpus.download_error}`));
  }
  $('retry-button').textContent = selected.result ? 'Run again' : 'Analyze document';
  $('retry-button').disabled = selected.status === 'processing' || corpus?.analysis_supported === false;

  $('original-link').hidden = !selected.original_available;
  if (selected.status === 'failed' && selected.result?.issues.length) {
    const issue = selected.result.issues[selected.result.issues.length - 1];
    notice(`This run failed: ${issue.message}`, true);
  }
}
async function checkConfiguration() {
  try {
    const state = await api('/capabilities');
    $('run-policy').textContent=`Per run: ${state.max_web_requests} discovery requests, ${state.max_verification_requests ?? 12} source-verification requests${state.max_run_cost_usd!=null ? `, $${state.max_run_cost_usd} estimated spending threshold` : ''}. Unfinished work can be continued; completed locations are reused.`;
    if (!state.fixture_mode && (!state.model_configured || !state.api_key_configured)) {
      const missing = [!state.model_configured ? 'GEO_MODEL' : null, !state.api_key_configured ? 'OPENAI_API_KEY' : null].filter(Boolean);
      $('configuration-notice').hidden = false;
      $('configuration-notice').textContent = `Live extraction needs ${missing.join(' and ')} in the server environment. Documents can still be stored. The synthetic example works without credentials.`;
    }
  } catch (error) { notice(error.message, true); }
}
checkConfiguration();refresh();setInterval(()=>{if(!document.hidden&&documents.some(d=>d.status==='processing'))refresh();},2000);

function setResultTab(tab) {
  resultTab = tab;
  for (const name of RESULT_TABS) {
    const active = name === tab;
    $(name + '-tab').setAttribute('aria-selected', String(active));
    $(name + '-tab').tabIndex = active ? 0 : -1;
    $(name + '-panel').hidden = !active;
  }
}
function renderGeoJSON() {
  const result = selected.result;
  const count = result?.geojson?.features?.length || 0;
  $('geojson-summary').textContent = result ? `${count} accepted geographic features. Inspect all extracted locations in Entities & evidence.` : 'GeoJSON will appear after document analysis.';
  $('geojson-content').textContent = result ? JSON.stringify(result.geojson, null, 2) : '';
  $('copy-geojson').disabled = !result;
  $('geojson-empty').hidden = !result || count > 0;
  $('geojson-empty').textContent = 'No accepted geometry in this run. Unresolved and excluded entities are available in Entities & evidence.';
}
for (const name of RESULT_TABS) {
  $(name + '-tab').addEventListener('click', () => setResultTab(name));
  $(name + '-tab').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const tabs = RESULT_TABS; const offset = event.key === 'ArrowRight' ? 1 : -1; const tab = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[tabs.length - 1] : tabs[(tabs.indexOf(name) + offset + tabs.length) % tabs.length];
    setResultTab(tab); $(tab + '-tab').focus();
  });
}
$('copy-geojson').addEventListener('click', async () => {
  if (!selected.result) return;
  try { await navigator.clipboard.writeText(JSON.stringify(selected.result.geojson, null, 2)); notice('GeoJSON copied.'); }
  catch { notice('Copy unavailable. Use the GeoJSON download button.', true); }
});

function renderRawLLM() {
  const panel = $('raw-llm-content'); panel.replaceChildren();
  const responses = (selected.result?.raw_llm_responses || []).filter(response => !['web_search', 'web_verification', 'geographic_audit', 'geocoder_refinement'].includes(response.stage));
  if (!responses.length) {
    panel.append(el('p', 'Raw LLM output was not saved for this run. Choose Run again to capture it. Synthetic fixtures do not make LLM queries.', 'inline-empty'));
    return;
  }
  responses.forEach((response, index) => {
    const section = el('details', undefined, 'raw-response');
    section.open = index === 0;
    const labels = [`Response ${index + 1}`, response.stage === 'web_search' ? `web search: ${response.entity_name}` : response.chunk ? `chunk ${response.chunk}` : null, response.stage === 'web_search' ? null : response.correction ? 'correction' : 'initial extraction', response.served_from_cache ? 'cached response' : null].filter(Boolean);
    section.append(el('summary', labels.join(' ? ')));
    section.append(el('p', [response.model, response.reasoning_effort ? `reasoning: ${response.reasoning_effort}` : null, response.prompt_version, response.status, response.provider_response_id].filter(Boolean).join(' ? '), 'detail-subtitle'));
    for (const source of response.sources || []) {
      const a = link(source.title || source.url, source.url);
      if (a) section.append(a, el('br'));
    }
    if (response.chunk_context) {
      const context = el('details');
      context.append(el('summary', 'Locations and geographic context sent to this chunk'), el('pre', JSON.stringify(response.chunk_context, null, 2)));
      section.append(context);
    }
    if (response.tool_calls?.length) {
      const trace = el('details');
      trace.append(el('summary', 'Search actions'), el('pre', JSON.stringify(response.tool_calls, null, 2)));
      section.append(trace);
    }
    let payload;
    try { payload = JSON.parse(response.output_text); } catch { payload = null; }
    if (Array.isArray(payload?.entities)) {
      const scroll = el('div', undefined, 'raw-table-scroll');
      const table = el('table', undefined, 'raw-entity-table');
      const header = el('thead'), row = el('tr');
      for (const label of ['ID', 'Entity', 'Kind', 'Semantic unit', 'Type', 'Role', 'Verification classes']) row.append(el('th', label));
      header.append(row); table.append(header);
      const body = el('tbody');
      for (const entity of payload.entities) {
        if (!entity || typeof entity !== 'object') continue;
        const item = el('tr');
        for (const value of [entity.id, entity.name, entity.entity_kind, entity.semantic_unit ?? (Object.hasOwn(entity,'semantic_unit') ? 'None' : undefined), entity.subtype ?? entity.type, entity.role, Array.isArray(entity.verification_classes) ? entity.verification_classes.join(', ') : undefined]) item.append(el('td', value == null ? 'Not returned' : String(value)));
        body.append(item);
      }
      table.append(body); scroll.append(table); section.append(scroll);
    }
    const raw = el('details', undefined, 'raw-json');
    raw.append(el('summary', 'Exact response text'), el('pre', response.output_text || '(No text returned)'));
    section.append(raw); panel.append(section);
  });
}

$('continue-button').addEventListener('click', async()=>{if(!selected)return;try{await api(`/library/${selected.id}/continue`,{method:'POST'});notice('Continuing unfinished work; completed locations will be reused.');await refresh();}catch(error){notice(error.message,true);}});
$('cached-refresh-button').addEventListener('click', async()=>{if(!selected)return;try{await api(`/library/${selected.id}/revalidate`,{method:'POST'});notice('Checking saved evidence locally; no paid model or web requests.');await refresh();}catch(error){notice(error.message,true);}});

function renderGeographicReview(panel, r) {
  if(r.flagged) {
    const warning=el('div',undefined,'issue review-warning');
    warning.append(el('strong','Flagged for review'),el('p','Coordinates retained. This warning does not establish an error.'));
    for(const flag of r.review_flags || []) warning.append(el('p',flag.reason));
    panel.append(warning);
  }
  const id=r.entity.entity_kind==='semantic_unit'?r.entity.id:r.entity.semantic_unit;
  const extent=(selected.result?.semantic_unit_extents || []).find(e=>e.id===id);
  if(!extent) return;
  const details=el('details',undefined,'detail-section');
  details.append(el('summary',`Geographic review: ${extent.name}`),el('p',`${extent.point_count} located members. ${human(extent.review?.status || 'not_checked')}.`));
  if(extent.bbox) details.append(el('p',`Member extent (west, south, east, north): ${extent.bbox.join(', ')}${extent.crosses_antimeridian?' (crosses antimeridian)':''}`));
  if(extent.review?.summary) details.append(el('p',extent.review.summary));
  for(const finding of extent.review?.findings || []) {
    const record=selected.result.entities.find(e=>e.entity.id===finding.entity_id);
    const button=el('button',record?.entity.name || finding.entity_id,'text-button');button.type='button';
    button.addEventListener('click',()=>{if(record){selectedEntity=record.entity.id;renderLocations();renderDetail(record);}});
    details.append(button,el('p',finding.reason));
  }
  for(const response of selected.result?.raw_llm_responses || []) {
    if(response.stage!=='geographic_audit' || response.semantic_unit!==id) continue;
    const raw=el('details');raw.append(el('summary','Audit input and exact response'),el('pre',JSON.stringify({model:response.model,reasoning_effort:response.reasoning_effort,input:response.audit_input,output:response.output_text,cached:response.served_from_cache},null,2)));details.append(raw);
  }
  panel.append(details);
}
