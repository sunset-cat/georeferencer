"""Refresh deterministic acceptance from saved evidence, without paid requests."""
import asyncio
import json
from pathlib import Path

from georeferencer.config import Settings
from georeferencer.default_documents import seed_defaults
from georeferencer.library import LibraryStore
from georeferencer.models import RunResult
from georeferencer.revalidation import revalidate


async def main():
    settings=Settings.from_env()
    store=LibraryStore(settings.library_dir)
    seed_defaults(store)
    rows=[store.get(i['id']) for i in store.list() if i['id'].startswith('default-')]
    if any(r['status']=='processing' for r in rows):
        raise RuntimeError('Wait for active library jobs to finish before refreshing')
    summary=[]
    for row in rows:
        previous=RunResult.model_validate_json(row['result'])
        result=await revalidate(settings,row,previous)
        assert result.usage.model_calls==result.usage.web_requests==result.usage.source_calls==0
        store.finish(row['id'],result)
        summary.append({'id':row['id'],'name':row['name'],
            'paid_run_id':previous.request_id,'refresh_run_id':result.request_id,
            'before':previous.progress,'after':result.progress,
            'paid_run_cost':previous.estimated_cost.model_dump(mode='json') if previous.estimated_cost else None,
            'usage':previous.usage.model_dump(mode='json'),
            'resolved':[e.entity.name for e in result.entities if e.status=='resolved'],
            'unresolved':[{'name':e.entity.name,'state':e.resolution_state,'reason':e.reason,
                'issues':[i.model_dump(mode='json') for i in e.issues]} for e in result.entities if e.status in {'unresolved','conflicting'}]})
    Path('output').mkdir(exist_ok=True)
    Path('output/full-rerun-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{k:r[k] for k in ['id','before','after','resolved']} for r in summary],indent=2))


if __name__=='__main__':asyncio.run(main())
