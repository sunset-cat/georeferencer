from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Category(StrEnum):
    geographic = "general_geographic"
    geological = "geological"
    mining = "mining"
    other = "other"


class VerificationClass(StrEnum):
    geographic_place = "geographic_place"
    mineral_tenure = "mineral_tenure"
    drilling_site = "drilling_site"
    geological_feature = "geological_feature"
    mineral_site = "mineral_site"
    unknown = "unknown"


TYPOLOGY = {
    Category.geographic: {"settlement", "administrative_area", "mountain", "volcano", "river", "lake"},
    Category.geological: {"intrusion", "formation", "fault", "geological_unit"},
    Category.mining: {"project", "property", "permit", "mine", "drill_hole", "mineralized_zone"},
    Category.other: {"facility", "unknown"},
}


class Mention(Model):
    quote: str = Field(min_length=1)
    prefix: str | None = None
    suffix: str | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    document_sha256: str | None = None


class Identifier(Model):
    namespace: str
    value: str


class CoordinateClaim(Model):
    longitude: float | None = None
    latitude: float | None = None
    coordinate_format: Literal["decimal", "dms", "projected"] = "decimal"
    easting: float | None = None
    northing: float | None = None
    elevation: float | None = None
    zone: int | None = Field(default=None, ge=1, le=60)
    hemisphere: Literal["N", "S"] | None = None
    crs_evidence: Mention | None = None
    crs: str
    meaning: Literal["exact_site", "representative_point", "unknown"]
    evidence: Mention

    @model_validator(mode="after")
    def normalize_coordinates(self):
        if self.coordinate_format == "projected":
            import math
            if self.easting is None or self.northing is None or not all(math.isfinite(v) for v in (self.easting, self.northing)):
                raise ValueError("Projected coordinates require finite easting and northing")
            if self.longitude is not None or self.latitude is not None:
                raise ValueError("Projected source claims must not contain invented latitude/longitude")
            return self
        if self.coordinate_format == "dms":
            import math
            from .coordinates import parse_dms_pair
            pair = parse_dms_pair(self.evidence.quote)
            if pair is None:
                raise ValueError("No DMS pair occurs in coordinate evidence")
            for name in ("longitude", "latitude"):
                proposed, converted = getattr(self, name), getattr(pair, name)
                if proposed is not None and not math.isclose(proposed, converted, abs_tol=1e-9, rel_tol=0):
                    raise ValueError("Proposed decimal coordinates disagree with DMS evidence")
                setattr(self, name, converted)
        if self.longitude is None or self.latitude is None:
            raise ValueError("Decimal coordinates require both longitude and latitude")
        return self


class Entity(Model):
    id: str
    name: str = Field(min_length=1)
    entity_kind: Literal['general_geographic','semantic_unit','subaltern_location'] = 'general_geographic'
    semantic_unit: str | None = None
    category: Category
    subtype: Literal["settlement", "administrative_area", "mountain", "volcano", "river", "lake", "intrusion", "formation", "fault", "geological_unit", "project", "property", "permit", "mine", "drill_hole", "mineralized_zone", "facility", "unknown"]
    role: Literal["primary_subject", "subject_component", "context_location", "corporate_contact", "not_relevant", "unknown"]
    aliases: list[str]
    mentions: list[Mention] = Field(min_length=1)
    identifiers: list[Identifier]
    country_code: str | None
    jurisdiction: str | None
    coordinate_claims: list[CoordinateClaim] = Field(default_factory=list)
    verification_classes: list[VerificationClass] = Field(default_factory=list, max_length=6)
    project_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='before')
    @classmethod
    def legacy_entity_structure(cls,data):
        if not isinstance(data,dict):return data
        data=dict(data)
        if 'entity_kind' not in data:
            references=data.get('project_ids') or []
            role=data.get('role')
            if role=='primary_subject' or role=='context_location' and data.get('subtype') in {'project','property','mine'}:
                data['entity_kind']='semantic_unit'
            elif role=='subject_component' or len(references)==1 and data.get('subtype')!='administrative_area':
                data['entity_kind']='subaltern_location'
            else:data['entity_kind']='general_geographic'
            if data['entity_kind']=='subaltern_location' and len(references)==1:
                data.setdefault('semantic_unit',references[0])
        return data

    @field_validator("role", mode="before")
    @classmethod
    def legacy_role(cls, value):
        return "not_relevant" if value == "incidental" else value

    @model_validator(mode="after")
    def valid_subtype(self):
        if len(set(self.verification_classes)) != len(self.verification_classes):
            raise ValueError("verification classes must be unique")
        if VerificationClass.unknown in self.verification_classes and len(self.verification_classes) > 1:
            raise ValueError("unknown verification class cannot accompany a known class")
        if self.subtype not in TYPOLOGY[self.category]:
            raise ValueError("subtype does not belong to category")
        if self.country_code and (len(self.country_code) != 2 or not self.country_code.isalpha()):
            raise ValueError("country_code must be an ISO alpha-2 code")
        return self


class Relationship(Model):
    subject: str
    relation: Literal["part_of", "located_in", "near"]
    object: str
    evidence: Mention


class Extraction(Model):
    entities: list[Entity]
    relationships: list[Relationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_ids(self):
        ids = [e.id for e in self.entities]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate entity IDs")
        for r in self.relationships:
            if r.subject not in ids or r.object not in ids or r.subject == r.object:
                raise ValueError("invalid relationship reference")
        return self


class Document(Model):
    name: str
    text: str


class Point(Model):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]

    @model_validator(mode="after")
    def ranges(self):
        import math
        lon, lat = self.coordinates
        if not all(math.isfinite(x) for x in (lon, lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("invalid WGS84 longitude/latitude")
        return self


class Candidate(Model):
    name: str
    category: Category
    subtype: str
    country_code: str | None = None
    jurisdiction: str | None = None
    identifiers: list[Identifier] = Field(default_factory=list)
    geometry: Point | None = None
    geometry_basis: Literal["exact_site", "representative_point", "unknown"] = "unknown"
    source: str
    source_record_id: str
    source_record_url: str | None = None
    independence: Literal["independent", "same_origin", "unknown"] = "unknown"
    evidence: dict[str, str] = Field(default_factory=dict)


class Issue(Model):
    code: str
    message: str
    stage: str
    entity_id: str | None = None
    retryable: bool = False


class VerificationResult(Model):
    workflow: str
    status: Literal["confirmed", "supported", "unverified", "conflicting", "unavailable", "no_match"]
    identity: Literal["confirmed", "supported", "unverified"] = "unverified"
    geometry: Point | None = None
    geometry_basis: str | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    selected_candidate: Candidate | None = None
    independently_confirmed: bool = False
    issues: list[Issue] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    candidate_assessments: list[dict] = Field(default_factory=list)


class GeometryProvenance(Model):
    origin: Literal["document", "external"]
    workflow: str
    source: str | None = None
    source_record_id: str | None = None
    source_record_url: str | None = None
    evidence: Mention | None = None
    crs: str | None = None
    source_crs: str | None = None
    crs_status: Literal["assumed", "stated", "transformed"] | None = None
    transformation: dict = Field(default_factory=dict)


class Resolution(Model):
    entity: Entity
    status: Literal["resolved", "unresolved", "excluded", "conflicting"]
    reason: str | None = None
    geometry: Point | None = None
    geometry_basis: str | None = None
    geometry_provenance: GeometryProvenance | None = None
    independently_confirmed: bool = False
    verification: list[VerificationResult] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    resolution_state: str | None = None
    flagged: bool = False
    review_flags: list[dict] = Field(default_factory=list)


class Usage(Model):
    geocoder_refinement_requests: int = 0
    geographic_audit_requests: int = 0
    web_requests: int = 0
    verification_requests: int = 0
    web_search_tool_calls: int = 0
    model_calls: int = 0
    source_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    model_calls_without_usage: int = 0


class CostEstimate(Model):
    currency: Literal["USD"] = "USD"
    status: Literal["estimated", "incomplete", "unavailable"]
    total_usd: float | None = None
    token_cost_usd: float = 0
    web_search_cost_usd: float = 0
    pricing_as_of: str
    pricing_source: str
    local_cache_hits: int = 0
    calls: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RunResult(Model):
    request_id: str
    status: Literal["completed", "partial", "failed"]
    geojson: dict
    entities: list[Resolution]
    relationships: list[Relationship]
    issues: list[Issue]
    usage: Usage
    raw_llm_responses: list[dict] = Field(default_factory=list)
    estimated_cost: CostEstimate | None = None
    workflow_version: str | None = None
    resumed_from: str | None = None
    progress: dict = Field(default_factory=dict)
    limits: dict = Field(default_factory=dict)
    semantic_unit_extents: list[dict] = Field(default_factory=list)
