# AI Engineering: Georeferencer

**Time budget: ~4 hours. Please do not exceed this.**

This is intentionally a **proof of concept**, and a **starting point for a deeper discussion** with us — not a finished
system. We are not looking for an end product, exhaustive coverage, or polished results: a thoughtful design and a POC
that runs on a narrow slice tell us everything we need. We will walk through your submission together afterwards and
discuss how you'd take it further.

## 1. Overview

The goal of this challenge is to design and partially implement an autonomous agent — **Georeferencer** — that extracts
geographic references from heterogeneous documents (PDFs, HTML, images) and emits clean, validated **GeoJSON**.

This challenge focuses on AI workflow design: multi-step reasoning, tool use, self-correction, and structured output
generation. You are not expected to train models — focus on orchestrating existing ones well.

---

## 2. Document landscape

Geographic references in real-world documents are messy. A single press release might mention:

- Named projects without coordinates ("the Pike Zone at West McArthur")
- Coordinates in mixed projections (UTM, lat/lon, local grids)
- Place names that collide across jurisdictions (dozens of "Eagle" projects worldwide)
- Embedded maps that visually convey location but contain no machine-readable text
- Boilerplate noise (publisher's HQ address, regulatory disclaimers)

Examples to experiment with:

- Short-form press releases:
  [CanAlaska / Pike Zone](https://www.newsfilecorp.com/release/226247/CanAlaska-Assays-Confirm-Ultra-HighGrade-Uranium-Mineralization-at-Pike-Zone-on-West-McArthur-Joint-Venture),
  [Glenburgh gold intercepts](https://www.newsfilecorp.com/release/261117/200m-Gold-Intercepts-Support-Glenburghs-Emergence-as-a-Major-Gold-System),
  [Pan Global Spain projects](https://www.juniorminingnetwork.com/junior-miner-news/press-releases/2116-tsx-venture/pgz/184614-pan-global-advances-drill-programs-at-escacena-and-carmenes-projects-spain.html),
  [Abcourt Mines / Flordin drilling campaign](https://www.juniorminingnetwork.com/junior-miner-news/press-releases/857-tsx-venture/abi/196640-abcourt-resumes-drilling-on-its-flordin-property-with-a-20-000-metre-campaign.html)
- Long-form documents with embedded figures:
  [Pyrenees Field Trip Guide (MedGU 2024)](https://2024.medgu.org/source/files/Pyrenees-Fieldtrip-Guide-MedGU-2024.pdf)

Download these yourself and feed them to your agent as PDF or text — fetching documents is not part of the challenge.
Feel free to bring your own material.

---

## 3. Challenge: design & technical POC

Pick **one** of the two tracks below. Both are weighted equally — choose the one you find more interesting.

We'd like to see two things, of roughly equal importance:

1. **A design** of the full system on paper (in the README, or as a diagram via Excalidraw, Google Drawings, or
   similar) — the agent architecture you'd build if this were going to production, addressing the focus areas listed in
   section 4.
2. **A partial implementation** — a runnable POC covering part of the track. You're welcome to go end-to-end on a narrow
   slice, or go deep on a specific building block (e.g., for Track B: map detection, legend/scale extraction, or the
   georeferencing logic itself). Be explicit about what you built vs. what you'd build.

### Track A — Textual georeferencing

Build an agent that:

- Ingests multi-format documents (PDF, HTML, plain text — pick what's tractable)
- Extracts geographic references (place names, coordinates, addresses, project names)
- Validates and enriches them against external sources (geocoder, search, gazetteer)
- Resolves conflicts (duplicates, ambiguous names, mismatched coordinates, irrelevant mentions)
- Outputs one **GeoJSON `FeatureCollection`** per document, conforming to
  [RFC 7946](https://tools.ietf.org/html/rfc7946)

Example: a document containing _"Welcome to the Louvre Museum!"_ should yield:

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [2.3364, 48.8606]
      },
      "properties": {
        "name": "Louvre",
        "mention": "Welcome to the Louvre Museum!",
        "location_type": "Museum"
      }
    }
  ]
}
```

### Track B — Map georeferencing

Documents often contain embedded maps — geological cross-sections, claim boundary diagrams, drill collar plots — that
carry rich spatial information but are images, not data. Build an agent that:

- Ingests documents containing maps (PDF pages, standalone images)
- Detects and isolates map figures (vs. photos, charts, logos)
- Extracts spatial reference signals from the map itself: title, scale bar, north arrow, coordinate gridlines, labelled
  features, legend
- Attempts to georeference the map — at minimum producing a bounding box, ideally with a confidence score and the cues
  used
- Outputs one **GeoJSON `FeatureCollection`** per document, with one `Feature` per map: a `Polygon` geometry for the
  map extent and extracted metadata in `properties`

Vision-capable LLMs, OCR, and classical CV are all fair game.

---

## 4. Stack & deliverables

- **Language**: Python.
- **Tools**: you are free to add any libraries, external APIs, or model providers. If you'd like an OpenAI or Anthropic
  API key, ask us.
- **Models**: use whatever you like — we care about the system, not the weights.
- **AI coding assistants**: feel free to use Claude Code, Codex, Cursor, or whatever you usually work with.

#### Submission Checklist:

**Code**: your packaged project with a clear `README.md` including setup and run instructions.

**Runnable system**: CLI, API, or web UI — your choice.

**Documentation**: a georeferencing agent that works on one document is a demo. One that works across thousands of
heterogeneous documents — with bounded cost, predictable latency, and graceful failure — is a system. In your README,
cover which track you chose, your assumptions and shortcuts, and how you'd extend the POC into that system. We're
particularly interested in your thinking on:

- **agent design**: how do you structure the reasoning loop? Where do you draw the line between deterministic pipeline
  steps and LLM-driven decisions?
- **tool use & enrichment**: which external tools (geocoders, gazetteers, search engines, vision models, OCR) does the
  agent need? How do you keep it from spiralling on cost or hallucinating its way past validation?
- **self-correction & confidence**: how does the system detect that an extraction is wrong — ambiguous name, projection
  mismatch, location in the wrong hemisphere, a "map" that's actually a logo — and recover?
- **observability**: where do you implement monitoring to catch silent quality regressions (a prompt change halves
  recall, a tool starts returning stale data, a vision model drifts on a new document layout)?
- **evaluation**: how would you measure precision and recall on this task without a ground-truth dataset?

We don't expect deep answers on all five — pick the two or three that connect most naturally to the choices you made and
go deep there.

**Time budget**: ~4 hours — please do not exceed this. Focus on the core reasoning patterns rather than exhaustive
coverage of your chosen track. Shortcuts are expected: just tell us which ones you took and why.

#### What we're looking for

- **Sensible design choices** and clear, readable code over any performance number.
- **Your reasoning**, written down: what you built, what you'd build next, and the trade-offs you see. This, plus the
  discussion, is the real deliverable.

We are explicitly **not** looking for a production-ready system, broad document coverage, or high extraction accuracy.
Think of this as the seed for a conversation rather than a competition.

#### How to submit

Send us either a compressed archive (`.zip`, `.tar.gz`) via email or a link to a repository (GitHub, GitLab, etc.) with
everything needed to run the project.
