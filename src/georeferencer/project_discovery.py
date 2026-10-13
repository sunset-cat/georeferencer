"""Shared discovery for semantic spatial units: projects, regions or geological subjects."""
import asyncio
import hashlib
from urllib.parse import urlsplit,urlunsplit

from .models import VerificationResult
from .observability import BudgetExceeded,diagnostic
from .routing import is_mining_asset
from .workflows import normalize_jurisdiction


def url_key(url):
    parts=urlsplit(url)
    # Compare the PDF spelling, but always fetch the provider-returned URL.
    path=parts.path[:-1] if parts.path.lower().endswith('.pdf/') else parts.path
    return urlunsplit((parts.scheme.lower(),parts.netloc.lower(),path,parts.query,''))


def compatible_context(entity,project):
    # Explicit regional/ geological scope can span countries. A mining site's
    # country/region still constrains its associated components.
    if not is_mining_asset(project):return True
    return not (entity.country_code and project.country_code and entity.country_code!=project.country_code or
        entity.jurisdiction and project.jurisdiction and
        normalize_jurisdiction(entity.jurisdiction)!=normalize_jurisdiction(project.jurisdiction))


def build_groups(all_entities,eligible,ctx):
    all_by_id={e.id:e for e in all_entities}
    projects={e.id:e for e in all_entities if e.role in {'primary_subject','subject_component','context_location'} and
        e.entity_kind=='semantic_unit'}
    groups={}
    memberships={}
    for entity in eligible:
        references=[]
        parent_ids=([entity.semantic_unit] if entity.semantic_unit else []) if entity.entity_kind=='subaltern_location' else entity.project_ids
        if entity.entity_kind=='subaltern_location' and not parent_ids:
            ctx.error('missing_semantic_unit','Subaltern location has no semantic-unit reference; no parent was inferred','extraction',entity.id)
        if entity.entity_kind!='subaltern_location' and entity.semantic_unit:
            ctx.error('invalid_semantic_unit_reference','Only subaltern locations can have a semantic-unit parent','extraction',entity.id)
        for identifier in dict.fromkeys(parent_ids):
            project=projects.get(identifier)
            if project is None or not compatible_context(entity,project):
                ctx.error('invalid_project_reference',f'Spatial group reference {identifier} is missing, not a relevant spatial subject, or has conflicting geography','extraction',entity.id)
                continue
            references.append(identifier)
        if not references and entity.id in projects:
            references=[entity.id]
        if not references:references=[entity.id]
        memberships[entity.id]=references
        for identifier in references:
            group=groups.setdefault(identifier,{'project':all_by_id[identifier],'entities':[]})
            group['entities'].append(entity)
    return groups,memberships


class ProjectDiscovery:
    def __init__(self,workflow,all_entities,eligible,ctx):
        self.workflow=workflow
        self.groups,self.memberships=build_groups(all_entities,eligible,ctx)
        self.results={}
        self.attempted=set()
        ctx.event('web.discovery_groups',groups=[{'project_id':identifier,'project_name':g['project'].name,
            'entity_ids':[e.id for e in g['entities']]} for identifier,g in self.groups.items()])

    async def prepare_cached(self,entities,ctx):
        from .source_passages import cached_sources
        allowed={e.id for e in entities if not e.coordinate_claims}
        for identifier,group in self.groups.items():
            members=[e for e in group['entities'] if e.id in allowed and e.id not in ctx.shared_verification]
            sources={}
            for entity in members:
                for source in await asyncio.to_thread(cached_sources,self.workflow.settings,entity):
                    sources.setdefault(source['source_sha256'],source)
            if not sources:continue
            checks=await self.confirm(members,list(sources.values()),[],ctx,identifier)
            ctx.shared_verification.update(checks)

    async def verify(self,entity,ctx):
        for identifier in self.memberships[entity.id]:
            if identifier not in self.attempted:
                await self.discover(identifier,ctx)
        checks=[self.results[identifier][entity.id] for identifier in self.memberships[entity.id]]
        return self.workflow.result([c for check in checks for c in check.candidates],
            [i for check in checks for i in check.issues],[s for check in checks for s in check.sources])

    async def discover(self,identifier,ctx):
        from .web_resolution import response_payload
        self.attempted.add(identifier)
        group=self.groups[identifier]
        project,members=group['project'],group['entities']
        outcomes={}
        needed=[]
        for entity in members:
            shared=ctx.shared_verification.get(entity.id)
            if shared and (shared.geometry is not None or any(c.evidence.get('coordinate_format')=='projected' for c in shared.candidates)):
                outcomes[entity.id]=shared
            elif entity.id in ctx.completed_entity_ids:
                outcomes[entity.id]=VerificationResult(workflow=self.workflow.name,status='no_match')
            else:needed.append(entity)
        self.results[identifier]=outcomes
        if not needed:
            ctx.event('web.project_search_skipped',project_id=identifier,reason='All group members already have accepted or checked coordinate evidence')
            return
        request={'project_id':identifier,'project':self.workflow.request(project),
            'entities':[{'entity_id':e.id,'role':e.role,'subtype':e.subtype,
                'already_resolved':e.id not in {n.id for n in needed},**self.workflow.request(e),
                'passages':self.workflow.request(e)['passages'][:2]} for e in members],
            'requested_entity_ids':[e.id for e in needed]}
        ctx.event('web.project_discovery_started',project_id=identifier,entity_ids=[e.id for e in members],
            requested_entity_ids=request['requested_entity_ids'])
        try:
            if self.workflow.settings.verification_model and ctx.usage.verification_requests>=self.workflow.settings.max_verification_requests:
                raise BudgetExceeded('Source verification request budget exhausted')
            raw=await self.workflow.model_response(project,ctx,request)
            payload=response_payload(raw)
            documents=payload.get('documents') or []
            if not isinstance(documents,list):raise ValueError('Project discovery documents must be a list')
            proposals=payload['candidates']
            provider={url_key(s['url']):s['url'] for s in raw['sources']}
            requested=list(dict.fromkeys(d['url'] for d in documents if isinstance(d,dict) and isinstance(d.get('url'),str)))
            requested.extend(c.get('url') for c in proposals if isinstance(c,dict) and c.get('url') not in requested)
            urls=[]
            issues=[]
            for url in requested:
                if not isinstance(url,str) or url_key(url) not in provider:
                    for entity in needed:issues.append(ctx.error('web_evidence_rejected',f'Proposed document URL was not returned by search: {url}','web_search',entity.id))
                    continue
                actual=provider[url_key(url)]
                if actual not in urls:urls.append(actual)
            # Ranked document proposals are bounded independently of entity count.
            urls=urls[:4]
            texts=[]
            seen=set()
            for url in urls:
                try:
                    source=await self.download(url,ctx,identifier)
                    if source['source_sha256'] not in seen:
                        texts.append(source);seen.add(source['source_sha256'])
                except BudgetExceeded:raise
                except Exception as exc:
                    code=getattr(exc,'source_issue_code','web_source_download_failed')
                    for entity in needed:issues.append(ctx.error(code,f'{url}: {diagnostic(exc)}','web_search',entity.id))
            checks=await self.confirm(needed,texts,proposals,ctx,identifier) if texts else {}
            for entity in needed:
                old=ctx.shared_verification.get(entity.id)
                check=checks.get(entity.id)
                candidates=[*(old.candidates if old else []),*(check.candidates if check else [])]
                entity_issues=[i for i in issues if i.entity_id==entity.id]+(check.issues if check else [])
                outcomes[entity.id]=self.workflow.result(candidates,entity_issues,raw['sources'])
        except Exception as exc:
            code=('verification_budget_exhausted' if 'verification' in str(exc).lower() else 'web_budget_exhausted') if isinstance(exc,BudgetExceeded) else 'web_project_discovery_failed'
            for entity in needed:
                previous=outcomes.get(entity.id) or ctx.shared_verification.get(entity.id)
                issue=ctx.error(code,diagnostic(exc),'web_search',entity.id,retryable=not isinstance(exc,BudgetExceeded))
                outcomes[entity.id]=self.workflow.result(previous.candidates if previous else [],
                    [*(previous.issues if previous else []),issue],previous.sources if previous else [])
        ctx.event('web.project_discovery_completed',project_id=identifier,entity_ids=list(outcomes))

    async def download(self,url,ctx,identifier):
        import httpx
        from .download import download_document,DownloadConnectionError
        from .ingestion import ingest
        settings=self.workflow.settings
        for attempt in range(2):
            ctx.event('web.source_request',project_id=identifier,url=url,attempt=attempt+1)
            try:
                document=await self.workflow.documents.get(url,settings.max_source_download_bytes,
                    settings.timeout_seconds,downloader=download_document,before_download=lambda:ctx.consume('download'))
                break
            except (httpx.TransportError,DownloadConnectionError,TimeoutError):
                if attempt:raise
        try:
            (body,_,fmt),text_hit=await self.workflow.documents.parse(document,settings.max_source_pdf_pages)
            text=ingest(document.final_url,body,fmt).text
        except Exception as exc:
            exc.source_issue_code='web_source_parse_failed'
            raise
        ctx.event('web.source_response',project_id=identifier,url=url,bytes=len(document.data),
            characters=len(text),cache_hit=document.cache_hit,text_cache_hit=text_hit)
        return {'url':url,'text':text,'source_sha256':hashlib.sha256(text.encode()).hexdigest()}

    async def confirm(self,entities,sources,proposals,ctx,identifier):
        from .source_passages import entity_passages
        from .web_resolution import response_payload,validate_candidate
        results={}
        texts={s['url']:s['text'] for s in sources}
        for start in range(0,len(entities),6):
            batch=entities[start:start+6]
            if self.workflow.settings.verification_model:
                excerpts=[]
                for source in sources:
                    # Give every requested entity a share of the excerpt budget.
                    passages=[]
                    for entity in batch:
                        passages.extend(entity_passages(entity,source['text'],limit=12000//len(batch)))
                    if passages:excerpts.append({'url':source['url'],'source_sha256':source['source_sha256'],'passages':list(dict.fromkeys(passages))})
                if not excerpts:
                    for entity in batch:results[entity.id]=self.workflow.result([],[],[])
                    continue
                request={'project_id':identifier,'entities':[{'entity_id':e.id,**self.workflow.request(e)} for e in batch],
                    'project':self.workflow.request(self.groups[identifier]['project']),
                    'sources':excerpts}
                try:
                    raw=await self.workflow.model_response(batch[0],ctx,request,verification=True)
                    claims=response_payload(raw)['candidates']
                except BudgetExceeded as exc:
                    for entity in entities[start:]:
                        issue=ctx.error('verification_budget_exhausted',diagnostic(exc),'web_search',entity.id)
                        results[entity.id]=self.workflow.result([], [issue], [{'url':s['url']} for s in sources])
                    return results
                except Exception as exc:
                    for entity in batch:
                        issue=ctx.error('web_verification_failed',diagnostic(exc),'web_search',entity.id,retryable=True)
                        results[entity.id]=self.workflow.result([], [issue], [{'url':s['url']} for s in sources])
                    continue
            else:claims=proposals
            batch_ids={e.id for e in batch}
            invalid=[c for c in claims if not isinstance(c,dict) or
                not (isinstance(c.get('entity_id'),str) and c['entity_id'] in batch_ids or
                     len(batch)==1 and c.get('entity_id') is None)]
            valid=[c for c in claims if c not in invalid]
            for entity in batch:
                candidates,issues=[],[]
                if invalid:issues.append(ctx.error('web_evidence_rejected',
                    'Verification response included malformed candidates or unknown entity IDs; those records were ignored',
                    'web_search',entity.id))
                matching=[c for c in valid if c.get('entity_id')==entity.id or len(batch)==1 and not c.get('entity_id')]
                for claim in matching[:2]:
                    try:
                        actual=next((url for url in texts if url_key(url)==url_key(claim['url'])),None)
                        if actual is None:raise ValueError('Verification candidate URL was not supplied')
                        candidate=validate_candidate(entity,{**claim,'url':actual},texts[actual])
                        candidate.evidence.update(source_sha256=hashlib.sha256(texts[actual].encode()).hexdigest(),
                            discovery_project_id=identifier)
                        candidates.append(candidate)
                    except Exception as exc:issues.append(ctx.error('web_evidence_rejected',diagnostic(exc),'web_search',entity.id))
                results[entity.id]=self.workflow.result(candidates,issues,[{'url':s['url'],'title':'Downloaded source'} for s in sources])
        return results
