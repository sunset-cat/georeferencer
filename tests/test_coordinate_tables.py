import asyncio
from pathlib import Path

import pytest

from georeferencer.config import Settings
from georeferencer.coordinate_tables import collar_rows
from georeferencer.models import Document, Entity, Extraction, VerificationResult
from georeferencer.pipeline import Pipeline
from georeferencer.projections import transform_coordinates
from georeferencer.workflows import ExplicitCoordinates

TABLE = (Path(__file__).parent/'fixtures/monte_carmo_collars.txt').read_text(encoding='utf-8')


def test_original_press_release_all_17_collars_resolve_without_search(tmp_path):
    class Extractor:
        async def extract(self,document,ctx,feedback):
            # Deterministic table recovery also covers holes omitted by extraction.
            return Extraction(entities=[])
    class Search:
        name='web_search'
        def supports(self,entity): return True
        async def verify(self,entity,ctx): raise AssertionError('Input collars must not use paid search')
    result=asyncio.run(Pipeline(Settings(log_dir=tmp_path),Extractor(),[ExplicitCoordinates(),Search()]).run('Original July 2022 collar table',TABLE))
    assert len(result.entities)==len(result.geojson['features'])==17
    assert result.status=='completed'
    assert result.usage.model_calls==result.usage.web_requests==0
    hole=next(r for r in result.entities if r.entity.name=='FSA-221')
    assert hole.geometry.coordinates==pytest.approx((-48.0733711,-10.7539184),abs=1e-6)
    assert hole.geometry_provenance.origin=='document'
    assert hole.geometry_provenance.crs_status=='transformed'
    assert hole.geometry_provenance.transformation['source_crs']=='EPSG:29192'
    assert hole.entity.coordinate_claims[0].easting==820158
    assert 'zone 22S' in hole.entity.coordinate_claims[0].crs_evidence.quote


def test_header_order_and_explicit_crs_are_required():
    table='Easting Northing Elevation\nFSA-221 820158 8809745 427\nDatum SAD69 / zone 22S.\n'
    rows=collar_rows(table)
    assert len(rows)==1 and rows[0]['claim'].northing==8809745
    assert not collar_rows('FSA-221 820158 8809745 427')
    with pytest.raises(ValueError,match='zone and hemisphere'):
        transform_coordinates('SAD69 / UTM',820158,8809745)
    with pytest.raises(ValueError,match='column ordering'):
        transform_coordinates('SAD69 / UTM zone 22S',8809745,820158)


def test_table_missing_crs_is_awaiting_conversion_not_a_missing_location(tmp_path):
    class Extractor:
        async def extract(self,*args):return Extraction(entities=[])
    result=asyncio.run(Pipeline(Settings(log_dir=tmp_path),Extractor(),[ExplicitCoordinates()]).run('Missing datum',TABLE.split('*Collar')[0]))
    assert len(result.entities)==17 and not result.geojson['features']
    assert all(r.resolution_state=='awaiting_conversion' for r in result.entities)


def test_continue_reuses_extraction_and_completed_geometry(tmp_path):
    class Extractor:
        calls=0
        async def extract(self,*args):
            self.calls+=1
            return Extraction(entities=[Entity(id='place',name='Tocantins',category='general_geographic',subtype='administrative_area',role='context_location',aliases=[],mentions=[{'quote':'Tocantins'}],identifiers=[],country_code='BR',jurisdiction='Tocantins')])
    class Budget:
        name='web_search'
        def supports(self,e):return True
        async def verify(self,e,ctx):
            issue=ctx.error('web_budget_exhausted','Limit reached',self.name,e.id)
            return VerificationResult(workflow=self.name,status='unavailable',issues=[issue])
    extractor=Extractor();pipeline=Pipeline(Settings(log_dir=tmp_path),extractor,[ExplicitCoordinates(),Budget()])
    text='Tocantins\n'+TABLE
    first=asyncio.run(pipeline.run('table',text,include_context=True))
    assert first.progress['resolved']==17 and first.progress['pending']==1
    second=asyncio.run(pipeline.run('table',text,include_context=True,resume=first))
    assert extractor.calls==1 and second.resumed_from==first.request_id
    assert second.progress['resolved']==17 and second.progress['pending']==1
