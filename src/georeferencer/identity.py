"""Within-document consolidation using compatible names, context and identifiers."""
from .models import Extraction
from .validation import normalize


def consolidate(extraction: Extraction) -> tuple[Extraction, list[tuple[str, str]]]:
    result = extraction.model_copy(deep=True)
    groups = []
    remap = {}
    merged = []
    related = {frozenset((r.subject, r.object)) for r in result.relationships}
    for entity in result.entities:
        match = None
        for group in groups:
            from .workflows import normalize_jurisdiction
            same_context = (entity.category, entity.subtype, normalize(entity.country_code), normalize_jurisdiction(entity.jurisdiction)) == (group.category, group.subtype, normalize(group.country_code), normalize_jurisdiction(group.jurisdiction))
            if entity.semantic_unit and group.semantic_unit:
                same_context=bool(same_context and remap.get(entity.semantic_unit,entity.semantic_unit)==remap.get(group.semantic_unit,group.semantic_unit))
            from .routing import is_mining_asset
            if is_mining_asset(entity) and (entity.project_ids or group.project_ids):
                left={remap.get(identifier,identifier) for identifier in entity.project_ids}
                right={remap.get(identifier,identifier) for identifier in group.project_ids}
                same_context=bool(same_context and left and right and left.intersection(right))
            group_ids = {(i.namespace, i.value) for i in group.identifiers}
            entity_ids = {(i.namespace, i.value) for i in entity.identifiers}
            # Require identical nonempty identifiers. Partial overlaps can bridge unrelated entities.
            group_members = {old for old, target in remap.items() if target == group.id} | {group.id}
            name_match = normalize(entity.name) == normalize(group.name) and bool(entity.country_code or entity.jurisdiction)
            no_id_conflict = not entity_ids or not group_ids or entity_ids == group_ids
            if same_context and no_id_conflict and ((entity_ids and entity_ids == group_ids) or name_match) and not any(frozenset((entity.id, member)) in related for member in group_members):
                match = group
                break
        if match is None:
            groups.append(entity)
            remap[entity.id] = entity.id
            continue
        remap[entity.id] = match.id
        merged.append((entity.id, match.id))
        priority = {"primary_subject":0,"subject_component":1,"context_location":2,"corporate_contact":3,"not_relevant":4,"unknown":5}
        if priority[entity.role] < priority[match.role]:
            match.role = entity.role
        if not match.identifiers:
            match.identifiers = entity.identifiers
        classes = set(match.verification_classes) | set(entity.verification_classes)
        if len(classes) > 1:
            classes.discard("unknown")
        match.verification_classes = sorted(classes)
        match.aliases = sorted(set(match.aliases + entity.aliases + [entity.name]) - {match.name})
        match.project_ids=list(dict.fromkeys(match.project_ids+entity.project_ids))
        rank={'semantic_unit':0,'subaltern_location':1,'general_geographic':2}
        if rank[entity.entity_kind]<rank[match.entity_kind]:match.entity_kind=entity.entity_kind
        match.semantic_unit=(match.semantic_unit or entity.semantic_unit) if match.entity_kind=='subaltern_location' else None
        for field in ("mentions", "coordinate_claims"):
            values = getattr(match, field)
            existing = {v.model_dump_json() for v in values}
            for value in getattr(entity, field):
                if value.model_dump_json() not in existing:
                    values.append(value)
                    existing.add(value.model_dump_json())
    result.entities = groups
    for entity in result.entities:
        entity.project_ids=list(dict.fromkeys(remap.get(identifier,identifier) for identifier in entity.project_ids))
        if entity.semantic_unit:entity.semantic_unit=remap.get(entity.semantic_unit,entity.semantic_unit)
    for relationship in result.relationships:
        relationship.subject = remap[relationship.subject]
        relationship.object = remap[relationship.object]
    return Extraction.model_validate(result.model_dump()), merged
