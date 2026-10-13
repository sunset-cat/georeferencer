"""Compare points using stated transformation accuracy and source precision."""
import json
import math
import re


def distance_metres(a,b):
    lon1,lat1,lon2,lat2=map(math.radians,(*a,*b))
    h=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6_371_000*2*math.asin(min(1,math.sqrt(h)))


def precision_metres(candidate):
    evidence=candidate.evidence if candidate else {}
    if 'transformation' in evidence:
        accuracy=json.loads(evidence['transformation']).get('accuracy_metres',-1)
        return max(1,accuracy) if accuracy>=0 else 1
    if evidence.get('coordinate_format')=='dms':
        return 16 # Half an arcsecond rounded to integer seconds, conservatively.
    # Only infer decimal rounding precision from actual supported coordinate tokens.
    values=[]
    for value in candidate.geometry.coordinates if candidate and candidate.geometry else []:
        tokens=re.findall(r'[-+]?\d+\.\d+',evidence.get('coordinate_quote',''))
        for token in tokens:
            if float(token)==value:
                values.append(0.5*111_320*10**(-len(token.split('.')[1])))
    return max(values,default=0)


def compatible_points(left,right):
    if left.geometry_basis!=right.geometry_basis:
        return False
    tolerance=precision_metres(left)+precision_metres(right)
    return distance_metres(left.geometry.coordinates,right.geometry.coordinates)<=tolerance
