"""Deterministic angular conversion; changing notation does not change datum."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DMSPair:
    longitude: float
    latitude: float
    longitude_expression: str
    latitude_expression: str


NUMBER = r"\d+(?:[.,]\d+)?"
HEMISPHERE = r"(?:north|south|east|west|[NSEW])\b"
ANGLE = re.compile(
    rf"(?<![\w.+-])(?P<prefix>{HEMISPHERE}\s*)?"
    rf"(?P<degrees>[+-]?\d+)\s*(?:[°º˚]|degrees?)\s*"
    rf"(?P<minutes>{NUMBER})\s*(?:['′’]|minutes?)\s*"
    rf"(?:(?P<seconds>{NUMBER})\s*(?:[\"″”]|['′’]{{2}}|seconds?)\s*)?"
    rf"(?(prefix)(?P<suffix_after_prefix>{HEMISPHERE}(?!\s*[+-]?\d+\s*(?:[°º˚]|degrees?)))"
    rf"|(?P<suffix>{HEMISPHERE}))?", re.IGNORECASE)


def parse_dms_pair(text: str) -> DMSPair | None:
    """Read exactly one latitude/longitude pair with explicit hemispheres.

    Reject incomplete, invalid or ambiguous DMS rather than guessing axis/order.
    Supports straight/typographic primes, hemisphere prefixes/suffixes, and
    decimal seconds. Preserve the original expressions for source provenance.
    """
    found = {}
    for match in ANGLE.finditer(text):
        prefix = (match.group("prefix") or "").strip().upper()
        suffix = (match.group("suffix") or match.group("suffix_after_prefix") or "").strip().upper()
        if not prefix and not suffix:
            raise ValueError("DMS coordinate has no hemisphere; axis/sign cannot be established")
        if prefix and suffix and prefix[0] != suffix[0]:
            raise ValueError("DMS coordinate has contradictory hemispheres")
        hemisphere = (prefix or suffix)[0]
        axis = "latitude" if hemisphere in "NS" else "longitude"
        degrees = int(match.group("degrees"))
        minutes = float(match.group("minutes").replace(",", "."))
        seconds = float((match.group("seconds") or "0").replace(",", "."))
        limit = 90 if axis == "latitude" else 180
        if not (0 <= minutes < 60 and 0 <= seconds < 60 and abs(degrees) <= limit):
            raise ValueError("DMS degrees/minutes/seconds are outside valid ranges")
        if abs(degrees) == limit and (minutes or seconds):
            raise ValueError("DMS coordinate exceeds the latitude/longitude limit")
        if match.group("degrees").startswith("-") and hemisphere in "NE":
            raise ValueError("DMS negative sign contradicts its hemisphere")
        if match.group("degrees").startswith("+") and hemisphere in "SW":
            raise ValueError("DMS positive sign contradicts its hemisphere")
        if axis in found:
            raise ValueError("Multiple DMS coordinates for the same axis; no automatic selection")
        value = (abs(degrees) + minutes / 60 + seconds / 3600) * (-1 if hemisphere in "SW" else 1)
        found[axis] = (value, match.group().strip())
    if not found:
        return None
    if set(found) != {"latitude", "longitude"}:
        raise ValueError("DMS evidence must contain both latitude and longitude")
    return DMSPair(found["longitude"][0], found["latitude"][0],
                   found["longitude"][1], found["latitude"][1])


def coordinate_evidence_matches(claim) -> bool:
    if claim.coordinate_format == "projected":
        numbers = [float(n) for n in re.findall(r"[-+]?\d+(?:\.\d+)?", claim.evidence.quote)]
        return claim.easting in numbers and claim.northing in numbers
    if claim.coordinate_format == "dms":
        import math
        try:
            pair = parse_dms_pair(claim.evidence.quote)
            return bool(pair and math.isclose(claim.longitude, pair.longitude, abs_tol=1e-9, rel_tol=0)
                        and math.isclose(claim.latitude, pair.latitude, abs_tol=1e-9, rel_tol=0))
        except ValueError:
            return False
    numbers = [float(n) for n in re.findall(r"[-+]?\d+(?:\.\d+)?", claim.evidence.quote)]
    return claim.longitude in numbers and claim.latitude in numbers


def parse_projected_pair(text):
    """Read explicitly labelled metre axes; never infer order from magnitude."""
    number=r'(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)'
    found={}
    for axis,labels in [('easting',r'm\s*E'),('northing',r'm\s*N')]:
        matches=[m.group('number') for m in re.finditer(rf'(?P<number>{number})\s*(?:{labels})\b',text,re.I)]
        matches.extend(m.group('number') for m in re.finditer(rf'\b(?:{axis}|UTM\s*{"E" if axis=="easting" else "N"})\s*[:=]?\s*(?P<number>{number})',text,re.I))
        values={float(v.replace(',','')) for v in matches}
        if len(values)>1:raise ValueError('Multiple projected coordinates for one axis; no automatic selection')
        if values:found[axis]=values.pop()
    return (found['easting'],found['northing']) if len(found)==2 else None
