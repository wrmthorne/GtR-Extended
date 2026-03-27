# GtR-Extended

Accompanying repository to the paper "Demystifying Funding: Reconstructing a Unified Dataset of the UK Funding Lifecycle", published at NSLP 2026. The database integrates data from the [UKRI Gateway to Research (GtR) API](https://gtr.ukri.org/), [UKRI Panel Meetings and Attendance](https://github.com/wrmthorne/UKRI-Panel-Meetings-and-Attendance), and UKRI funding opportunity pages into a unified structure. To our knowledge this is the first time these sources have been brought together, completing the UKRI funding lifecycle.

## Entities

### GtR API

- **Projects** with taxonomies (research topics, subjects, programmes)
- **Funds** linked to projects
- **Organisations** with addresses
- **Persons** with ORCID identifiers
- **Project members** (PI, Co-I, fellow, etc.)
- **Project partners** (lead org, collaborator, co-funder, etc.)
- **Person-organisation links** (employment relationships)
- **Project relationships** (studentships, transfers between projects)
- **Outcomes**: publications, key findings, impact summaries, collaborations, disseminations, further funding, intellectual property, policy influences, products, research materials, artistic/creative products, research databases/models, software/technical products, spin-outs

### Meetings

Sourced from the [UKRI Panel Meetings and Attendance](https://github.com/wrmthorne/UKRI-Panel-Meetings-and-Attendance) dataset:

- **Meetings** with panel reference, council, dates, convenor
- **Applications** with applicant, organisation, award details
- **Meeting-application links** with rank, outcome, score, awarded amount
- **Panel attendance** with panellist name, organisation, role

### Opportunities

Funding opportunity pages scraped from UKRI, with AI-extracted metadata including funding amounts, durations, funders, and dates.

## Entity Linking

`link.py` creates cross-entity links that cannot be derived from a single data source.

### Application to Project (`--app-proj`)

Exact case-insensitive match of `application_id` or `award_id` against `grant_reference`.

### Application to Opportunity (`--app-opp`)

Fuzzy title matching with constraints:

- Candidate text is `opportunity_name` from the application, falling back to the meeting name
- Fuzzy ratio scored against opportunity title (raw and with "Funding opportunity: " prefix stripped)
- Council filter: meeting council(s) must overlap opportunity funder codes
- Date filter: earliest meeting must be on or after opportunity opening date
- Score boosted +0.1 if application route matches opportunity funding type
- Score penalised -0.15 if total awarded amount falls outside the opportunity's min/max award range
- Minimum score threshold: 0.65

### Project to Opportunity (`--proj-opp`)

Propagation: project inherits `opportunity_id` from its linked application.

### Panel Attendance to Person (`--pan-per`)

Two-phase process:

1. **Disambiguation**: panel attendance records are clustered by (surname, initial) within each council. Sub-clusters are split when full first names conflict or organisation similarity falls below 0.7.
2. **GtR alignment**: each cluster is matched to GtR persons (those with project membership records):
   - Single candidate for surname+initial: linked directly
   - Multiple candidates with organisation info: best organisation similarity must exceed 0.6 with a margin of 0.15 over the second best
   - Multiple candidates with full first name: linked if exactly one candidate matches

## Setup

Requires Python >= 3.13 and docker

```bash
uv sync
```

Create the database:

```bash
# Once ingested, to load just the database, append "postgres"
docker compose up -d
```

## Usage

Place data under `./data_cache/` with subdirectories: `projects/`, `funds/`, `organisations/`, `persons/`, `outcomes/`, `meetings/`, and `opportunities/extracted_opportunities.jsonl`.

Ingest all data:

```bash
uv run python -m gtr_database.ingest
```

Create cross-entity links:

```bash
uv run python -m gtr_database.link
```

Both commands accept flags to run specific subsets (e.g. `--projects`, `--app-proj`). Run with `--help` for details.

```bash
uv run python -m gtr_database.validate
```

Validate runs data correctness checks against the matches made and the general quality of the stored data.