"""Recover bounded, header-labelled collar tables before external discovery."""
import re

from .models import CoordinateClaim, Entity, Mention, Identifier
from .validation import normalize

VERSION = "collar-tables-v1"
HOLE = re.compile(r"\b[A-Z][A-Z0-9]{0,9}[-–]\d{2,8}[A-Z]?\b", re.I)
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
AXIS = re.compile(r"\b(?:easting|northing|UTM\s+[EN])\b", re.I)


def collar_rows(text):
    lines = text.splitlines(keepends=True)
    offsets, total = [], 0
    for line in lines:
        offsets.append(total); total += len(line)
    rows = []
    for index, line in enumerate(lines):
        # Require two labelled axes close together, not two guessed numbers.
        if not AXIS.search(line):
            continue
        header = " ".join(lines[index:index+7])
        labels = [m.group().upper() for m in AXIS.finditer(header)]
        if len(labels) < 2 or not any("EAST" in s or s.endswith(" E") for s in labels[:2]) or not any("NORTH" in s or s.endswith(" N") for s in labels[:2]):
            continue
        e_first = "EAST" in labels[0] or labels[0].endswith(" E")
        block = []
        started = False
        for j in range(index+1, min(len(lines), index+300)):
            row = lines[j]; found = HOLE.search(row)
            numbers = NUMBER.findall(row[found.end():]) if found else []
            if found and len(numbers) >= 2:
                values = [float(v) for v in numbers]
                east, north = values[:2] if e_first else values[1::-1]
                if not (100_000 <= east <= 900_000 and 0 <= north <= 10_000_000):
                    continue
                started = True
                block.append((j, found.group(), east, north, values[2] if len(values)>2 else None))
            elif started and (row.lstrip().startswith("*") or re.search(r"\b(?:Table|Figure)\s+\d", row, re.I)):
                break
        if not block:
            continue
        # Scope CRS to the table and adjacent footnotes, never another report section.
        scope_end = min(len(lines), block[-1][0]+10)
        scope = "".join(lines[max(0,index-4):scope_end])
        datum = re.search(r"SAD\s*69|WGS\s*84|NAD\s*83", scope, re.I)
        zone = re.search(r"(?:zone|UTM)\s*[:/]?\s*(\d{1,2})\s*([NS])\b", scope, re.I)
        crs = f"{datum.group()} / UTM zone {zone[1]}{zone[2].upper()}" if datum and zone else "unknown / UTM"
        crs_line = next((s.strip() for s in lines[max(0,index-4):scope_end] if datum and zone and datum.group() in s and zone.group() in s), None)
        for j, name, east, north, elevation in block:
            rows.append({"name":name,"claim":CoordinateClaim(coordinate_format="projected",easting=east,northing=north,
                elevation=elevation,crs=crs,zone=int(zone[1]) if zone else None,hemisphere=zone[2].upper() if zone else None,
                meaning="exact_site",evidence=Mention(quote=lines[j].strip()),crs_evidence=Mention(quote=crs_line) if crs_line else None)})
    return list({(r["name"],r["claim"].easting,r["claim"].northing):r for r in rows}.values())


def attach_tables(extraction, document, ctx):
    rows = collar_rows(document.text)
    for row in rows:
        name = row["name"]
        matches = [e for e in extraction.entities if e.subtype == "drill_hole" and normalize(name) in
                   {normalize(n) for n in [e.name,*e.aliases,*(i.value for i in e.identifiers)]}]
        if not matches:
            subjects = [e for e in extraction.entities if e.role=="primary_subject"]
            countries = {e.country_code for e in subjects if e.country_code}
            regions = {e.jurisdiction for e in subjects if e.jurisdiction}
            units=[e for e in subjects if e.entity_kind=='semantic_unit']
            entity = Entity(id="table-"+name,name=name,category="mining",subtype="drill_hole",role="subject_component",
                entity_kind='subaltern_location',semantic_unit=units[0].id if len(units)==1 else None,
                aliases=[],mentions=[Mention(quote=name)],identifiers=[Identifier(namespace="document_drill_id",value=name)],
                country_code=next(iter(countries)) if len(countries)==1 else None,jurisdiction=next(iter(regions)) if len(regions)==1 else None,
                verification_classes=["drilling_site"])
            extraction.entities.append(entity); matches=[entity]
        for entity in matches:
            if row["claim"].model_dump_json() not in {c.model_dump_json() for c in entity.coordinate_claims}:
                entity.coordinate_claims.append(row["claim"].model_copy(deep=True))
    ctx.event("coordinates.tables",rows=len(rows),holes=len({r['name'] for r in rows}),version=VERSION)
    return rows
