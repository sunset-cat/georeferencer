// Read-only views of saved model output and verification logs.
const sourceEventCache = new Map();

function readModelJSON(text) {
  try { return JSON.parse((text || '').trim().replace(/^```(?:json)?\s*|\s*```$/g, '')); }
  catch { return null; }
}

function appendIssueMessages(panel, issues) {
  for (const issue of issues || []) {
    const box = el('div', undefined, 'issue');
    box.append(el('strong', human(issue.code)), el('p', issue.message));
    if (issue.retryable) box.append(el('small', 'Retryable'));
    panel.append(box);
  }
}

function locationWebSection(record) {
  const result = selected.result;
  const responses = (result?.raw_llm_responses || []).filter(response => ['web_search', 'web_verification'].includes(response.stage) && (response.entity_id === record.entity.id || response.entity_ids?.includes(record.entity.id)));
  const checks = (record.verification || []).filter(check => check.workflow === 'web_search');
  const issues = (record.issues || []).filter(issue => issue.stage === 'web_search');
  if (!responses.length && !checks.length && !issues.length) return null;
  const panel = el('details', undefined, 'detail-section location-web-section');
  panel.append(el('summary', 'Web search results'));
  panel.append(el('p', 'LLM-proposed candidates and source evidence. Proposed coordinates may not have been accepted.', 'detail-subtitle'));
  if (!responses.length) {
    panel.append(el('p', 'No raw web-search LLM response was saved for this location.', 'detail-subtitle'));
  }
  for (const response of responses) {
    const section = el('div', undefined, 'raw-response web-response');
    const verifying = response.stage === 'web_verification';
    section.append(el('h4', verifying ? 'Source verification · downloaded passages' : 'Source discovery · web search'));
    if (response.served_from_cache) section.append(el('p', 'Cached response', 'detail-subtitle'));
    section.append(el('p', [response.model, response.reasoning_effort ? `reasoning: ${response.reasoning_effort}` : null, response.status].filter(Boolean).join(' · '), 'detail-subtitle'));
    if (response.project_id) section.append(el('p', `Shared discovery group: ${response.project_name || response.project_id} · ${response.entity_ids?.length || 0} candidate locations`, 'detail-subtitle'));
    if (response.discovery_entities?.length) {
      const members=el('details'); members.append(el('summary', 'Locations included in shared discovery'));
      for (const entity of response.discovery_entities) members.append(el('p', `${entity.name} (${entity.entity_id})`));
      section.append(members);
    }
    const payload = readModelJSON(response.output_text);
    if (typeof payload?.note === 'string') section.append(el('p', payload.note, 'search-note'));
    if (Array.isArray(payload?.documents) && payload.documents.length) {
      const docs=el('details'); docs.append(el('summary', `Documents proposed for the group (${payload.documents.length})`));
      for (const document of payload.documents) {
        const a=link(document.title || document.url,document.url); if (a) docs.append(a,el('br'));
        if (document.entity_ids?.length) docs.append(el('p', `Locations: ${document.entity_ids.join(', ')}`));
      }
      section.append(docs);
    }
    const candidates = Array.isArray(payload?.candidates) ? payload.candidates.filter(c=>!response.entity_ids || c.entity_id===record.entity.id) : [];
    section.append(el('h4', `LLM returned candidates (${candidates.length})`, 'verification-label'));
    for (const candidate of candidates) {
      if (!candidate || typeof candidate !== 'object') continue;
      const card = el('div', undefined, 'verification-card search-candidate');
      card.append(el('strong', candidate.name || response.entity_name || 'Candidate'));
      const a = link('Proposed source', candidate.url); if (a) card.append(el('br'), a);
      const coordinates = candidate.coordinate_format === 'projected' && typeof candidate.easting === 'number' && typeof candidate.northing === 'number'
        ? `Easting ${candidate.easting}, Northing ${candidate.northing}${candidate.elevation!=null ? `, elevation ${candidate.elevation}` : ''} · ${candidate.crs || 'CRS not returned'}`
        : candidate.coordinate_format === 'dms' && candidate.latitude_dms && candidate.longitude_dms
        ? `${candidate.latitude_dms}, ${candidate.longitude_dms} · ${candidate.crs || 'CRS not returned'}`
        : typeof candidate.longitude === 'number' && typeof candidate.latitude === 'number'
        ? `${candidate.longitude}, ${candidate.latitude} (longitude, latitude) · ${candidate.crs || 'CRS not returned'}` : 'None returned';
      card.append(el('p', `LLM proposed coordinates: ${coordinates}`));
      if (candidate.coordinate_quote && candidate.longitude == null && candidate.latitude == null) card.append(el('p', 'Coordinate quotation returned. Python checks the original coordinate evidence and converts supported coordinate systems after source retrieval.'));
      if (!candidate.coordinate_quote && !candidate.coordinate_quotes?.length) card.append(el('p', 'No coordinate quotation returned. Coordinate statements in the LLM note have not been verified.'));
      if (candidate.coordinate_format) card.append(el('p', `Coordinate type: ${human(candidate.coordinate_format)}`));
      if (candidate.meaning) card.append(el('p', `Geometry meaning: ${human(candidate.meaning)}`));
      if (candidate.identity_quote) card.append(el('blockquote', candidate.identity_quote, 'evidence-quote'));
      if (candidate.coordinate_quote) card.append(el('blockquote', candidate.coordinate_quote, 'evidence-quote'));
      for (const [field,label] of [['coordinate_quotes','Coordinate evidence'],['header_quotes','Header evidence'],['crs_quotes','Coordinate-system evidence']]) {
        if (!Array.isArray(candidate[field]) || !candidate[field].length) continue;
        const evidence=el('details'); evidence.append(el('summary',label));
        for (const quote of candidate[field]) evidence.append(el('blockquote',quote,'evidence-quote'));
        card.append(evidence);
      }
      section.append(card);
    }
    const sources = el('details', undefined, 'raw-json');
    sources.append(el('summary', `Sources returned by search (${response.sources?.length || 0})`));
    for (const source of response.sources || []) {
      const a = link(source.title || source.url, source.url); if (a) sources.append(a, el('br'));
    }
    section.append(sources);
    if (response.input_sources?.length) {
      const inputs = el('details', undefined, 'raw-json');
      inputs.append(el('summary', 'Source passages checked by verifier'));
      for (const source of response.input_sources) {
        const a = link('Downloaded source', source.url); if (a) inputs.append(a);
        for (const passage of source.passages || []) inputs.append(el('blockquote', passage, 'evidence-quote'));
      }
      section.append(inputs);
    }
    if (response.tool_calls?.length) {
      const actions = el('details', undefined, 'raw-json');
      actions.append(el('summary', 'Search actions'), el('pre', JSON.stringify(response.tool_calls, null, 2)));
      section.append(actions);
    }
    const raw = el('details', undefined, 'raw-json');
    raw.append(el('summary', 'Exact LLM response'), el('pre', response.output_text || '(No text returned)'));
    section.append(raw); panel.append(section);
  }
  for (const check of checks) {
    panel.append(el('p', `App verification: ${human(check.status)} · identity ${human(check.identity)} · ${check.geometry ? 'coordinates supported' : 'no accepted coordinates'}`, 'verification-outcome'));
    if (check.selected_candidate) {
      const candidate = check.selected_candidate;
      const a = link('Selected source record', candidate.source_record_url); if (a) panel.append(a);
      const evidence = candidate.evidence || {};
      if (evidence.coordinate_format === 'dms') {
        panel.append(el('p', `Original DMS: ${evidence.original_latitude} · ${evidence.original_longitude}`));
        panel.append(el('p', `Python-converted decimal degrees: ${evidence.converted_longitude}, ${evidence.converted_latitude} (longitude, latitude)`));
        panel.append(el('p', evidence.conversion, 'detail-subtitle'));
      }
      if (evidence.coordinate_validation) panel.append(el('p', evidence.coordinate_validation, 'detail-subtitle'));
      if (!responses.length) for (const [key, value] of Object.entries(candidate.evidence || {})) {
        if (key.endsWith('_quote')) panel.append(el('blockquote', value, 'evidence-quote'));
      }
    }
  }
  appendIssueMessages(panel, issues);
  return panel;
}

function sourceEventsForSelectedRun() {
  const id = selected.id, requestId = selected.result?.request_id;
  const key = `${id}:${requestId}`;
  if (!sourceEventCache.has(key)) {
    const state = {loading: true, events: []}; sourceEventCache.set(key, state);
    state.promise = api(`/library/${id}/events`).then(events => {
      state.events = events.filter(event => event.request_id === requestId);
    }).catch(error => { state.error = error.message; }).finally(() => {
      state.loading = false;
    });
  }
  return sourceEventCache.get(key);
}

function appendSourceRecords(panel, candidates, selectedCandidate, assessments = []) {
  const records = el('details', undefined, 'source-records');
  records.append(el('summary', `Returned records (${candidates.length})`));
  for (const candidate of candidates) {
    const row = el('div', undefined, 'source-record');
    const accepted = selectedCandidate && candidate.source === selectedCandidate.source && candidate.source_record_id === selectedCandidate.source_record_id;
    row.append(el('strong', `${candidate.name}${accepted ? ' · selected record' : ''}`));
    row.append(el('p', `Returned type: ${human(candidate.category)} / ${human(candidate.subtype)}`));
    row.append(el('p', [candidate.source, candidate.source_record_id, candidate.country_code, candidate.jurisdiction].filter(Boolean).join(' · ')));
    const assessment = assessments.find(item => item.source === candidate.source && item.source_record_id === candidate.source_record_id);
    if (assessment) {
      row.append(el('p', assessment.passes_filters ? 'Passes deterministic identity filters' : 'Rejected by deterministic identity filters'));
      for (const reason of assessment.reasons || []) row.append(el('p', reason, 'cost-warning'));
    }
    const a = link('Source record', candidate.source_record_url); if (a) row.append(a);
    if (candidate.geometry) row.append(el('p', `Returned geometry: ${JSON.stringify(candidate.geometry.coordinates)} · ${human(candidate.geometry_basis)}`));
    records.append(row);
  }
  panel.append(records);
}

function locationSourceSection(record) {
  const checks = (record.verification || []).filter(check => check.workflow !== 'web_search');
  if (!checks.length) return null;
  const section = el('details', undefined, 'detail-section location-sources-section');
  section.append(el('summary', `Deterministic checks (${checks.length})`));
  const body = el('div'); section.append(body);
  const id = selected.id, requestId = selected.result.request_id;
  section.addEventListener('toggle', () => {
    if (!section.open || !body.isConnected || selected?.id !== id || selected.result?.request_id !== requestId || selectedEntity !== record.entity.id) return;
    const state = sourceEventsForSelectedRun();
    appendDeterministicChecks(body, record, state);
    if (state.loading) state.promise.then(() => {
      if (body.isConnected && selected?.id === id && selected.result?.request_id === requestId && selectedEntity === record.entity.id) appendDeterministicChecks(body, record, state);
    });
  });
  return section;
}

function appendDeterministicChecks(panel, record, state) {
  panel.replaceChildren();
  const checks = (record.verification || []).filter(check => check.workflow !== 'web_search');
  for(const response of selected.result?.raw_llm_responses || []) {
    if(response.stage!=='geocoder_refinement' || !response.entity_ids?.includes(record.entity.id)) continue;
    const details=el('details');details.append(el('summary','LLM geocoder query suggestions'));
    details.append(el('p',`${response.model} - ${response.status}${response.served_from_cache?' - cached':''}`));
    const payload=readModelJSON(response.output_text);
    for(const suggestion of payload?.queries || []) if(suggestion.entity_id===record.entity.id) details.append(el('p',`${suggestion.query}: ${suggestion.reason}`));
    const raw=el('details');raw.append(el('summary','Input and exact response'),el('pre',JSON.stringify({input:response.refinement_input,output:response.output_text},null,2)));details.append(raw);panel.append(details);
  }
  if (state.loading) panel.append(el('p', 'Loading saved query attempts…', 'detail-subtitle'));
  else if (state.error) panel.append(el('p', `Attempt logs could not be loaded: ${state.error}. Saved verification results are shown below.`, 'issue'));
  else if (!state.events.length) panel.append(el('p', 'Attempt logs are unavailable for this saved run. Saved verification results are shown below.', 'detail-subtitle'));
    for (const check of checks) {
      const card = el('div', undefined, 'verification-card deterministic-check');
      const label = check.workflow === 'explicit_coordinates' ? 'Document coordinates' : human(check.workflow);
      const top = el('div', undefined, 'top'); top.append(el('strong', label), badge(check.status)); card.append(top);
      card.append(el('p', `Identity: ${human(check.identity)} · ${check.geometry ? 'coordinates supported' : 'no accepted coordinates'}`));
      if (check.geometry) card.append(el('p', `Coordinates: ${JSON.stringify(check.geometry.coordinates)}`));
      if (check.workflow === 'geocoder') card.append(el('p', 'Deterministic matching: type, country and normalized jurisdiction, then identifier or exact name/alias. One qualifying record is selected; several qualifying records are ambiguous. New runs query the region and country first; if none qualifies, they retry with country only.'));
      const events = state.events.filter(event => event.entity_id === record.entity.id && event.workflow === check.workflow);
      const requests = events.filter(event => event.event === 'source.request');
      requests.forEach((request, index) => {
        const response = events.find(event => event.event === 'source.response' && event.timestamp >= request.timestamp && (!requests[index + 1] || event.timestamp < requests[index + 1].timestamp));
        const query = el('div', undefined, 'source-attempt');
        query.append(el('strong', `Attempt ${index + 1} · ${request.source || label}`));
        const actualQueryRecorded = typeof request.refined === 'boolean';
        query.append(el('p', `${actualQueryRecorded ? 'Query sent' : 'Recorded entity name'}: ${request.query || record.entity.name} · country: ${request.country_code || 'unspecified'} · jurisdiction: ${request.jurisdiction || 'unspecified'}`));
        if (!actualQueryRecorded) query.append(el('p', 'Older trace: the actual query text was not captured. The jurisdiction refinement may differ from the entity name shown. Run again to record the queries sent.', 'detail-subtitle'));
        if (request.query_reason) query.append(el('p', request.query_reason));
        if (response) {
          query.append(el('p', `${response.candidate_count} returned records · ${response.cache_hit ? 'cache hit' : 'source queried'}${response.latency_ms != null ? ` · ${response.latency_ms} ms` : ''}`));
          appendSourceRecords(query, response.candidates || [], check.selected_candidate, response.candidate_assessments || []);
        } else query.append(el('p', 'No successful response was recorded for this attempt.'));
        card.append(query);
      });
      if (!requests.length && check.workflow !== 'explicit_coordinates') {
        card.append(el('p', state.loading ? 'Query details are loading.' : 'Individual query attempts were not recorded in the available logs.'));
        appendSourceRecords(card, check.candidates || [], check.selected_candidate, check.candidate_assessments || []);
      }
      if (check.workflow === 'explicit_coordinates') {
        card.append(el('p', `${record.entity.coordinate_claims?.length || 0} coordinate claims from the document; no external query.`));
        for (const claim of record.entity.coordinate_claims || []) if (claim.coordinate_format === 'dms') {
          card.append(el('blockquote', claim.evidence.quote, 'evidence-quote'));
          card.append(el('p', `Python-converted DMS: ${claim.longitude}, ${claim.latitude} (longitude, latitude) · source CRS: ${claim.crs}`));
        }
      }
      appendIssueMessages(card, check.issues); panel.append(card);
    }
}
