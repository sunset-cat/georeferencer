"""Forward-only location memory for sequential document chunks."""
from .validation import normalize
from .workflows import normalize_jurisdiction


def build_chunk_context(entities, document):
    locations=[]
    contexts={}
    for entity in entities:
        mentions=[m for m in entity.mentions if m.start is not None]
        if not mentions:
            continue
        locations.append({
            'id':entity.id,'name':entity.name,'aliases':entity.aliases,
            'entity_kind':entity.entity_kind,'semantic_unit':entity.semantic_unit,
            'subtype':entity.subtype,'role':entity.role,
            'verification_classes':[str(c) for c in entity.verification_classes],
            'country_code':entity.country_code,'jurisdiction':entity.jurisdiction,
            'identifiers':[i.model_dump() for i in entity.identifiers],
            'project_ids':entity.project_ids,
        })
        if entity.role not in {'primary_subject','subject_component','context_location'}:
            continue
        geographic=entity.subtype in {'administrative_area','settlement'}
        if not geographic and not (entity.country_code or entity.jurisdiction):
            continue
        # A named locality is its own hint, not a geographic scope for every
        # other entity with the same country/region (e.g. a town and a project).
        key=(entity.country_code,entity.jurisdiction,entity.id if geographic else None)
        context=contexts.setdefault(key,{
            'country_code':entity.country_code,'jurisdiction':entity.jurisdiction,
            'geographic_locations':[],'applies_to':[],'evidence':[],
        })
        context['applies_to'].append(entity.id)
        if geographic:
            context['geographic_locations'].append({'id':entity.id,'name':entity.name,'subtype':entity.subtype})
        # A few verbatim excerpts per context keep memory compact. Full mentions
        # remain on the entities, and scope references retain every applicable ID.
        if len(context['evidence'])<3:
            mention=mentions[0]
            context['evidence'].append({'entity_id':entity.id,'quote':mention.quote,
                'start':mention.start,'end':mention.end,
                'context':document.text[max(0,mention.start-180):mention.end+180]})
    return {'existing_locations':locations,'geographic_contexts':list(contexts.values())}


def reference_conflict(existing, update):
    if existing.semantic_unit and update.semantic_unit and existing.semantic_unit!=update.semantic_unit:
        return 'Semantic-unit parent differs from the referenced entity'
    from .routing import is_mining_asset
    if is_mining_asset(existing) and existing.project_ids and update.project_ids and not set(existing.project_ids)&set(update.project_ids):
        return 'Project association differs from the referenced mining entity'
    if (existing.category,existing.subtype)!=(update.category,update.subtype):
        return 'Location type differs from the referenced entity'
    if existing.country_code and update.country_code and existing.country_code!=update.country_code:
        return 'Country differs from the referenced entity'
    if existing.jurisdiction and update.jurisdiction and normalize_jurisdiction(existing.jurisdiction)!=normalize_jurisdiction(update.jurisdiction):
        return 'Jurisdiction differs from the referenced entity'
    def names(entity):
        return {normalize(n) for n in [entity.name,*entity.aliases]
            if normalize(n) not in {'the project','the zone','the deposit','the property'}}
    if not names(existing)&names(update):
        return 'Name/aliases do not identify the referenced entity'
    for left in existing.identifiers:
        if any(right.namespace==left.namespace and right.value!=left.value for right in update.identifiers):
            return 'Identifiers conflict within the same namespace'
    return None


def merge_update(existing, update):
    """Add current-chunk evidence to a checked existing reference."""
    priority={'primary_subject':0,'subject_component':1,'context_location':2,
        'corporate_contact':3,'not_relevant':4,'unknown':5}
    if priority[update.role]<priority[existing.role]:
        existing.role=update.role
    existing.country_code=existing.country_code or update.country_code
    existing.jurisdiction=existing.jurisdiction or update.jurisdiction
    rank={'semantic_unit':0,'subaltern_location':1,'general_geographic':2}
    if rank[update.entity_kind]<rank[existing.entity_kind]:existing.entity_kind=update.entity_kind
    if existing.entity_kind=='subaltern_location':existing.semantic_unit=existing.semantic_unit or update.semantic_unit
    else:existing.semantic_unit=None
    existing.project_ids=list(dict.fromkeys(existing.project_ids+update.project_ids))
    existing.aliases=sorted(set(existing.aliases+update.aliases+[update.name])-{existing.name})
    classes=set(existing.verification_classes)|set(update.verification_classes)
    if len(classes)>1:classes.discard('unknown')
    existing.verification_classes=sorted(classes)
    for field in ['mentions','coordinate_claims','identifiers']:
        values=getattr(existing,field)
        seen={v.model_dump_json() for v in values}
        for value in getattr(update,field):
            if value.model_dump_json() not in seen:
                values.append(value)
                seen.add(value.model_dump_json())
