import argparse
from collections import defaultdict

import polars as pl
from tqdm.auto import tqdm
from sqlalchemy import text
from sqlalchemy.orm import Session
from rapidfuzz import fuzz as rf_fuzz

from .ingest import SessionLocal


FUNDER_PATTERNS = {
    'AHRC': 'Arts and Humanities',
    'BBSRC': 'Biotechnology',
    'EPSRC': 'Engineering and Physical',
    'ESRC': 'Economic and Social',
    'IUK': 'Innovate UK',
    'MRC': 'Medical Research',
    'NERC': 'Natural Environment',
    'STFC': 'Science and Technology',
}



# --- Application → Project Linking ---

def link_applications_to_projects(session: Session):
    """Link applications to projects via exact grant reference match only."""
    print("Linking applications to projects...")
    # Create temporary expression indexes so the join can use index scans
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS _ix_proj_grant_ref_lower "
        "ON projects (lower(trim(grant_reference))) WHERE grant_reference IS NOT NULL"
    ))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS _ix_app_application_id_lower "
        "ON applications (lower(trim(application_id))) WHERE application_id IS NOT NULL"
    ))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS _ix_app_award_id_lower "
        "ON applications (lower(trim(award_id))) WHERE award_id IS NOT NULL"
    ))
    result = session.execute(text("""
        UPDATE applications a SET project_id = p.id
        FROM projects p
        WHERE a.project_id IS NULL
          AND p.grant_reference IS NOT NULL
          AND (
            (a.application_id IS NOT NULL AND lower(trim(a.application_id)) = lower(trim(p.grant_reference)))
            OR
            (a.award_id IS NOT NULL AND lower(trim(a.award_id)) = lower(trim(p.grant_reference)))
          )
    """))
    session.commit()
    print(f"Applications linked to projects by grant reference: {result.rowcount}")


# --- Application → Opportunity Linking ---

def _parse_funder_codes(funders_text: str | None) -> set[str]:
    """Parse council codes from an opportunity's funders array (e.g. "['EPSRC','MRC']")."""
    if not funders_text:
        return set()
    import re
    return set(re.findall(r"[A-Z]{2,5}", funders_text))


def link_applications_to_opportunities(session: Session):
    """Link applications to opportunities with date, council, and funding constraints."""
    from rapidfuzz import process as rf_process

    print("Linking applications to opportunities...")

    # Fetch unlinked applications with meeting context
    apps = session.execute(text("""
        SELECT
            a.id,
            a.opportunity_name,
            a.council,
            a.extra->>'route' as route,
            min(m.meeting_start) as earliest_meeting,
            array_agg(DISTINCT m.council) FILTER (WHERE m.council IS NOT NULL) as meeting_councils,
            array_agg(DISTINCT m.meeting_name) FILTER (WHERE m.meeting_name IS NOT NULL) as meeting_names,
            sum(ma.awarded_amount) as total_awarded
        FROM applications a
        JOIN meeting_applications ma ON ma.application_id = a.id
        JOIN meetings m ON ma.meeting_id = m.id
        WHERE a.opportunity_id IS NULL
        GROUP BY a.id, a.opportunity_name, a.council, a.extra->>'route'
    """)).fetchall()

    if not apps:
        print("No unlinked applications to process")
        return

    # Fetch all opportunities
    opps = session.execute(text("""
        SELECT
            id, title, funders::text as funders_text, opening_date,
            funding_type, minimum_award, maximum_award
        FROM opportunities
        WHERE title IS NOT NULL
    """)).fetchall()

    if not opps:
        print("No opportunities in database")
        return

    # Pre-process opportunities
    opp_data = []
    for opp in opps:
        codes = _parse_funder_codes(opp.funders_text)
        opp_data.append({
            "id": opp.id,
            "title_lower": opp.title.lower(),
            "title_stripped_lower": opp.title.replace("Funding opportunity: ", "").lower(),
            "funder_codes": codes,
            "opening_date": opp.opening_date,
            "funding_type": (opp.funding_type or "").lower(),
            "min_award": opp.minimum_award,
            "max_award": opp.maximum_award,
        })

    # Index opportunities by funder code for fast lookup
    # Each code maps to the indices into opp_data; None key = opps with no funder codes
    opp_by_funder: dict[str | None, list[int]] = defaultdict(list)
    for i, opp in enumerate(opp_data):
        if opp["funder_codes"]:
            for code in opp["funder_codes"]:
                opp_by_funder[code].append(i)
        else:
            opp_by_funder[None].append(i)

    # Build parallel title lists per funder group for rapidfuzz batch matching
    _funder_groups: dict[str | None, tuple[list[int], list[str], list[str]]] = {}
    for code, indices in opp_by_funder.items():
        deduped = sorted(set(indices))
        _funder_groups[code] = (
            deduped,
            [opp_data[i]["title_lower"] for i in deduped],
            [opp_data[i]["title_stripped_lower"] for i in deduped],
        )

    def _candidate_opps(app_councils: set[str]) -> tuple[list[int], list[str], list[str]]:
        """Return (opp_indices, raw_titles, stripped_titles) for the relevant funder groups."""
        if not app_councils:
            all_idx = list(range(len(opp_data)))
            return (
                all_idx,
                [opp_data[i]["title_lower"] for i in all_idx],
                [opp_data[i]["title_stripped_lower"] for i in all_idx],
            )
        seen = set()
        indices = []
        for code in app_councils:
            if code in _funder_groups:
                for i in _funder_groups[code][0]:
                    if i not in seen:
                        seen.add(i)
                        indices.append(i)
        # Include opportunities with no funder codes (could match any council)
        if None in _funder_groups:
            for i in _funder_groups[None][0]:
                if i not in seen:
                    seen.add(i)
                    indices.append(i)
        return (
            indices,
            [opp_data[i]["title_lower"] for i in indices],
            [opp_data[i]["title_stripped_lower"] for i in indices],
        )

    matches = []
    for app in tqdm(apps, desc="Matching applications"):
        # Candidate text: opportunity_name from meetings data, or meeting name as fallback
        candidate_text = app.opportunity_name
        if not candidate_text and app.meeting_names:
            candidate_text = app.meeting_names[0]
        if not candidate_text:
            continue

        candidate_lower = candidate_text.lower()

        # Council from meetings (authoritative), fall back to application-level
        app_councils: set[str] = set()
        if app.meeting_councils:
            app_councils = set(app.meeting_councils)
        elif app.council:
            app_councils = {app.council}

        earliest_meeting = app.earliest_meeting
        route = (app.route or "").lower()
        total_awarded = float(app.total_awarded) if app.total_awarded else None

        # Get council-filtered candidate opportunities
        cand_indices, cand_raw, cand_stripped = _candidate_opps(app_councils)
        if not cand_indices:
            continue

        # Batch fuzzy match using rapidfuzz
        raw_result = rf_process.extractOne(
            candidate_lower, cand_raw, scorer=rf_fuzz.ratio, score_cutoff=50,
        )
        stripped_result = rf_process.extractOne(
            candidate_lower, cand_stripped, scorer=rf_fuzz.ratio, score_cutoff=50,
        )

        # Pick best from raw vs stripped title match
        best_score = 0.0
        best_local_idx = None
        if raw_result and raw_result[1] > best_score:
            best_score = raw_result[1]
            best_local_idx = raw_result[2]
        if stripped_result and stripped_result[1] > best_score:
            best_score = stripped_result[1]
            best_local_idx = stripped_result[2]

        if best_local_idx is None:
            continue

        opp_idx = cand_indices[best_local_idx]
        opp = opp_data[opp_idx]
        score = best_score / 100.0

        # Date filter
        if earliest_meeting and opp["opening_date"] and earliest_meeting < opp["opening_date"]:
            continue

        # Boost if route matches funding_type
        if route and opp["funding_type"] and route in opp["funding_type"]:
            score += 0.1

        # Penalty if awarded amount outside funding range
        if total_awarded is not None:
            if opp["min_award"] is not None and total_awarded < opp["min_award"]:
                score -= 0.15
            if opp["max_award"] is not None and total_awarded > opp["max_award"]:
                score -= 0.15

        if score >= 0.65:
            matches.append({"aid": str(app.id), "oid": str(opp["id"])})

    # Batch update
    for i in range(0, len(matches), 5000):
        batch = matches[i:i + 5000]
        session.execute(
            text("UPDATE applications SET opportunity_id = CAST(:oid AS uuid) "
                 "WHERE id = CAST(:aid AS uuid)"),
            batch
        )
    session.commit()
    print(f"Applications linked to opportunities: {len(matches)}/{len(apps)}")


# --- Project → Opportunity Linking ---

def propagate_project_opportunities(session: Session):
    """Project inherits opportunity_id from its linked application."""
    print("Propagating opportunity links to projects...")
    result = session.execute(text("""
        UPDATE projects p SET opportunity_id = a.opportunity_id
        FROM applications a
        WHERE a.project_id = p.id
          AND p.opportunity_id IS NULL
          AND a.opportunity_id IS NOT NULL
    """))
    session.commit()
    print(f"Projects linked to opportunities via application: {result.rowcount}")


# --- PanelAttendance → Person Linking ---

def _sub_cluster_by_org(group: pl.DataFrame) -> list[list[str]]:
    """Sub-cluster rows within a (council, surname, initial) group by org similarity.

    Returns a list of clusters, each being a list of panel_attendance IDs.
    """
    if group.height <= 1:
        return [group["id"].to_list()]

    orgs = group["org"].to_list()
    ids = group["id"].to_list()

    # If no orgs at all, everything merges
    if all(o == "" for o in orgs):
        return [ids]

    clusters: list[list[int]] = []  # indices into the group
    cluster_orgs: list[list[str]] = []

    for i, org in enumerate(orgs):
        merged = False
        for ci, (c_indices, c_orgs) in enumerate(zip(clusters, cluster_orgs)):
            can_merge = True
            if org:
                for co in c_orgs:
                    if co and rf_fuzz.ratio(org, co) < 70:
                        can_merge = False
                        break
            if can_merge:
                c_indices.append(i)
                if org:
                    c_orgs.append(org)
                merged = True
                break
        if not merged:
            clusters.append([i])
            cluster_orgs.append([org] if org else [])

    return [[ids[i] for i in c] for c in clusters]


def link_panel_attendance_to_people(session: Session):
    """Two-phase linking: disambiguate within each council, then align with GtR persons."""
    print("Linking panel attendance to persons...")

    # --- Phase 1: Disambiguate within each council ---
    rows = session.execute(text(
        "SELECT id::text, source_panellist_name, organisation_name, council "
        "FROM panel_attendance "
        "WHERE source_panellist_name IS NOT NULL"
    )).fetchall()

    if not rows:
        print("No panel attendance records to process")
        return

    # Build a Polars DataFrame and parse the already-cleaned "first last" names
    df = pl.DataFrame({
        "id": [r.id for r in rows],
        "name": [r.source_panellist_name for r in rows],
        "org": [(r.organisation_name or "").lower().strip() for r in rows],
        "council": [(r.council or "").upper() for r in rows],
    }).with_columns(
        pl.col("name").str.to_lowercase().str.split(" ").alias("_tokens"),
    ).filter(
        pl.col("_tokens").list.len() >= 2,
    ).with_columns(
        # surname = last token, first_name = all tokens except last
        pl.col("_tokens").list.last().alias("surname"),
        pl.col("_tokens").list.head(pl.col("_tokens").list.len() - 1).list.join(" ").alias("first_name"),
    ).with_columns(
        pl.col("first_name").str.slice(0, 1).alias("initial"),
    ).filter(
        (pl.col("surname") != "") & (pl.col("initial") != "")
    ).drop("_tokens")

    # Group by (council, surname, initial) and sub-cluster by org similarity
    all_individuals: list[dict] = []
    grouped = df.group_by(["council", "surname", "initial"]).agg(
        pl.col("id"),
        pl.col("first_name"),
        pl.col("org"),
    )

    for row in tqdm(grouped.iter_rows(named=True), total=grouped.height, desc="Phase 1: clustering"):
        ids = row["id"]
        first_names = row["first_name"]
        orgs = row["org"]

        sub_df = pl.DataFrame({"id": ids, "first_name": first_names, "org": orgs})
        clusters = _sub_cluster_by_org(sub_df)

        for cluster_ids in clusters:
            cluster = sub_df.filter(pl.col("id").is_in(cluster_ids))
            # Pick best representative: longest first_name, then longest org
            best = cluster.sort(
                [pl.col("first_name").str.len_chars(), pl.col("org").str.len_chars()],
                descending=True,
            ).row(0, named=True)
            all_individuals.append({
                "surname": row["surname"],
                "initial": row["initial"],
                "first_name": best["first_name"],
                "org": best["org"],
                "council": row["council"],
                "ids": cluster_ids,
            })

    n_councils = df["council"].n_unique()
    print(f"Phase 1: Disambiguated {df.height} panel attendance records into "
          f"{len(all_individuals)} unique individuals across {n_councils} councils")

    # --- Phase 2: Align with existing GtR Persons ---

    people_rows = session.execute(text("""
        SELECT DISTINCT p.id::text, lower(p.first_name) as first, lower(p.surname) as sur
        FROM persons p
        WHERE p.first_name IS NOT NULL AND p.surname IS NOT NULL
          AND EXISTS (SELECT 1 FROM project_members pm WHERE pm.person_id = p.id)
    """)).fetchall()

    name_lookup: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for pid, first, sur in people_rows:
        if first and sur:
            name_lookup[(sur, first[0])].append((pid, first))
    print(f"Person index: {len(name_lookup)} surname+initial combos from {len(people_rows)} GtR persons")

    # Build org lookup: person_id -> set(org_name_lower)
    person_org_rows = session.execute(text("""
        SELECT DISTINCT pm.person_id::text, lower(o.name) as org_name
        FROM project_members pm
        JOIN project_partners pp ON pm.project_id = pp.project_id AND pp.role = 'LEAD_ORG'
        JOIN organisations o ON pp.organisation_id = o.id
    """)).fetchall()

    person_orgs: dict[str, set[str]] = defaultdict(set)
    for pid, org in person_org_rows:
        person_orgs[pid].add(org)

    # Match individuals to persons
    matched_ids: dict[str, str] = {}  # panel_attendance_id -> person_id
    matched_count = 0

    for ind in tqdm(all_individuals, desc="Phase 2: matching to GtR persons"):
        candidates = name_lookup.get((ind["surname"], ind["initial"]))
        if not candidates:
            continue

        person_id = None

        if len(candidates) == 1:
            # Unique surname+initial in GtR: safe to link
            person_id = candidates[0][0]
        else:
            # Multiple candidates: score each and only link if there is a clear winner
            scored: list[tuple[str, float]] = []
            for cand_pid, cand_first in candidates:
                score = 0.0

                # First name similarity (only if panellist has a full name, not just an initial)
                if ind["first_name"] and len(ind["first_name"]) > 1:
                    score += rf_fuzz.ratio(ind["first_name"], cand_first) / 100.0

                # Organisation similarity
                if ind["org"]:
                    cand_orgs = person_orgs.get(cand_pid)
                    if cand_orgs:
                        score += max(rf_fuzz.ratio(ind["org"], co) for co in cand_orgs) / 100.0

                scored.append((cand_pid, score))

            scored.sort(key=lambda x: x[1], reverse=True)
            best_pid, best_score = scored[0]
            second_score = scored[1][1] if len(scored) > 1 else 0.0

            # Link only if best candidate scores well and has clear margin over runner-up
            if best_score > 0.6 and (best_score - second_score) > 0.15:
                person_id = best_pid

        if person_id:
            matched_count += 1
            for pa_id in ind["ids"]:
                matched_ids[pa_id] = person_id

    # Batch update
    if matched_ids:
        updates = [{"pan_id": k, "per_id": v} for k, v in matched_ids.items()]
        for i in range(0, len(updates), 5000):
            batch = updates[i:i + 5000]
            session.execute(
                text("UPDATE panel_attendance SET person_id = CAST(:per_id AS uuid) "
                     "WHERE id = CAST(:pan_id AS uuid)"),
                batch
            )
        session.commit()

    total_pa = sum(len(ind["ids"]) for ind in all_individuals)
    linked_pa = len(matched_ids)
    print(f"Phase 2: Linked {matched_count}/{len(all_individuals)} individuals "
          f"({linked_pa}/{total_pa} panel attendance records) to GtR persons")


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="Entity linking for the GtR database")
    parser.add_argument("--app-proj", action="store_true", help="Link applications to projects")
    parser.add_argument("--app-opp", action="store_true", help="Link applications to opportunities")
    parser.add_argument("--proj-opp", action="store_true", help="Link projects to opportunities (via applications)")
    parser.add_argument("--pan-per", action="store_true", help="Link panel attendance to persons")
    args = parser.parse_args()

    # If no flags, run everything
    flags = {k: getattr(args, k.replace("-", "_")) for k in ("app-proj", "app-opp", "proj-opp", "pan-per")}
    run_all = not any(flags.values())

    if run_all or flags["app-proj"]:
        with SessionLocal() as session:
            link_applications_to_projects(session)

    if run_all or flags["app-opp"]:
        with SessionLocal() as session:
            link_applications_to_opportunities(session)

    if run_all or flags["proj-opp"]:
        with SessionLocal() as session:
            propagate_project_opportunities(session)

    if run_all or flags["pan-per"]:
        with SessionLocal() as session:
            link_panel_attendance_to_people(session)

    print("Entity linking complete.")


if __name__ == "__main__":
    main()
