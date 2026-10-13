"""Generate a self-contained, offline data-flow diagram from current contracts.

Reads saved results only. Makes no model, geocoder or web calls and does not update
the library. The JSON payload identifies saved versus reconstructed examples.
"""
import argparse
import json
from pathlib import Path
import sqlite3

from georeferencer import models
from georeferencer.extraction import strict_schema, PROMPT, PROMPT_VERSION
from georeferencer.geographic_audit import Audit, INSTRUCTIONS as AUDIT_PROMPT
from georeferencer.geocoder_refinement import Suggestions, PROMPT as QUERY_PROMPT
from georeferencer.routing import uses_geocoder, is_mining_asset
from georeferencer.web_resolution import INSTRUCTIONS, VERIFICATION_INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[1]


def shape(properties, required=None):
    return {'type':'object','properties':properties,'required':required or list(properties),
            'description':'Dictionary contract; not an additional Pydantic model.'}


def obj(name, fields, example, schema=None, note=''):
    return {'name':name,'fields':fields,'example':example,'schema':schema,'note':note}


def schema_object(name, model, example, note=''):
    schema=model.model_json_schema()
    fields=list(schema['properties'])
    if isinstance(example,list):
        definitions=schema.pop('$defs',{})
        schema={'type':'array','items':schema,'$defs':definitions}
    return obj(name,fields,example,schema,note)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document',default='default-monte-do-carmo-serra-alta')
    parser.add_argument('--result',type=Path,help='Saved RunResult JSON instead of SQLite')
    args=parser.parse_args()
    if args.result:
        result=json.loads(args.result.read_text(encoding='utf-8'));document={'name':'Saved example','text':''}
    else:
        path=(ROOT/'data/library/library.sqlite3').resolve()
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT name,text,source_url,content_type,length(content) AS bytes,result FROM documents WHERE id=?',(args.document,)).fetchone()
            if row is None or not row['result']:parser.error('A saved document result is required.')
            document={'name':row['name'],'text':row['text'],'source_url':row['source_url'],
                      'content_type':row['content_type'],'bytes':row['bytes']};result=json.loads(row['result'])
    validated=models.RunResult.model_validate(result)
    records={r.entity.id:r for r in validated.entities}
    raws=result.get('raw_llm_responses',[])
    extracted=[r for r in raws if r.get('stage') in {None,'extraction'} and r.get('chunk')]
    discovery=next(r for r in raws if r.get('stage')=='web_search' and r.get('project_name')=='Monte do Carmo Project')
    verification=next(r for r in raws if r.get('stage')=='web_verification' and r.get('project_name')=='Monte do Carmo Project')
    coordinate_response=next((r for r in raws if r.get('stage')=='web_verification' and any(c.get('coordinate_format')=='dms' for c in json.loads(r['output_text']).get('candidates',[]))),verification)
    verification=coordinate_response
    audit_raw=next(r for r in raws if r.get('stage')=='geographic_audit')
    refinement=next((r for r in raws if r.get('stage')=='geocoder_refinement'),None)
    place=next(r for r in validated.entities if r.entity.name=='Tocantins State')
    hole=next(r for r in validated.entities if r.entity.name=='FSA-221')
    deposit=next(r for r in validated.entities if r.entity.name=='Serra Alta deposit')
    unit=next(r for r in validated.entities if r.entity.id==hole.entity.semantic_unit)
    raw_extract=json.loads(extracted[0]['output_text'])
    later_context=extracted[1]['chunk_context'] if len(extracted)>1 else {'existing_locations':[],'geographic_contexts':[]}
    part=extracted[0]['chunk_range'];part_text=document['text'][part[0]:part[1]]
    geocheck=next(v for v in place.verification if v.workflow=='geocoder')
    geocandidate=geocheck.selected_candidate
    claims=json.loads(coordinate_response['output_text'])
    external=next(v for v in deposit.verification if v.workflow=='web_search')
    extent=next(e for e in result['semantic_unit_extents'] if e['id']==unit.entity.id)
    group_request={'project_id':discovery['project_id'],
        'project':{'name':unit.entity.name,'aliases':unit.entity.aliases,'country':unit.entity.country_code,
                   'jurisdiction':unit.entity.jurisdiction,'passages':[m.model_dump() for m in unit.entity.mentions[:2]]},
        'entities':discovery['discovery_entities'],'requested_entity_ids':discovery['requested_entity_ids']}
    # Project.passages in the real adapter contains quote/prefix/suffix only.
    group_request['project']['passages']=[{k:m[k] for k in ['quote','prefix','suffix']} for m in group_request['project']['passages']]
    v_request={'project_id':verification['project_id'],'project':group_request['project'],
        'entities':[{'entity_id':identifier,'name':records[identifier].entity.name,
            'aliases':records[identifier].entity.aliases,'country':records[identifier].entity.country_code,
            'jurisdiction':records[identifier].entity.jurisdiction,
            'passages':[{k:m.model_dump()[k] for k in ['quote','prefix','suffix']} for m in records[identifier].entity.mentions[:5]]}
            for identifier in verification['entity_ids']], 'sources':verification['input_sources']}

    stages=[]
    def step(identifier,title,kind,purpose,inputs,outputs,metadata=None,notes='',prompt=None,branch='Main path'):
        stages.append(dict(id=identifier,title=title,kind=kind,purpose=purpose,inputs=inputs,outputs=outputs,
            metadata=metadata or {},notes=notes,prompt=prompt,branch=branch))

    step('read','Read and split the document','python','Extract canonical text and prepare sequential chunks.',
        [obj('Original document',['name','content','content_type','source_url'],{'name':document['name'],
             'content':f"<{document.get('bytes','unknown number of')} original bytes stored in library>",
             'content_type':document.get('content_type'),'source_url':document.get('source_url')},
             note='Saved library metadata. Original binary content is represented by its byte count, not embedded in the diagram.')],
        [schema_object('Document',models.Document,{'name':document['name'],'text':document['text'][:600]},'Text excerpt; the runtime Document contains the full text.'),
         obj('Chunk tuple + extraction context',['start','end','text','RunContext.chunk_index','RunContext.chunk_range'],
             {'chunk_tuple':[part[0],part[1],part_text[:500]],'RunContext.chunk_index':extracted[0]['chunk'],'RunContext.chunk_range':part},
             note='Runtime chunks are (start, end, text) tuples, not a Chunk Pydantic model. Text is shortened in this illustration.')],
        {'chunk_size_characters':14000,'overlap_characters':350,'processing_order':'sequential'},
        'The library uses chunking; the direct API and ordinary CLI have different input paths.')
    step('extract','Identify entities in one chunk','llm','Interpret physical places, relevance, type, group membership and coordinate claims.',
        [obj('Sol request body',['text','validation_feedback','previous_extraction','chunk_context'],
             {'text':part_text[:500],'validation_feedback':[],'previous_extraction':None,'chunk_context':extracted[0]['chunk_context']},
             note='Reconstructed current request shape; text excerpt. Earlier entities are context, not new evidence.')],
        [obj('Raw LLM Extraction',['entities'],raw_extract,strict_schema(),
             'Saved raw model response. Mentions are strings. category is not requested; Python derives it from subtype. relationships and legacy project_ids are not requested.')],
        {k:extracted[0].get(k) for k in ['model','reasoning_effort','prompt_version','chunk','chunk_range','correction','provider_response_id','usage']},
        'The live JSON schema requires entity_kind and semantic_unit from initial recognition. A location may have coordinate_claims: [].',PROMPT)
    step('anchor','Validate, anchor and accumulate entities','python','Resolve mention expressions to text spans, derive category, preserve IDs and merge compatible updates.',
        [obj('Raw LLM Extraction',['entities'],raw_extract,strict_schema())],
        [schema_object('Canonical Entity',models.Entity,hole.entity.model_dump(mode='json'),
             'Saved canonical example. This hole also contains a coordinate claim recovered from the input-table shortcut; it is not a verbatim copy of the LLM output.'),
         schema_object('Mention',models.Mention,hole.entity.mentions[0].model_dump(mode='json')),
         schema_object('CoordinateClaim',models.CoordinateClaim,hole.entity.coordinate_claims[0].model_dump(mode='json'))],
        {'id_example':hole.entity.id,'offset_units':'Python Unicode characters','document_version':'Mention.document_sha256'},
        'Python assigns category from subtype; stable IDs are namespaced across chunks. Unsupported evidence is quarantined; schema-level failures allow bounded correction.')
    step('context','Pass forward context to the next chunk','python','Supply known entities and scoped geographic contexts without reusing old mentions as new evidence.',
        [obj('Accumulated entities',['entities[]'],[unit.entity.model_dump(mode='json'),hole.entity.model_dump(mode='json')])],
        [obj('chunk_context',['existing_locations','geographic_contexts'],later_context,
             shape({'existing_locations':{'type':'array'},'geographic_contexts':{'type':'array'}}),
             'Saved chunk-2 context. existing_locations carries compact metadata, not full previous coordinate claims. geographic_contexts carries evidence and applies_to IDs.')],
        {'loop':'context -> next Sol request -> validate/merge -> updated context'},
        'No final LLM reconciliation pass. Multiple geographic contexts are scoped with applies_to; a shared country does not establish group membership.')
    decisions=[{'id':r.entity.id,'name':r.entity.name,'entity_kind':r.entity.entity_kind,
        'semantic_unit':r.entity.semantic_unit,'role':r.entity.role,'category':r.entity.category.value,
        'subtype':r.entity.subtype,'verification_classes':[v.value for v in r.entity.verification_classes],
        'has_coordinate_claims':bool(r.entity.coordinate_claims),'uses_geocoder':uses_geocoder(r.entity),
        'is_mining_asset':is_mining_asset(r.entity)} for r in [unit,hole,place,deposit]]
    step('route','Classify and route each location','python','Use independent structure, role and verification dimensions to choose source workflows.',
        [obj('Entity[]',['entity_kind','semantic_unit','role','category','subtype','verification_classes','country_code','jurisdiction','coordinate_claims'],
            [r.entity.model_dump(mode='json') for r in [unit,hole,place,deposit]])],
        [obj('Routing decisions (derived view)',list(decisions[0]),decisions,note='Explanation of predicates evaluated in Python; this is not a serialized RoutingDecision model.')],
        {'role_policy':'primary_subject / subject_component; library also includes context_location',
         'excluded_roles':['corporate_contact','not_relevant','unknown'],
         'structural_display_names':{'general_geographic':'general geographic context','semantic_unit':'spatial group','subaltern_location':'component location'}},
        'Accepted input claims avoid discovery. Geographic places can use a geocoder. Mining metadata routes to shared source verification. Physical subtype still participates in identity checks.')
    step('geocoder','Query Photon and normalize its records','api','Retrieve concrete provider records; the model does not supply coordinates.',
        [obj('Photon query parameters',['q','countrycode','limit','lang'],{'q':'Tocantins','countrycode':'BR','limit':20,'lang':'en'},
             note='Successful query shape. The original Tocantins State query failed; cleanup preserves the entity name and BR constraint.')],
        [schema_object('Candidate[]',models.Candidate,[geocandidate.model_dump(mode='json')],
             'Saved normalized accepted Photon record. Photon returns features[].properties and geometry; the source adapter produces Candidate objects.')],
        {'provider':'photon','operation':'GET /api/','network_parameters_are_chosen_by':'Python'},branch='Geographic-place route')
    step('geocheck','Check geocoder identity and ambiguity','python','Check type, country, normalized jurisdiction and original name/identifier; keep all candidates for inspection.',
        [schema_object('Entity',models.Entity,place.entity.model_dump(mode='json')),
         schema_object('Candidate[]',models.Candidate,[c.model_dump(mode='json') for c in geocheck.candidates])],
        [schema_object('VerificationResult',models.VerificationResult,geocheck.model_dump(mode='json'))],
        {'unique_qualifying_record':'selected','multiple_qualifying_records':'conflicting; geometry withheld','no_match':'refine query or use source discovery'},
        'Model suggestions cannot override identity filters. Distinct source records remain ambiguous even if a later query retrieves only one.',branch='Geographic-place route')
    step('queries','Suggest alternative geocoder queries','llm','Batch unsuccessful or ambiguous places and propose search strings only.',
        [obj('Query refinement request',['semantic_group','entities[].entity_id','entities[].name','entities[].aliases','entities[].type','entities[].country_code','entities[].jurisdiction','entities[].mentions','entities[].previous_queries','entities[].status','entities[].records'],
             refinement['refinement_input'] if refinement else {},note='Saved targeted diagnostic request, deliberately testing the original failed query.')],
        [schema_object('Suggestions',Suggestions,json.loads(refinement['output_text']) if refinement else {'queries':[]})],
        {'model':refinement.get('model') if refinement else 'configured discovery model','reasoning_effort':'low','tools':[],
         'max_new_queries_per_entity':3,'next_step':'Python executes deduplicated queries; same original filters'},
        'The ordinary Tocantins cleanup needed no LLM. This saved response belongs to the explicit Luna fallback test.',QUERY_PROMPT,branch='Geocoder fallback')
    step('discovery','Discover shared documents for a spatial group','llm','Find sources for unresolved group members; reuse input and cached coordinate evidence.',
        [obj('Shared discovery request',['project_id','project','entities','requested_entity_ids'],group_request,
             note='Reconstructed project context with saved discovery_entities/requested_entity_ids. project_id/project are legacy names for any spatial subject, not only mining projects.')],
        [obj('Discovery JSON',['documents[].url','documents[].title','documents[].entity_ids','candidates','note'],json.loads(discovery['output_text'])),
         obj('Provider tool trace / sources',['tool_calls','sources'],{'tool_calls':discovery['tool_calls'],'sources':discovery['sources']},
             note='These are provider trace metadata, separate from the model-selected documents and candidate JSON.')],
        {**{k:discovery.get(k) for k in ['model','reasoning_effort','prompt_version','entity_ids']},'tools':['web_search'],'max_tool_calls':2,'max_proposed_documents':4},
        'One logical discovery per spatial group; all members are supplied. Standalone geographic failures can also form discovery groups. Source verification calls are not new discoveries.',INSTRUCTIONS,branch='Shared source route')
    step('passages','Retrieve sources and select passages','python','Download shared source snapshots, check proposed URLs, and select bounded text/header/CRS passages.',
        [obj('Proposed documents + allowed provider URLs',['documents','sources'],{'documents':json.loads(discovery['output_text']).get('documents',[]),'sources':discovery['sources']})],
        [obj('Verification sources[]',['url','source_sha256','passages'],verification['input_sources'],
             note='Saved passages actually supplied to source verification. The source snapshot holds complete downloaded text; these objects contain selected excerpts only.')],
        {'selection':'lexical name/coordinate/CRS windows','reused_across':'group members','no_LLM_in_this_step':True},
        'The current selection can omit a relevant table/page/map. Complete report text is downloaded, not sent wholesale to the model.',branch='Shared source route')
    step('verify','Interpret source passages and tables','llm','Return values attributed to exact entity IDs, with coordinate/header/CRS evidence.',
        [obj('Sol verification request',['project_id','project','entities','sources'],v_request,
             note='Reconstructed entity context + saved input_sources. Entity contexts may contain later accumulated mentions; this is not an exact historical request replay.')],
        [obj('LLM coordinate candidates',['entity_id','name','url','identity_quote','coordinate_format','longitude','latitude','latitude_dms','longitude_dms','easting','northing','elevation','units','zone','hemisphere','crs','coordinate_quotes','header_quotes','crs_quotes','meaning'],claims,
             note='Saved source-verification response containing the deposit DMS example, from the same batch as the saved input_sources above.')],
        {**{k:coordinate_response.get(k) for k in ['model','reasoning_effort','prompt_version','entity_ids']},'tools':[],'max_entities_per_batch':6},
        'Decimal copies numbers; DMS retains latitude_dms/longitude_dms; projected retains easting/northing/zone/hemisphere. Python converts, not the model.',VERIFICATION_INSTRUCTIONS,branch='Shared source route')
    step('coordinates','Validate values and convert coordinates','python','Anchor source quotations, check copied numbers/row assignment, establish CRS scope and transform supported values.',
        [obj('Source candidate JSON',['candidates','coordinate_quotes','header_quotes','crs_quotes'],claims),
         schema_object('Input CoordinateClaim',models.CoordinateClaim,hole.entity.coordinate_claims[0].model_dump(mode='json'))],
        [schema_object('Normalized Candidate',models.Candidate,external.selected_candidate.model_dump(mode='json')),
         schema_object('VerificationResult',models.VerificationResult,external.model_dump(mode='json'))],
        {'output_coordinate_order':'longitude, latitude','geographic_output_crs':'WGS84','projected_conversion':'pyproj / PROJ',
         'evidence_storage':'Candidate.evidence values are strings; some contain serialized JSON'},
        'Quoted text existing in a source is necessary but does not prove semantic interpretation. Unstated geographic datum uses a labelled assumption; missing projected CRS is not guessed.',branch='All resolution routes')
    step('resolution','Select acceptable geometry and provenance','python','Combine workflow evidence, detect material conflicts, and preserve unresolved/excluded states.',
        [schema_object('VerificationResult[]',models.VerificationResult,[v.model_dump(mode='json') for v in deposit.verification])],
        [schema_object('Resolution',models.Resolution,deposit.model_dump(mode='json')),
         schema_object('GeometryProvenance',models.GeometryProvenance,deposit.geometry_provenance.model_dump(mode='json'))],
        {'independently_confirmed':'separate from document/external origin','conflicting_geometry':'withheld, not averaged'},
        'Candidate is a source proposal; VerificationResult is a workflow outcome; Resolution is the final per-entity outcome.')
    step('extent','Compute the spatial-group extent','python','Use only resolved component locations linked by semantic_unit.',
        [obj('Resolution[]',['entity.entity_kind','entity.semantic_unit','status','geometry'],[r.model_dump(mode='json') for r in validated.entities])],
        [obj('semantic_unit_extents[]',['id','name','member_ids','located_member_ids','point_count','bbox','crosses_antimeridian','extreme_entity_ids','review'],[extent])],
        {'bbox_order':'west, south, east, north','included_kind':'subaltern_location','excluded':'general context; unresolved/excluded points; anchor representative point'},
        'The output example is the saved extent after review. Before the audit, review is not yet populated. This is an observed member extent, not a legal/project boundary.')
    step('audit','Review the group extent','llm','Inspect unit context, bbox and supplied extreme members; flag concrete concerns only.',
        [obj('Geographic audit request',['semantic_unit','bbox','crosses_antimeridian','point_count','extreme_entity_ids','extreme_member_count','extreme_members_sampled','members'],audit_raw['audit_input'],
             note='Saved exact audit input; contains extreme members, not all interior members.')],
        [schema_object('Audit',Audit,json.loads(audit_raw['output_text']))],
        {**{k:audit_raw.get(k) for k in ['model','reasoning_effort','prompt_version']},'tools':[],'max_extreme_members':16},
        'Warnings add Resolution.flagged/review_flags; they do not delete coordinates or assert independent confirmation.',AUDIT_PROMPT)
    step('output','Publish the complete result','python','Keep GeoJSON geometry, full entity outcomes, evidence, review and cost data together.',
        [obj('Resolutions + extents',['entities','semantic_unit_extents'],{'entities':[deposit.model_dump(mode='json'),place.model_dump(mode='json')],'semantic_unit_extents':result['semantic_unit_extents']})],
        [schema_object('RunResult',models.RunResult,result),
         obj('GeoJSON Feature',['type','id','geometry','properties'],next(f for f in result['geojson']['features'] if f['id']==deposit.entity.id),
             note='Saved GeoJSON feature: properties carries classification, evidence, provenance and review metadata.')],
        {'saved_request_id':result['request_id'],'workflow_version':result.get('workflow_version'),'current_progress':result.get('progress')},
        'All saved fields are inspectable here. Legacy relationships/project_ids remain readable but are not requested from current extraction.')
    step('plugins','Extend with another deterministic source adapter','api','Register a suitable source workflow by type and jurisdiction; retain common acceptance rules.',
        [schema_object('Entity',models.Entity,hole.entity.model_dump(mode='json'))],
        [obj('Future adapter contract',['Candidate[]','VerificationResult'],{'return_types':['Candidate[]','VerificationResult'],'example_tools':['permit / claim API','drill-collar API','national gazetteer / survey service']},
             note='Proposed interfaces; no live permit/drilling/survey API is implemented. Existing LocalRegistry is a Python lookup over normalized exports.')],
        {'implemented':False,'routing_basis':['verification_classes','country_code','jurisdiction','identifiers'],'preserved':'same evidence / geometry acceptance contract'},
        'Future tools do not require redesigning entity recognition or the final output contract.',branch='Future extension')

    entity_schema=models.Entity.model_json_schema()
    dimensions=[]
    explanations={'entity_kind':'Spatial structure: context, group anchor or linked component.',
        'semantic_unit':'Parent group ID for a component; null for a group or general context.',
        'role':'Document relevance; separate from spatial structure.',
        'verification_classes':'Potential source families; multiple known classes may coexist; unknown stands alone.',
        'category':'Derived broad physical family, not requested in live extraction.',
        'subtype':'Physical subtype used in compatibility checks; separate from verification family.'}
    for field,description in explanations.items():
        dimensions.append({'field':field,'description':description,'schema':entity_schema['properties'][field],
            'values':models.VerificationClass._value2member_map_.keys() if field=='verification_classes' else
                list(models.TYPOLOGY) if field=='category' else entity_schema['properties'][field].get('enum'),
            'examples':[{'name':r.entity.name,'value':r.entity.model_dump(mode='json')[field]} for r in [unit,hole,place,deposit]]})
    for dimension in dimensions:
        if dimension['values'] is not None:dimension['values']=list(dimension['values'])
    schemas={name:getattr(models,name).model_json_schema() for name in ['Document','Mention','Identifier','CoordinateClaim','Entity','Extraction','Point','Candidate','Issue','VerificationResult','GeometryProvenance','Resolution','Usage','CostEstimate','RunResult']}
    schemas.update(RawLLMExtraction=strict_schema(),Suggestions=Suggestions.model_json_schema(),Audit=Audit.model_json_schema())
    payload={'title':'Georeferencer: objects and agent workflow','document_name':document['name'],
        'saved_request_id':result['request_id'],'example_policy':'Saved examples are labelled. Reconstructed request shapes and derived explanatory objects are labelled separately. No new analyses were run.',
        'dimensions':dimensions,'typology':{str(k):sorted(v) for k,v in models.TYPOLOGY.items()},'schemas':schemas,'stages':stages}
    destination=ROOT/'docs/diagrams'
    destination.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(payload,ensure_ascii=False,indent=2)
    (destination/'agent-dataflow.json').write_text(encoded,encoding='utf-8')
    template=(ROOT/'scripts/dataflow-template.html').read_text(encoding='utf-8')
    html=template.replace('__DATA__',encoded.replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026'))
    (destination/'agent-dataflow.html').write_text(html,encoding='utf-8')
    print('Generated docs/diagrams/agent-dataflow.html and agent-dataflow.json; no model or network calls.')


if __name__=='__main__':main()
