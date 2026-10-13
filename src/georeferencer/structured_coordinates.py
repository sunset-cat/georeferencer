"""Check LLM-interpreted coordinate fields against separately quoted evidence."""
import json
import math
import re

from .coordinates import parse_dms_pair
from .models import Point
from .projections import source_crs, transform_coordinates
from .validation import normalize


def _quotes(claim, field, singular, canonical):
    values=claim.get(field) or []
    if not isinstance(values,list) or any(not isinstance(v,str) or not v.strip() for v in values):
        raise ValueError(f'{field} must be a list of nonempty quotations')
    old=claim.get(singular)
    if old and old not in values:values=[*values,old]
    for quote in values:
        if not isinstance(quote,str) or ' '.join(quote.split()) not in canonical:
            raise ValueError(f'{field}: quotation is absent from retrieved source')
    return values


def _number(claim,field,required=True):
    value=claim.get(field)
    if value is None and not required:return None
    if type(value) not in {float,int} or not math.isfinite(value):
        raise ValueError(f'{field} must be a finite number')
    return value


def _check_values(values,quotes):
    tokens=re.findall(r'[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)',' '.join(quotes))
    numbers={float(t.replace(',','')) for t in tokens}
    if any(value not in numbers for value in values if value is not None):
        raise ValueError('Structured coordinate values are not present in the coordinate quotations')


def _quoted_crs(evidence):
    codes=set(re.findall(r'\bEPSG\s*:\s*(\d+)\b',evidence,re.I))
    if len(codes)>1:raise ValueError('Multiple CRS codes occur in evidence; provide scoped quotations')
    if codes:return source_crs('EPSG:'+next(iter(codes)))
    if re.search(r'\bUTM\b',evidence,re.I):return source_crs(evidence)
    for pattern,label in [(r'WGS\s*84','WGS84'),(r'SAD\s*69','SAD69'),
                          (r'NAD\s*83','NAD83'),(r'NAD\s*27','NAD27')]:
        if re.search(pattern,evidence,re.I):return source_crs(label)
    return None


def apply_structured_coordinates(candidate,entity,claim,text):
    canonical=' '.join(text.split())
    coordinate_quotes=_quotes(claim,'coordinate_quotes','coordinate_quote',canonical)
    header_quotes=_quotes(claim,'header_quotes','header_quote',canonical)
    crs_quotes=_quotes(claim,'crs_quotes','crs_quote',canonical)
    coordinate_format=claim['coordinate_format']
    if not isinstance(coordinate_format,str) or coordinate_format not in {'decimal','dms','projected','unknown'}:
        raise ValueError('Unsupported coordinate_format')
    candidate.evidence.update(coordinate_format=coordinate_format,
        structured_values=json.dumps({k:claim[k] for k in ['longitude','latitude','latitude_dms','longitude_dms',
            'easting','northing','elevation','units','zone','hemisphere'] if claim.get(k) is not None}),
        coordinate_quotes=json.dumps(coordinate_quotes,ensure_ascii=False),
        header_quotes=json.dumps(header_quotes,ensure_ascii=False),
        crs_quotes=json.dumps(crs_quotes,ensure_ascii=False))
    if coordinate_quotes:candidate.evidence['coordinate_quote']='\n'.join(coordinate_quotes)
    if crs_quotes:candidate.evidence['crs_quote']='\n'.join(crs_quotes)
    if coordinate_format=='unknown':
        if any(claim.get(k) is not None for k in ['longitude','latitude','easting','northing','latitude_dms','longitude_dms']):
            raise ValueError('Unknown coordinate format must not contain interpreted coordinate values')
        candidate.evidence['coordinate_validation']='No supported coordinate interpretation returned'
        return candidate
    if not coordinate_quotes:raise ValueError('Coordinate interpretation requires coordinate quotations')
    assignment=normalize(' '.join(coordinate_quotes+header_quotes))
    names=[normalize(entity.name),*(normalize(a) for a in entity.aliases)]
    if not any(n and re.search(r'(?<!\w)'+re.escape(n)+r'(?!\w)',assignment) for n in names):
        raise ValueError('Coordinate evidence does not identify the requested entity')
    source_label=claim.get('crs') or 'unknown'
    if not isinstance(source_label,str):raise ValueError('crs must be a string or null')
    source_evidence=' '.join(coordinate_quotes+header_quotes+crs_quotes)
    crs_status='stated'
    if coordinate_format=='projected':
        if claim.get('longitude') is not None or claim.get('latitude') is not None:
            raise ValueError('Projected claims must not contain model-converted longitude/latitude')
        x,y=_number(claim,'easting'),_number(claim,'northing')
        elevation=_number(claim,'elevation',required=False)
        _check_values([x,y,elevation],coordinate_quotes)
        if not header_quotes and not re.search(r'\b(?:easting|northing|mE|mN)\b',' '.join(coordinate_quotes),re.I):
            raise ValueError('An unlabelled coordinate row requires supporting header quotations')
        candidate.evidence.update(original_easting=str(x),original_northing=str(y),source_crs=source_label)
        if elevation is not None:candidate.evidence['original_elevation']=str(elevation)
        zone,hemisphere=claim.get('zone'),claim.get('hemisphere')
        if zone is not None and (type(zone) is not int or not 1<=zone<=60):raise ValueError('UTM zone must be an integer from 1 to 60')
        if hemisphere is not None and hemisphere not in {'N','S'}:raise ValueError('Projected hemisphere must be N or S')
        try:
            declared=source_crs(source_label,zone,hemisphere)
            supported=_quoted_crs(source_evidence)
            if supported is None:raise ValueError('Source datum/projection/zone evidence is missing')
            if not declared.is_projected:raise ValueError('Projected values require a projected source CRS')
            if not declared.equals(supported):raise ValueError('Structured CRS contradicts the quoted source coordinate system')
            units=claim.get('units')
            if units not in {None,'m','metre','meter','metres','meters'}:
                raise ValueError('Projected units are unsupported; original values retained')
            if not all(axis.unit_name.lower() in {'metre','meter'} for axis in declared.axis_info[:2]):
                raise ValueError('Source CRS uses unsupported projected units')
            coords,metadata=transform_coordinates(declared.to_string(),x,y)
            crs_status='transformed'
            candidate.evidence['transformation']=json.dumps(metadata)
        except (ValueError,RuntimeError) as exc:
            candidate.evidence['coordinate_validation']=str(exc)
            return candidate
    else:
        if claim.get('easting') is not None or claim.get('northing') is not None:
            raise ValueError('Geographic claims must not contain projected coordinate fields')
        if coordinate_format=='dms':
            expressions=[claim.get('latitude_dms'),claim.get('longitude_dms')]
            if any(not isinstance(v,str) or not v.strip() for v in expressions):
                raise ValueError('DMS claims require original latitude_dms and longitude_dms expressions')
            quoted=' '.join(' '.join(q.split()) for q in coordinate_quotes)
            if any(' '.join(v.split()) not in quoted for v in expressions):
                raise ValueError('DMS expressions are absent from coordinate quotations')
            if claim.get('longitude') is not None or claim.get('latitude') is not None:
                raise ValueError('DMS claims must not contain model-converted decimal values')
            latitude=parse_dms_pair(expressions[0]+' '+expressions[1])
            if latitude is None:raise ValueError('No complete DMS coordinate pair was returned')
            # Reject reversed fields even though their hemispheres permit parsing.
            if latitude.latitude_expression!=expressions[0].strip() or latitude.longitude_expression!=expressions[1].strip():
                raise ValueError('DMS fields do not match their latitude/longitude hemispheres')
            x,y=latitude.longitude,latitude.latitude
            candidate.evidence.update(original_latitude=expressions[0],original_longitude=expressions[1],
                conversion='Python: degrees + minutes/60 + seconds/3600; south/west negative. Datum checked separately.')
        else:
            x,y=_number(claim,'longitude'),_number(claim,'latitude')
            _check_values([x,y],coordinate_quotes)
            candidate.evidence.update(original_longitude=str(x),original_latitude=str(y))
        Point(coordinates=(x,y))
        supported=_quoted_crs(source_evidence)
        unspecified=source_label.strip().upper() in {'','UNKNOWN','UNSPECIFIED'}
        if unspecified and supported is None:
            if re.search(r'\b(?:UTM|SIRGAS|NAD|SAD|EPSG)\b',source_evidence,re.I):
                raise ValueError('Source states an unrecognised coordinate system; no WGS84 assumption')
            source_label='WGS84';crs_status='assumed'
            candidate.evidence['source_crs']='unknown'
        else:
            declared=supported if unspecified else source_crs(source_label)
            if supported is None:raise ValueError('Declared source CRS is not supported by quoted evidence')
            if not declared.equals(supported):raise ValueError('Structured CRS contradicts quoted source coordinate system')
            if not declared.is_geographic:raise ValueError('Geographic values require a geographic source CRS')
            source_label=declared.to_string()
            candidate.evidence['source_crs']=source_label
            if not declared.equals(source_crs('WGS84')):
                (x,y),metadata=transform_coordinates(source_label,x,y)
                candidate.evidence['transformation']=json.dumps(metadata)
                crs_status='transformed'
        coords=(x,y)
    candidate.evidence.update(crs='WGS84',crs_status=crs_status,
        converted_longitude=str(coords[0]),converted_latitude=str(coords[1]))
    if claim.get('meaning') not in {'exact_site','representative_point'}:
        candidate.evidence['coordinate_validation']='Coordinates were checked, but their spatial meaning for this entity is not established'
        return candidate
    candidate.geometry=Point(coordinates=coords)
    candidate.geometry_basis=claim['meaning']
    candidate.evidence['coordinate_validation']='Structured values and individual quotations checked; '+('WGS84 assumed because source datum is unspecified.' if crs_status=='assumed' else 'source coordinate system checked and coordinates processed in Python.')
    return candidate
