import argparse
import json
import os
import re
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL
from decimal import Decimal, InvalidOperation
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta

import httpx
from sqlalchemy import create_engine, text, DateTime, Date
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .schema import (
    Base, Project, ProjectTaxonomy, ProjectMember, ProjectPartner,
    ProjectFund, Organisation, Outcome, Person, Address,
    Publication, KeyFinding, ImpactSummary, Collaboration,
    Dissemination, FurtherFunding, IntellectualProperty,
    PolicyInfluence, Product, ResearchMaterial,
    ArtisticAndCreativeProduct, ResearchDatabaseAndModel,
    SoftwareAndTechnicalProduct, SpinOut,
    Meeting, MeetingApplication, PanelAttendance, Application,
    Opportunity, PersonOrganisationLink, ProjectRelationship,
)

DATABASE_URL = "postgresql+psycopg://gtr:gtr@localhost:5432/gtr"
PROTEUS_URL = "http://localhost:4923/proteus/transform"
SPECS_DIR = Path(".").resolve() / "specs"

SPEC_MAP = {
    "project": "project_spec.json",
    "fund": "fund_spec.json",
    "organisation": "organisation_spec.json",
    "person": "person_spec.json",
    "publication": "outcome_spec.json",
    "keyFinding": "outcome_spec.json",
    "impactSummary": "outcome_spec.json",
    "collaboration": "outcome_spec.json",
    "dissemination": "outcome_spec.json",
    "futherfunding": "outcome_spec.json",
    "intellectualProperty": "outcome_spec.json",
    "policyInfluence": "outcome_spec.json",
    "product": "outcome_spec.json",
    "researchMaterial": "outcome_spec.json",
    "artisticAndCreativeProduct": "outcome_spec.json",
    "researchDatabaseAndModel": "outcome_spec.json",
    "softwareAndTechnicalProduct": "outcome_spec.json",
    "spinOut": "outcome_spec.json",
}

SUBTYPE_MAP = {
    "publication": (Publication, "publication"),
    "keyFinding": (KeyFinding, "key_finding"),
    "impactSummary": (ImpactSummary, "impact_summary"),
    "collaboration": (Collaboration, "collaboration"),
    "dissemination": (Dissemination, "dissemination"),
    "futherfunding": (FurtherFunding, "further_funding"),
    "intellectualProperty": (IntellectualProperty, "intellectual_property"),
    "policyInfluence": (PolicyInfluence, "policy_influence"),
    "product": (Product, "product"),
    "researchMaterial": (ResearchMaterial, "research_material"),
    "artisticAndCreativeProduct": (ArtisticAndCreativeProduct, "artistic_and_creative_product"),
    "researchDatabaseAndModel": (ResearchDatabaseAndModel, "research_database_and_model"),
    "softwareAndTechnicalProduct": (SoftwareAndTechnicalProduct, "software_and_technical_product"),
    "spinOut": (SpinOut, "spin_out"),
}

PERSON_ROLES = {"PI_PER", "COI_PER", "PM_PER", "FELLOW_PER", "RESEARCH_PER", "SUPER_PER", "RESEARCH_COI_PER",
                "TGH_PER", "STUDENT_PER"}
ORG_ROLES = {"LEAD_ORG", "COLLAB_ORG", "FELLOW_ORG", "COFUND_ORG", "PP_ORG", "FUNDER", "PARTICIPANT_ORG",
             "STUDENT_PP_ORG"}
PROJECT_RELS = {"STUDENTSHIP", "STUDENTSHIP_FROM", "TRANSFER", "TRANSFER_FROM"}
OUTCOME_RELS = {
    "PUBLICATION", "KEY_FINDING", "IMPACT_SUMMARY", "COLLABORATION",
    "DISSEMINATION", "FURTHER_FUNDING", "IP", "POLICY",
    "RESEARCH_DATABASE_AND_MODEL", "RESEARCH_MATERIAL",
    "SOFTWARE_AND_TECHNICAL_PRODUCT", "ARTISTIC_AND_CREATIVE_PRODUCT",
    "PRODUCT", "SPIN_OUT",
}

# Directories under data_cache for each flag
GTR_ENDPOINT_DIRS = {
    "projects": ["projects"],
    "funds": ["funds"],
    "organisations": ["organisations"],
    "persons": ["persons"],
    "outcomes": [
        "outcomes/publications", "outcomes/keyfindings", "outcomes/impactsummaries",
        "outcomes/collaborations", "outcomes/disseminations", "outcomes/furtherfundings",
        "outcomes/intellectualproperties", "outcomes/policyinfluences", "outcomes/products",
        "outcomes/researchmaterials", "outcomes/artisticandcreativeproducts",
        "outcomes/researchdatabaseandmodels", "outcomes/softwareandtechnicalproducts",
        "outcomes/spinouts",
    ],
}

UUID_RE = re.compile(r".*/([0-9A-Fa-f-]{36})(?:\?.*)?$")

engine = create_engine(
    DATABASE_URL,
    pool_size=32,
    max_overflow=10,
    pool_timeout=30
)
SessionLocal = sessionmaker(bind=engine)


# ===== Utilities ===== #

def extract_id(href: str | None) -> UUID | None:
    if not href: return None
    m = UUID_RE.search(href)
    return UUID(m.group(1)) if m else None


def det_uuid(*parts: str | UUID) -> UUID:
    return uuid5(NAMESPACE_URL, ":".join(str(p) for p in parts))


def _parse_int(val: str | None) -> int | None:
    if not val:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _parse_decimal(val: str | None) -> Decimal | None:
    if not val:
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_datetime(val: str | None) -> datetime | None:
    if not val or not val.strip():
        return None
    val = val.strip()
    # Try ISO 8601 with timezone first (e.g. 2023-05-23T00:00:00+01:00)
    try:
        return datetime.fromisoformat(val).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m",
                "%Y%m%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None





# ===== Proteus Client ===== #

class ProteusClient:

    def __init__(self, url: str):
        self.url = url
        self._spec_cache = {}
        self._local = __import__("threading").local()

    def _get_http(self) -> httpx.Client:
        if not hasattr(self._local, "http"):
            self._local.http = httpx.Client(timeout=120)
        return self._local.http

    def transform(self, input_data: dict, spec_name: str) -> list[dict]:
        if spec_name not in self._spec_cache:
            spec_path = SPECS_DIR / spec_name
            self._spec_cache[spec_name] = json.loads(spec_path.read_text())

        resp = self._get_http().post(self.url, json={
            "spec": self._spec_cache[spec_name],
            "input": input_data,
            "config": {"path_not_found_to_null": True},
        })
        resp.raise_for_status()
        return resp.json()

    def close(self):
        pass


# ===== Database Utilities ===== #

def _sanitize_value(v, col_type=None):
    if v is None:
        return None
    if isinstance(v, int) and isinstance(col_type, (DateTime, Date)):
        try:
            if v > 10 ** 12:
                return datetime.fromtimestamp(v / 1000.0)
            return datetime.fromtimestamp(v)
        except:
            return None
    if isinstance(v, (datetime, date)):
        return v
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return json.dumps(v) if v else None
    return str(v)


def bulk_upsert(session: Session, table, records: list[dict], conflict_keys=("id",), do_update=True,
                batch_size: int = 4000):
    if not records:
        return

    type_map = {c.name: c.type for c in table.__table__.columns}

    cleaned_records = []
    for r in records:
        row = {k: _sanitize_value(v, type_map.get(k))
               for k, v in r.items() if k in type_map}
        if row.get("id"):
            cleaned_records.append(row)

    if not cleaned_records:
        return

    all_keys = set().union(*(r.keys() for r in cleaned_records))
    for r in cleaned_records:
        for k in all_keys:
            r.setdefault(k, None)

    seen = {}
    for r in cleaned_records:
        key = tuple(r[k] for k in conflict_keys)
        seen[key] = r
    cleaned_records = list(seen.values())

    for i in range(0, len(cleaned_records), batch_size):
        batch = cleaned_records[i:i + batch_size]
        stmt = pg_insert(table).values(batch)
        if do_update:
            update_dict = {c: stmt.excluded[c] for c in batch[0].keys() if c not in conflict_keys}
            stmt = stmt.on_conflict_do_update(index_elements=conflict_keys, set_=update_dict)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_keys)

        session.execute(stmt)


# ===== GtR API Data Ingestion ===== #

def process_batch(session: Session, transformed: list):
    data_map = {
        Project: [], ProjectTaxonomy: [], ProjectMember: [], ProjectPartner: [],
        ProjectFund: [], Organisation: [], Outcome: [], Person: [], Address: [],
        PersonOrganisationLink: [], ProjectRelationship: [],
    }
    for model_class, _ in SUBTYPE_MAP.values():
        data_map[model_class] = []

    outcome_project_pairs = []

    for item in transformed:
        if not isinstance(item, dict):
            continue

        p = item.get("project")
        if p and isinstance(p, dict):
            try:
                pid = UUID(p["id"])

                for ident in item.get("identifiers") or []:
                    if isinstance(ident, dict) and ident.get("identifier_type") == "RCUK":
                        ref = ident.get("identifier_value")
                        if ref:
                            p["grant_reference"] = ref
                            break

                data_map[Project].append(p)

                for t in item.get("taxonomies") or []:
                    if isinstance(t, dict):
                        t["id"] = det_uuid(pid, t.get("taxonomy_type"), t.get("label"))
                        t["project_id"] = pid
                        data_map[ProjectTaxonomy].append(t)

                for link in item.get("links") or []:
                    if not isinstance(link, dict):
                        continue
                    target_id = extract_id(link.get("href"))
                    rel = link.get("rel")
                    if not target_id or not rel:
                        continue

                    if rel in PERSON_ROLES:
                        data_map[ProjectMember].append({
                            "id": det_uuid(pid, target_id, rel),
                            "project_id": pid,
                            "person_id": target_id,
                            "role": rel
                        })
                    elif rel in ORG_ROLES:
                        data_map[ProjectPartner].append({
                            "id": det_uuid(pid, target_id, rel),
                            "project_id": pid,
                            "organisation_id": target_id,
                            "role": rel
                        })
                    elif rel in PROJECT_RELS:
                        data_map[ProjectRelationship].append({
                            "id": det_uuid(pid, target_id, rel),
                            "from_project_id": pid,
                            "to_project_id": target_id,
                            "relationship_type": rel,
                            "start_date": _parse_datetime(link.get("start_date")),
                            "end_date": _parse_datetime(link.get("end_date")),
                        })
                    elif rel in OUTCOME_RELS:
                        outcome_project_pairs.append({
                            "oid": str(target_id), "pid": str(pid)
                        })
            except (KeyError, ValueError, TypeError) as e:
                print(f"WARN: Failed to process project record: {e}")

        if f := item.get("fund"):
            if isinstance(f, dict):
                for link in item.get("links") or []:
                    if isinstance(link, dict) and link.get("rel") == "FUNDED":
                        f["project_id"] = extract_id(link.get("href"))
                        break
                if f.get("project_id"):
                    data_map[ProjectFund].append(f)
                else:
                    print("WARN: Funding record is missing project_id")

        if o := item.get("organisation"):
            if isinstance(o, dict):
                org_id = UUID(o["id"])
                data_map[Organisation].append(o)
                for addr in o.get("addresses") or []:
                    if isinstance(addr, dict):
                        addr["id"] = det_uuid(o["id"], addr.get("post_code", ""), addr.get("line1", ""))
                        addr["organisation_id"] = org_id
                        data_map[Address].append(addr)
                for link in item.get("links") or []:
                    if not isinstance(link, dict):
                        continue
                    rel = link.get("rel")
                    target_id = extract_id(link.get("href"))
                    if rel == "EMPLOYEE" and target_id:
                        start_millis = link.get("start_millis")
                        end_millis = link.get("end_millis")
                        data_map[PersonOrganisationLink].append({
                            "id": det_uuid(target_id, org_id, "EMPLOYED"),
                            "person_id": target_id,
                            "organisation_id": org_id,
                            "role": "EMPLOYED",
                            "start_date": datetime.fromtimestamp(start_millis / 1000.0).date() if start_millis else None,
                            "end_date": datetime.fromtimestamp(end_millis / 1000.0).date() if end_millis else None,
                        })

        if pers := item.get("person"):
            if isinstance(pers, dict):
                data_map[Person].append(pers)
                person_id = UUID(pers["id"])
                for link in item.get("links") or []:
                    if not isinstance(link, dict):
                        continue
                    rel = link.get("rel")
                    target_id = extract_id(link.get("href"))
                    if rel == "EMPLOYED" and target_id:
                        start_millis = link.get("start_millis")
                        end_millis = link.get("end_millis")
                        data_map[PersonOrganisationLink].append({
                            "id": det_uuid(person_id, target_id, "EMPLOYED"),
                            "person_id": person_id,
                            "organisation_id": target_id,
                            "role": "EMPLOYED",
                            "start_date": datetime.fromtimestamp(start_millis / 1000.0).date() if start_millis else None,
                            "end_date": datetime.fromtimestamp(end_millis / 1000.0).date() if end_millis else None,
                        })

        if oc_base := item.get("outcome"):
            for link in item.get("links") or []:
                if isinstance(link, dict) and link.get("rel") == "PROJECT":
                    proj_id = extract_id(link.get("href"))
                    if proj_id:
                        oc_base["project_id"] = proj_id
                        break

            data_map[Outcome].append(oc_base)

            otype = oc_base.get("outcome_type")
            if entry := SUBTYPE_MAP.get(otype):
                model_class, item_key = entry
                if subtype_data := item.get(item_key):
                    subtype_data["id"] = oc_base["id"]
                    data_map[model_class].append(subtype_data)

    for table, records in data_map.items():
        if records:
            bulk_upsert(session, table, records)

    if outcome_project_pairs:
        for i in range(0, len(outcome_project_pairs), 5000):
            batch = outcome_project_pairs[i:i + 5000]
            session.execute(
                text("UPDATE outcomes SET project_id = CAST(:pid AS uuid) "
                     "WHERE id = CAST(:oid AS uuid) AND project_id IS NULL"),
                batch
            )


def worker(path: Path, proteus: ProteusClient):
    try:
        with SessionLocal() as session:
            session.execute(text("SET session_replication_role = 'replica'"))

            data = json.loads(path.read_text())
            spec_file = next((SPEC_MAP[k] for k in SPEC_MAP if k in data), None)
            if not spec_file:
                return f"SKIPPED: {path.name}, data is None? {data is None}"

            transformed = proteus.transform(data, spec_file)
            process_batch(session, transformed)
            session.commit()
            return f"SUCCESS: {path.name}"
    except Exception as e:
        return f"ERROR: {path.name} - {type(e).__name__}: {e}"


# ===== Meeting Ingestion ===== #

_MEETING_EXTRA_KEYS = {"count_of_applications", "count_of_awards", "count_of_deferred",
                       "notes", "meeting_convenor", "membership_source_files"}

_APP_EXTRA_KEYS = {"link", "route", "opportunity_number", "opportunity",
                   "type_of_application", "year", "rank_group", "notes",
                   "child_grant_reference", "panel"}


def process_meetings_json(session: Session, meetings_dir: Path):
    """Ingest meeting data from standardised JSON files under meetings_dir/{council}/*.json."""
    from collections import defaultdict

    meeting_records = []
    all_panellist_records = []
    app_map: dict[tuple[str, str], tuple[dict, dict]] = {}
    app_meetings: dict[tuple[str, str], list[tuple[UUID, dict]]] = defaultdict(list)

    council_dirs = sorted(p for p in meetings_dir.iterdir() if p.is_dir())
    print(f"Processing meetings from {len(council_dirs)} council directories...")

    for council_dir in council_dirs:
        json_files = sorted(council_dir.glob("*.json"))
        for jf in json_files:
            try:
                raw = json.loads(jf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                print(f"WARN: Skipping {jf}: {e}")
                continue

            council = (raw.get("council") or "").upper()
            panel_ref = raw.get("meeting_reference") or jf.stem
            source = raw.get("source")

            meeting_uuid = det_uuid("meeting", council, panel_ref)
            meeting_extra = {k: raw[k] for k in _MEETING_EXTRA_KEYS if raw.get(k)}

            meeting_records.append({
                "id": meeting_uuid,
                "council": council,
                "meeting_name": raw.get("meeting_name"),
                "panel_reference": panel_ref,
                "meeting_start": _parse_datetime(raw.get("meeting_start")),
                "meeting_end": _parse_datetime(raw.get("meeting_end")),
                "meeting_convenor": raw.get("meeting_convenor"),
                "year": _parse_int(raw.get("year")),
                "source": source,
                "extra": meeting_extra or None,
            })

            # Applications
            for app_raw in raw.get("applications", []):
                app_id = app_raw.get("application_id")
                if not app_id:
                    continue

                app_fields = {
                    "application_id": app_id,
                    "application_title": app_raw.get("title"),
                    "lead_applicant": app_raw.get("lead_applicant"),
                    "lead_organisation": app_raw.get("lead_organisation"),
                    "outcome": app_raw.get("outcome"),
                    "rank": _parse_int(app_raw.get("rank")),
                    "rank_group": app_raw.get("rank_group"),
                    "score": app_raw.get("score") or app_raw.get("score_range"),
                    "awarded_amount": _parse_decimal(
                        app_raw.get("awarded_amount") or app_raw.get("kaus_awarded")),
                    "award_id": app_raw.get("award_id"),
                }
                app_extra = {k: app_raw[k] for k in _APP_EXTRA_KEYS if app_raw.get(k)}

                key = (council, app_id)
                if key in app_map:
                    existing_fields, existing_extra = app_map[key]
                    for k, v in app_fields.items():
                        if v is not None and existing_fields.get(k) is None:
                            existing_fields[k] = v
                    existing_extra.update({k: v for k, v in app_extra.items() if v})
                else:
                    app_map[key] = (app_fields, app_extra)

                app_meetings[key].append((meeting_uuid, {
                    "rank": app_fields.get("rank"),
                    "rank_group": app_fields.get("rank_group"),
                    "outcome": app_fields.get("outcome"),
                    "awarded_amount": app_fields.get("awarded_amount"),
                    "score": app_fields.get("score"),
                    "source": source,
                    "extra": {k: v for k, v in app_extra.items() if v} or None,
                }))

            # PanelAttendances
            for pan_raw in raw.get("panellists", []):
                pan_name = pan_raw.get("name")
                if not pan_name:
                    continue
                pan_uuid = det_uuid("panellist", meeting_uuid, pan_name)
                all_panellist_records.append({
                    "id": pan_uuid,
                    "meeting_id": meeting_uuid,
                    "source_panellist_name": pan_name,
                    "organisation_name": pan_raw.get("organisation") or None,
                    "panel_role": pan_raw.get("role") or None,
                    "subpanel": pan_raw.get("subpanel") or None,
                    "council": council,
                    "source": source,
                })

    print(f"Parsed {len(meeting_records)} meetings, {len(app_map)} unique applications, "
          f"{len(all_panellist_records)} panellists")

    # ===== Build Application records ===== #
    app_records = []
    for (c, app_id), (fields, extra) in app_map.items():
        app_uuid = det_uuid("application", c, app_id)
        app_records.append({
            "id": app_uuid,
            "application_id": app_id,
            "application_title": fields.get("application_title"),
            "lead_applicant": fields.get("lead_applicant"),
            "lead_organisation": fields.get("lead_organisation"),
            "award_id": fields.get("award_id"),
            "opportunity_number": extra.get("opportunity_number"),
            "opportunity_name": extra.get("opportunity") ,
            "council": c,
            "year": _parse_int(extra.get("year")),
        })

    # ===== Build MeetingApplication records ===== #
    ma_records = []
    for (c, app_id), meetings in app_meetings.items():
        app_uuid = det_uuid("application", c, app_id)
        for meeting_uuid, ma_data in meetings:
            ma_uuid = det_uuid("meeting_application", str(meeting_uuid), str(app_uuid))
            ma_records.append({
                "id": ma_uuid,
                "meeting_id": meeting_uuid,
                "application_id": app_uuid,
                **ma_data,
            })

    # ===== Upsert in order ===== #
    if meeting_records:
        bulk_upsert(session, Meeting, meeting_records)
        print(f"Upserted {len(meeting_records)} meetings")

    if app_records:
        bulk_upsert(session, Application, app_records)
        print(f"Upserted {len(app_records)} applications")

    if ma_records:
        bulk_upsert(session, MeetingApplication, ma_records)
        print(f"Upserted {len(ma_records)} meeting-application links")

    if all_panellist_records:
        bulk_upsert(session, PanelAttendance, all_panellist_records)
        print(f"Upserted {len(all_panellist_records)} panellists")

    session.commit()


# ===== Opportunity Ingestion ===== #

def process_opportunities_jsonl(session: Session, jsonl_path: Path):
    """Ingest extracted opportunities from a JSONL file."""
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)

            href = raw.get("href", "")
            # Derive opportunity_id from href slug
            slug = href.rstrip("/").rsplit("/", 1)[-1] if href else str(raw.get("id", ""))

            rec = {
                "id": raw["id"],
                "opportunity_id": slug,
                "href": href,
                "title": raw.get("title"),
                "summary": raw.get("summary"),
                "full_text": raw.get("full_text"),
                "raw_html": raw.get("raw_html"),
                "metadata_table": raw.get("metadata_table"),
                "updates": raw.get("updates"),
                "sections": raw.get("sections"),
                "status": raw.get("status"),
                "publication_date": _parse_datetime(raw.get("publication_date")),
                "opening_date": _parse_datetime(raw.get("opening_date")),
                "closing_date": _parse_datetime(raw.get("closing_date")),
                "funding_type": raw.get("funding_type"),
                "funders": raw.get("funders"),
                "co_funders": raw.get("co_funders"),
                "minimum_award": _parse_int(str(raw["minimum_award"])) if raw.get("minimum_award") is not None else None,
                "maximum_award": _parse_int(str(raw["maximum_award"])) if raw.get("maximum_award") is not None else None,
                "total_funding": _parse_int(str(raw["total_funding"])) if raw.get("total_funding") is not None else None,
                "funding_percentage": _parse_int(str(raw["funding_percentage"])) if raw.get("funding_percentage") is not None else None,
                "minimum_funding_duration": timedelta(days=30 * raw["minimum_funding_duration"]) if raw.get("minimum_funding_duration") is not None else None,
                "maximum_funding_duration": timedelta(days=30 * raw["maximum_funding_duration"]) if raw.get("maximum_funding_duration") is not None else None,
            }
            records.append(rec)

    print(f"Parsed {len(records)} opportunities from {jsonl_path}")
    if records:
        bulk_upsert(session, Opportunity, records)
        print(f"Upserted {len(records)} opportunities")
    session.commit()


# ===== CLI ===== #

def main():
    parser = argparse.ArgumentParser(description="Ingest GtR data into PostgreSQL")
    parser.add_argument("--data-dir", default="./data_cache",
                        help="Root directory containing cached data (default: ./data_cache)")

    parser.add_argument("--projects", action="store_true", help="Ingest projects only")
    parser.add_argument("--funds", action="store_true", help="Ingest funds only")
    parser.add_argument("--organisations", action="store_true", help="Ingest organisations only")
    parser.add_argument("--persons", action="store_true", help="Ingest persons only")
    parser.add_argument("--outcomes", action="store_true", help="Ingest outcomes only")
    parser.add_argument("--meetings", action="store_true", help="Ingest meetings only")
    parser.add_argument("--opportunities", action="store_true", help="Ingest opportunities only")

    args = parser.parse_args()
    data_dir = Path(args.data_dir)

    # If no specific flags, ingest everything
    flags = {k: getattr(args, k) for k in ("projects", "funds", "organisations", "persons", "outcomes", "meetings", "opportunities")}
    if not any(flags.values()):
        flags = {k: True for k in flags}

    Base.metadata.create_all(engine)

    # ===== Meetings ===== #
    if flags["meetings"]:
        meetings_path = data_dir / "meetings"
        if meetings_path.is_dir():
            with SessionLocal() as session:
                session.execute(text("SET session_replication_role = 'replica'"))
                process_meetings_json(session, meetings_path)
            print("Meetings ingestion complete.")
        else:
            print(f"WARN: {meetings_path} not found, skipping meetings")

    # ===== Opportunities ===== #
    if flags["opportunities"]:
        opps_path = data_dir / "opportunities" / "extracted_opportunities.jsonl"
        if opps_path.is_file():
            with SessionLocal() as session:
                session.execute(text("SET session_replication_role = 'replica'"))
                process_opportunities_jsonl(session, opps_path)
            print("Opportunities ingestion complete.")
        else:
            print(f"WARN: {opps_path} not found, skipping opportunities")

    # ===== GtR API data ===== #
    gtr_dirs = []
    for flag_name in ("projects", "funds", "organisations", "persons", "outcomes"):
        if flags[flag_name]:
            gtr_dirs.extend(GTR_ENDPOINT_DIRS[flag_name])

    if gtr_dirs:
        files = []
        for d in gtr_dirs:
            dir_path = data_dir / d
            if dir_path.is_dir():
                files.extend(sorted(dir_path.glob("*.json")))
            else:
                print(f"WARN: {dir_path} not found, skipping")

        if files:
            proteus = ProteusClient(PROTEUS_URL)
            max_workers = min(len(files), (os.cpu_count() or 1))
            print(f"Starting ingestion of {len(files)} files with {max_workers} threads...")

            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_path = {executor.submit(worker, path, proteus): path for path in files}

                    for count, future in enumerate(as_completed(future_to_path), 1):
                        result = future.result()
                        if "SUCCESS" in result:
                            print(f"[{count}/{len(files)}] {result}", end="\r")
                        else:
                            print(f"\n{result}")
            finally:
                proteus.close()

            print()

    print("Ingestion complete.")


if __name__ == "__main__":
    main()