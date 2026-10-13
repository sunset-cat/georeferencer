# Proposed location nomenclature v2

**Superseded:** the user chose a smaller vocabulary of verification source families.
The implementation now asks the LLM for `verification_classes` metadata using
`geographic_place`, `mineral_tenure`, `drilling_site`, `geological_feature`,
`mineral_site`, and the fallback `unknown`. Multiple applicable classes are allowed.
The detailed 40-type proposal below is retained as research history, not the chosen
nomenclature or active routing schema.

Proposal only: the application's active extraction schema remains v1 (18 subtypes).
This proposal uses broad, fixed types with optional controlled qualifiers. It aims to
cover common mining/geology documents; it cannot guarantee every possible location.

## Three representative default documents

The local library retains originals, extracted text, persisted extraction/verification
results, GeoJSON, and run history. Startup loads saved results; it makes no model calls.
Other default documents were removed following the user's scope change. User uploads
were preserved. These three sources are a coverage sample, not an extraction benchmark.

| Document | Why keep it | Location examples and observed needs |
|---|---|---|
| [ROB claims geological report and offering statement, 1988](https://propertyfile.gov.bc.ca/reports/PF019021.pdf) | Historical technical/claims report combined with a securities offering; noisy OCR and maps | Rob 17/19/20/21 claims, Liard Mining Division, Iskut River, Bronson Creek airstrip, Stewart-Cassiar Highway, Bowser Basin, Coast Plutonic Complex, Unuk River Formation, King Salmon Thrust Fault, S-71 hole, soil sample sites. Separate claims, natural features, geological units, structures, access infrastructure, and observations. |
| [BCGS GeoFile 2025-11: assessment report drillhole database](https://cmscontent.nrs.gov.bc.ca/geoscience/publicationcatalogue/GeoFile/BCGS_GF2025-11.pdf) | Survey publication describing real exploration data and their quality checks | Collar positions, original CRS/datum, downhole geometry, samples, and location methods. These are distinct spatial claims, not automatically interchangeable latitude/longitude pairs. Schema field names and generic drilling examples are not named locations. |
| [CanAlaska West McArthur summer drilling release, June 2025](https://www.canalaska.com/wp-content/uploads/2025/06/20250617-West-McArthur-Summer-Program-Start-FINAL.docx.pdf) | Short contemporary exploration release, similar to the interview input | West McArthur project, Pike Zone, Athabasca Basin, C10S Corridor, WMA079-01 and WMA074-04, Saskatchewan, corporate/contact cities. Separate project, zone, basin, target corridor and scoped hole identifiers; assay grades and lengths are not coordinates. |

## Fixed categories and types (40)

The LLM selects one type; code derives its category. Qualifiers refine the type without
creating hundreds of narrowly defined labels. Unknown qualifiers remain null.

| Category | Fixed types | Qualifier examples | Preferred verification |
|---|---|---|---|
| `geographic` | `administrative_area`, `settlement`, `geographic_region`, `landform`, `watercourse`, `waterbody`, `drainage_basin`, `protected_area`, `coastal_feature` | administrative level/mining division; mountain/ridge/valley/volcano/glacier; river/creek; lake/reservoir; bay/island | Official gazetteers, administrative/topographic datasets; geocoder candidates |
| `geological` | `geological_unit`, `intrusion`, `geological_structure`, `geological_basin`, `geological_province`, `mineral_belt`, `geological_site` | unit rank: group/formation/member/bed; intrusion kind: batholith/pluton/dyke/sill; structure: fault/shear/fold/contact/unconformity; site: outcrop/type locality | Geological surveys, stratigraphic lexicons, mapped geological features |
| `mining` | `project`, `property`, `exploration_target`, `mineral_occurrence`, `mineral_deposit`, `mineralized_zone`, `mine`, `mine_working` | target corridor/anomaly; showing; deposit commodity/model; working: pit/quarry/shaft/adit/stope | Mine/deposit/occurrence inventories, technical reports, operator datasets |
| `tenure` | `mineral_claim`, `mineral_tenure`, `permit_area`, `land_parcel` | lode/placer claim; exploration/mining licence, lease, concession; legal subdivision | Mining cadastral and permit registries, land records |
| `infrastructure` | `transport_route`, `transport_facility`, `processing_facility`, `mine_waste_facility`, `utility_facility` | road/street/rail; airstrip/port; mill/smelter/refinery; tailings/dump; dam/pipeline/power line | Operator plans, environmental assessment footprints, infrastructure datasets |
| `observation` | `borehole`, `sampling_site`, `survey_station`, `survey_area`, `survey_line` | hole purpose: exploration/production/monitoring; sample medium: rock/soil/sediment/water; station/grid/traverse | Collar, sampling and survey tables, assessment-report datasets |
| `other` | `facility`, `unknown` | office/laboratory/camp/building when no more specific class fits | Appropriate facility source or manual review |

Protected areas, coastal features and several infrastructure kinds extend beyond the
three-document sample. They come from the broader source review; they are proposed
coverage extensions, not examples claimed to occur in all three defaults.

## Classification rules

1. Classify the object described, not a keyword in its name. A named mineral deposit
   is not necessarily a producing mine. A shareholder-meeting location remains a place.
2. A legal claim/lease and a physical project are separate entities. A permit without a
   spatial scope is a legal document/identifier, not an independent geographic feature.
3. Geological basins differ from drainage basins and topographic depressions. Use
   geological context to choose `geological_basin`; never infer basin kind from its name
   alone. Retain ambiguous candidates with classification uncertainty.
4. Group/formation/member/bed are ranks of `geological_unit`. Use ranks and source
   identifiers rather than forcing all named units into `formation`.
5. A mineralized vein can have structural and mineralization attributes. Avoid producing
   duplicate entities merely because two descriptions apply to the same mapped object.
6. Named/identified targets and sites are extractable; generic mentions of a fault,
   a drilling method, rock type, laboratory process, or a schema column are not.
7. Survey grid names, map sheets and PLSS township/range/section references are spatial
   references first. Create a `land_parcel`/`survey_area` entity only when a real footprint
   is the referent. Otherwise preserve them in spatial claims attached to the asset.
8. An assay percentage, interval length, elevation or downhole depth is not a surface
   longitude/latitude. DMS, UTM and local-grid coordinates need parsing/CRS support.

## Roles and relevance

Keep type, document role, verification state and geometry state separate. Retain the
existing roles initially for compatibility; add `legal_context` and `operational_context`
to distinguish legal continuance from project surroundings and access infrastructure.
Prefer roles on individual mentions, with an entity-level set/summary: Alberta can occur
in both a meeting address and legal jurisdiction without becoming two Alberta entities.

Role should drive display/export policy, not silently prevent identity lookup. The UI
should show extracted entities, explicit reasons for export exclusion, and selected role
filters. A verified corporate city can be excluded from mining-site GeoJSON without
pretending that the city could not be located.

## Geometry and source provenance

- Types do not prescribe a single geometry. A borehole has a collar point and possibly
  a trajectory; a fault may be a line or zone; a basin, claim and protected area have extents.
- Preserve Point/LineString/Polygon/Multi* when supported. Do not manufacture polygons
  or treat a geocoder's province centroid as a geological basin or project boundary.
- Distinguish `collar`, `trajectory`, `boundary`, `footprint`, `representative_point`,
  `sample_position`, and `unknown` geometry meanings. Attach the meaning to each claim.
- Keep original coordinate representation, CRS/datum, axis order, units, transformation
  method and evidence. Missing coordinates remain missing until a suitable source matches.
- Identity confirmation and geometry confirmation are separate. Record source record IDs,
  URLs, date/version, and provenance; a report copied into a registry is not automatically
  independent corroboration.
- Namespace short hole/sample identifiers with project/dataset context. Do not merge
  identically named projects, holes or zones using a name or proximity alone.

## Migration from v1

| Current type | Proposed type + qualifier |
|---|---|
| `mountain`, `volcano` | `landform`, kind mountain/volcano |
| `river`, `lake` | `watercourse` kind river; `waterbody` kind lake |
| `formation`, `geological_unit` | `geological_unit`, rank formation where supported |
| `fault` | `geological_structure`, kind fault |
| `permit` | `permit_area` when spatial; otherwise legal metadata |
| `drill_hole` | `borehole`, purpose exploration |
| `project`, `property`, `mine`, `mineralized_zone`, `intrusion` | Same type, with proposed category mapping |
| `settlement`, `administrative_area`, `facility`, `unknown` | Same type |

Version the extraction schema and verification adapters together. Existing saved reports
retain their v1 labels; migrating a report is explicit. New types require corresponding
provider mappings and acceptance rules, rather than simply expanding the LLM enum.

## Supporting references

- [GeoNames feature codes](https://www.geonames.org/export/codes.html): general geographic feature diversity.
- [BGS Lexicon of Named Rock Units](https://www.bgs.ac.uk/technologies/the-bgs-lexicon-of-named-rock-units/): rank, synonyms, parent units, geographic extent and persistent identifiers.
- [GeoSciML GeologicUnit model](https://geosciml.org/doc/geosciml/2.0_rc2/documentation/html/GeoSciML/GeologicUnit/GeologicUnit.html): geological identity distinguished from mapped occurrences. This is an older conceptual-model reference, not a claim of current API conformance.
- [BLM mining claim recording](https://www.blm.gov/programs/energy-and-minerals/mining-and-minerals/locatable-minerals/mining-claims/recording): claim/site kinds and land-description references.
- [USGS USMIN](https://www.usgs.gov/centers/gggsc/science/usmin-mineral-deposit-database): distinct mine/deposit/district and mine-working/waste features.

This is an application vocabulary informed by these sources, not an official standard
or a manually labelled gold dataset. Saved LLM results still need semantic review.
