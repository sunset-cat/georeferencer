import asyncio
import json

import pytest

from georeferencer.config import Settings
from georeferencer.extraction import parse_extraction,strict_schema
from georeferencer.models import Entity,Extraction,Point,Resolution
from georeferencer.observability import RunContext
from georeferencer.pipeline import Pipeline
from georeferencer.project_discovery import build_groups
from georeferencer.semantic_units import location_extents
from georeferencer.chunked_extraction import ChunkedExtractor


def entity(identifier,name,kind,parent=None,subtype='mountain',role='subject_component'):
    return Entity(id=identifier,name=name,entity_kind=kind,semantic_unit=parent,
        category='general_geographic',subtype=subtype,role=role,aliases=[],identifiers=[],
        country_code=None,jurisdiction=None,mentions=[{'quote':name}])


def test_initial_llm_schema_requires_three_kinds_and_parent_field():
    schema=strict_schema()['$defs']['Entity']
    assert set(schema['properties']['entity_kind']['enum'])=={'general_geographic','semantic_unit','subaltern_location'}
    assert {'entity_kind','semantic_unit'}<=set(schema['required'])
    assert 'project_ids' not in schema['properties']
    data=entity('peak','Aneto','subaltern_location','pyrenees').model_dump(mode='json')
    data.pop('category');data.pop('project_ids');data['mentions']=['Aneto']
    result=parse_extraction(json.dumps({'entities':[data]}))
    assert result.entities[0].semantic_unit=='pyrenees'


def records():
    return [
        Resolution(entity=entity('unit','Pyrenees','semantic_unit',role='primary_subject'),status='resolved',geometry=Point(coordinates=(0,42.5))),
        Resolution(entity=entity('fr','France','general_geographic',subtype='administrative_area',role='context_location'),status='resolved',geometry=Point(coordinates=(2,46))),
        Resolution(entity=entity('es','Spain','general_geographic',subtype='administrative_area',role='context_location'),status='resolved',geometry=Point(coordinates=(-3,40))),
        Resolution(entity=entity('aneto','Aneto','subaltern_location','unit'),status='resolved',geometry=Point(coordinates=(0.66,42.63))),
        Resolution(entity=entity('ossau','Ossau','subaltern_location','unit'),status='resolved',geometry=Point(coordinates=(-0.42,42.83))),
        Resolution(entity=entity('pending','Unlocated peak','subaltern_location','unit'),status='unresolved'),
    ]


def test_box_uses_linked_subalterns_not_general_context_or_anchor_point():
    result=location_extents(records())[0]
    assert result['bbox']==pytest.approx([-0.42,42.63,0.66,42.83])
    assert result['point_count']==2
    assert result['member_ids']==['aneto','ossau','pending']
    assert result['extreme_entity_ids']['west']==['ossau']
    assert result['extreme_entity_ids']['south']==['aneto']


def test_box_handles_antimeridian_and_empty_units():
    rs=records();rs[3].geometry=Point(coordinates=(179,42));rs[4].geometry=Point(coordinates=(-179,43))
    result=location_extents(rs)[0]
    assert result['bbox']==pytest.approx([179,42,-179,43])
    assert result['crosses_antimeridian']
    result=location_extents(rs[:3])[0]
    assert result['bbox'] is None and result['point_count']==0


def test_discovery_uses_explicit_kind_and_reference_not_role_inference(tmp_path):
    rs=records()
    # A unit can be a secondary subject. Ownership comes from the explicit schema.
    rs[0].entity.role='context_location'
    entities=[r.entity for r in rs]
    groups,membership=build_groups(entities,entities,RunContext('schema-groups',tmp_path,0,0))
    assert membership['aneto']==['unit']
    assert {e.id for e in groups['unit']['entities']}=={'unit','aneto','ossau','pending'}
    assert membership['fr']==['fr'] and membership['es']==['es']


def test_canonical_parent_survives_chunks(monkeypatch,tmp_path):
    parts=['Pyrenees and Aneto.\n','Aneto additional evidence.\n']
    monkeypatch.setattr('georeferencer.chunked_extraction.split_text',lambda *a,**kw:[(0,len(parts[0]),parts[0]),(len(parts[0]),sum(map(len,parts)),parts[1])])
    class Extractor:
        async def extract(self,document,ctx,feedback):
            if ctx.chunk_index==1:return Extraction(entities=[entity('unit','Pyrenees','semantic_unit',role='primary_subject'),entity('peak','Aneto','subaltern_location','unit')])
            prior=next(e for e in ctx.extraction_context['existing_locations'] if e['name']=='Aneto')
            assert prior['entity_kind']=='subaltern_location' and prior['semantic_unit']=='c1-unit'
            return Extraction(entities=[entity('c1-peak','Aneto','subaltern_location','c1-unit')])
    from georeferencer.models import Document
    settings=Settings(library_dir=tmp_path,log_dir=tmp_path)
    result=asyncio.run(ChunkedExtractor(Extractor(),settings).extract(Document(name='report',text=''.join(parts)),RunContext('parent',tmp_path,0,0),[]))
    assert len(result.entities)==2
    assert result.entities[1].semantic_unit=='c1-unit' and len(result.entities[1].mentions)==2


def test_pipeline_returns_unit_extent_and_geojson_membership(tmp_path):
    rs=records()[:5]
    class Extractor:
        async def extract(self,*args):return Extraction(entities=[r.entity for r in rs])
    class Source:
        name='fixture_geometry'
        def supports(self,e):return True
        async def verify(self,e,ctx):
            from georeferencer.models import VerificationResult
            r=next(r for r in rs if r.entity.id==e.id)
            return VerificationResult(workflow=self.name,status='supported',identity='supported',geometry=r.geometry,geometry_basis='representative_point')
    result=asyncio.run(Pipeline(Settings(log_dir=tmp_path),Extractor(),[Source()]).run('report',' '.join(r.entity.name for r in rs),include_context=True))
    assert result.semantic_unit_extents[0]['point_count']==2
    feature=next(f for f in result.geojson['features'] if f['id']=='aneto')
    assert feature['properties']['entity_kind']=='subaltern_location'
    assert feature['properties']['semantic_unit']=='unit'
