"""Coordinate extents of explicitly linked subaltern locations, excluding context."""


def location_extents(resolutions):
    units={r.entity.id:r.entity for r in resolutions if r.entity.entity_kind=='semantic_unit' and r.status!='excluded'}
    members={identifier:[] for identifier in units}
    for record in resolutions:
        entity=record.entity
        if entity.entity_kind=='subaltern_location' and entity.semantic_unit in members:
            members[entity.semantic_unit].append(record)
    results=[]
    for identifier,unit in units.items():
        records=members[identifier]
        located=[r for r in records if r.status=='resolved' and r.geometry is not None]
        result={'id':identifier,'name':unit.name,'member_ids':[r.entity.id for r in records],
            'located_member_ids':[r.entity.id for r in located],'point_count':len(located),
            'bbox':None,'crosses_antimeridian':False,'extreme_entity_ids':{}}
        if located:
            longitudes=sorted(r.geometry.coordinates[0]%360 for r in located)
            gap=max(range(len(longitudes)),key=lambda i:(longitudes[(i+1)%len(longitudes)]-longitudes[i])%360 if len(longitudes)>1 else 360)
            start=longitudes[(gap+1)%len(longitudes)]
            def unwrapped(record):return (record.geometry.coordinates[0]%360-start)%360
            west=(start+180)%360-180
            east=(start+max(unwrapped(r) for r in located)+180)%360-180
            south=min(r.geometry.coordinates[1] for r in located)
            north=max(r.geometry.coordinates[1] for r in located)
            result.update(bbox=[west,south,east,north],crosses_antimeridian=west>east,
                extreme_entity_ids={
                    'west':[r.entity.id for r in located if unwrapped(r)==0],
                    'east':[r.entity.id for r in located if unwrapped(r)==max(unwrapped(e) for e in located)],
                    'south':[r.entity.id for r in located if r.geometry.coordinates[1]==south],
                    'north':[r.entity.id for r in located if r.geometry.coordinates[1]==north]})
        results.append(result)
    return results
