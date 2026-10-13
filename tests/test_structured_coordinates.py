import json

import pytest

from georeferencer.models import Entity
from georeferencer.web_resolution import validate_candidate


def hole():
    return Entity(id='hole',name='FSA-221',category='mining',subtype='drill_hole',
        role='subject_component',aliases=[],identifiers=[],country_code='BR',jurisdiction='Tocantins',
        mentions=[{'quote':'FSA-221'}])


def table_claim():
    return dict(name='FSA-221',url='https://survey.example/report',
        identity_quote='FSA-221 is a drill hole in Tocantins.',coordinate_format='projected',
        longitude=None,latitude=None,easting=820158,northing=8809745,elevation=426.852,
        units='m',zone=22,hemisphere='S',crs='SAD69 / UTM',meaning='exact_site',
        coordinate_quotes=['FSA-221 8809745 820158 426.852'],
        header_quotes=['Hole identifier | Northing (m) | Easting (m) | Elevation (m)'],
        crs_quotes=['All drill-hole collar coordinates in this table use SAD69 / UTM zone 22S.'])


def source(claim):
    return '\n\n'.join([claim['identity_quote'],*claim['header_quotes'],
        *claim['coordinate_quotes'],*claim['crs_quotes']])


def test_llm_mapped_northing_first_row_and_separate_crs_are_converted():
    c=table_claim()
    result=validate_candidate(hole(),c,source(c))
    assert result.geometry.coordinates==pytest.approx((-48.0733711,-10.7539184),abs=1e-6)
    assert result.evidence['crs_status']=='transformed'
    assert json.loads(result.evidence['header_quotes'])==c['header_quotes']
    assert json.loads(result.evidence['transformation'])['source_crs']=='EPSG:29192'
    assert json.loads(result.evidence['structured_values'])['easting']==820158
    # Interpret a different header vocabulary using LLM-supplied fields, without
    # hardcoding an English collar-table column format.
    c['header_quotes']=['Identifiant | Y (m) | X (m) | Altitude (m)']
    assert validate_candidate(hole(),c,source(c)).geometry is not None


@pytest.mark.parametrize('field',['coordinate_quotes','header_quotes','crs_quotes'])
def test_each_quote_must_exist_independently(field):
    c=table_claim();body=source(c);c[field].append('Invented source passage')
    with pytest.raises(ValueError,match='absent from retrieved source'):
        validate_candidate(hole(),c,body)


@pytest.mark.parametrize('field,value',[('easting',820159),('northing',8809746),('elevation',999),
    ('easting',True),('northing',float('inf'))])
def test_invented_or_invalid_structured_values_are_rejected(field,value):
    c=table_claim();body=source(c);c[field]=value
    with pytest.raises(ValueError):validate_candidate(hole(),c,body)


def test_missing_or_conflicting_zone_is_retained_without_geometry():
    c=table_claim();c['crs_quotes']=['This table uses SAD69 / UTM.'];c['zone']=c['hemisphere']=None
    result=validate_candidate(hole(),c,source(c))
    assert result.geometry is None and result.evidence['original_easting']=='820158'
    assert 'zone and hemisphere' in result.evidence['coordinate_validation']
    c=table_claim();c['zone']=21
    result=validate_candidate(hole(),c,source(c))
    assert result.geometry is None and 'contradicts' in result.evidence['coordinate_validation']


def test_structured_zone_cannot_supply_missing_source_evidence():
    c=table_claim();c['crs_quotes']=['This table uses SAD69 / UTM.']
    result=validate_candidate(hole(),c,source(c))
    assert result.geometry is None and 'zone and hemisphere' in result.evidence['coordinate_validation']


def test_row_must_identify_requested_hole_not_similarly_named_one():
    c=table_claim();c['coordinate_quotes']=['FSA-2210 8809745 820158 426.852']
    with pytest.raises(ValueError,match='identify the requested entity'):
        validate_candidate(hole(),c,source(c))


def test_unlabelled_row_requires_header_and_conversion_needs_scope():
    c=table_claim();c['header_quotes']=[]
    with pytest.raises(ValueError,match='header quotations'):validate_candidate(hole(),c,source(c))
    c=table_claim();c['meaning']='unknown'
    result=validate_candidate(hole(),c,source(c))
    assert result.geometry is None and result.evidence['converted_longitude']
    assert 'spatial meaning' in result.evidence['coordinate_validation']


def dms_claim():
    return dict(name='FSA-221',url='https://survey.example/report',identity_quote='FSA-221 in Tocantins.',
        coordinate_format='dms',longitude=None,latitude=None,crs='unknown',meaning='exact_site',
        latitude_dms='10° 45\' 4" south',longitude_dms='48° 4\' 20" west',
        coordinate_quotes=['FSA-221 latitude: 10° 45\' 4" south.', 'Longitude: 48° 4\' 20" west.'],
        header_quotes=[],crs_quotes=[])


def test_dms_expressions_in_separate_quotes_convert_with_labelled_assumption():
    c=dms_claim();result=validate_candidate(hole(),c,source(c))
    assert result.geometry.coordinates==pytest.approx((-48.072222222,-10.751111111))
    assert result.evidence['crs_status']=='assumed'
    c['crs']='WGS84';c['crs_quotes']=['Geographic coordinates use WGS84.']
    assert validate_candidate(hole(),c,source(c)).evidence['crs_status']=='stated'


def test_dms_fields_cannot_be_swapped_or_invented():
    c=dms_claim();c['latitude_dms'],c['longitude_dms']=c['longitude_dms'],c['latitude_dms']
    with pytest.raises(ValueError,match='hemispheres'):validate_candidate(hole(),c,source(c))
    c=dms_claim();body=source(c);c['longitude_dms']='49° 4\' 20" west'
    with pytest.raises(ValueError,match='absent'):validate_candidate(hole(),c,body)


def test_decimal_values_and_explicit_datum_are_checked():
    c=dms_claim();c.update(coordinate_format='decimal',longitude=-48.07,latitude=-10.75,
        latitude_dms=None,longitude_dms=None,coordinate_quotes=['FSA-221: longitude -48.07, latitude -10.75.'],
        crs='WGS84',crs_quotes=['Geographic coordinates use WGS84.'])
    result=validate_candidate(hole(),c,source(c))
    assert result.geometry.coordinates==(-48.07,-10.75)
    c['crs']='SAD69'
    with pytest.raises(ValueError,match='contradicts'):validate_candidate(hole(),c,source(c))
    c['crs_quotes']=['Geographic coordinates use SAD69.']
    assert validate_candidate(hole(),c,source(c)).evidence['crs_status']=='transformed'


def test_unknown_format_does_not_accept_numeric_coordinates():
    c=table_claim();c['coordinate_format']='unknown'
    with pytest.raises(ValueError,match='Unknown coordinate format'):
        validate_candidate(hole(),c,source(c))
