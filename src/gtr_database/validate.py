import argparse
import math
import time
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from .ingest import SessionLocal, engine

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

# Tables and their critical fields for completeness checks
COMPLETENESS_FIELDS = {
    "projects": [
        "title", "abstract", "status", "lead_funder",
        "grant_reference", "opportunity_id",
    ],
    "meetings": [
        "meeting_name", "panel_reference", "council",
        "year",
    ],
    "applications": [
        "application_title", "lead_applicant",
        "project_id", "opportunity_id",
    ],
    "meeting_applications": [
        "meeting_id", "application_id", "outcome", "rank",
    ],
    "panel_attendance": [
        "source_panellist_name", "organisation_name",
        "panel_role", "person_id",
    ],
    "persons": ["first_name", "surname", "orcid"],
    "organisations": ["name", "reg_number", "website"],
    "opportunities": [
        "title", "summary", "status",
        "opening_date", "closing_date", "funders",
    ],
    "project_funds": ["amount", "start_date", "end_date"],
    "outcomes": ["title", "description", "project_id"],
}

# FK checks: (child_table, fk_col, parent_table)
FK_CHECKS = [
    ("project_taxonomies", "project_id", "projects"),
    ("project_funds", "project_id", "projects"),
    ("project_members", "project_id", "projects"),
    ("project_members", "person_id", "persons"),
    ("project_partners", "project_id", "projects"),
    ("project_partners", "organisation_id", "organisations"),
    ("project_relationships", "from_project_id", "projects"),
    ("project_relationships", "to_project_id", "projects"),
    ("person_organisation_links", "person_id", "persons"),
    ("person_organisation_links", "organisation_id", "organisations"),
    ("addresses", "organisation_id", "organisations"),
    ("outcomes", "project_id", "projects"),
    ("meeting_applications", "meeting_id", "meetings"),
    ("meeting_applications", "application_id", "applications"),
    ("applications", "project_id", "projects"),
    ("applications", "opportunity_id", "opportunities"),
    ("panel_attendance", "meeting_id", "meetings"),
    ("panel_attendance", "person_id", "persons"),
]

# "Lonely parent" checks: parent table has no children
LONELY_PARENT_CHECKS = [
    ("projects", "id", "project_members", "project_id", "projects with no members"),
    ("meetings", "id", "meeting_applications", "meeting_id", "meetings with no applications"),
    ("meetings", "id", "panel_attendance", "meeting_id", "meetings with no panellists"),
]


# ===== Validation =====

def validate_fks(session: Session) -> dict[str, int]:
    """Check all FK references and return counts of orphaned rows."""
    orphans = {}
    for child_table, fk_col, parent_table in FK_CHECKS:
        count = session.execute(text(
            f"SELECT count(*) FROM {child_table} c "
            f"WHERE c.{fk_col} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {parent_table} p WHERE p.id = c.{fk_col})"
        )).scalar()
        if count:
            orphans[f"{child_table}.{fk_col} -> {parent_table}"] = count
    return orphans


def run_validate():
    """Validate FK integrity and print results."""
    with SessionLocal() as session:
        orphans = validate_fks(session)
        if orphans:
            print("Dangling FK references found:")
            for desc, n in orphans.items():
                print(f"  {desc}: {n} rows")
        else:
            print("All FK references valid.")


# ===== Evaluation helpers =====

def _wilson_ci(successes: int, total: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score interval for a proportion. Returns (point, lower, upper)."""
    if total == 0:
        return 0.0, 0.0, 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return p, max(0.0, centre - spread), min(1.0, centre + spread)


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{100 * n / total:.1f}%"


def _fmt_ci(successes: int, total: int) -> str:
    """Format a Wilson CI as 'XX.X% (95% CI: XX.X%-XX.X%)'."""
    if total == 0:
        return "N/A (no data)"
    p, lo, hi = _wilson_ci(successes, total)
    return f"{p * 100:.1f}% (95% CI: {lo * 100:.1f}%-{hi * 100:.1f}%)"


# ===== Section 1: Completeness =====

def _eval_completeness(session: Session) -> dict:
    results = {}
    for table, fields in COMPLETENESS_FIELDS.items():
        count_expr = ", ".join(
            f"count(*) FILTER (WHERE {f} IS NULL) AS {f}_null" for f in fields
        )
        row = session.execute(text(
            f"SELECT count(*) AS total, {count_expr} FROM {table}"
        )).mappings().one()

        total = row["total"]
        field_stats = []
        for f in fields:
            missing = row[f"{f}_null"]
            field_stats.append({
                "field": f,
                "present": total - missing,
                "missing": missing,
                "missing_pct": (missing / total * 100) if total else 0,
            })
        results[table] = {"total": total, "fields": field_stats}
    return results


# ===== Section 2: Referential Integrity =====

def _eval_referential_integrity(session: Session) -> dict:
    orphans = {}
    for child_table, fk_col, parent_table in FK_CHECKS:
        count = session.execute(text(
            f"SELECT count(*) FROM {child_table} c "
            f"WHERE c.{fk_col} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {parent_table} p WHERE p.id = c.{fk_col})"
        )).scalar()
        orphans[f"{child_table}.{fk_col} -> {parent_table}"] = count

    lonely = {}
    for parent_tbl, parent_col, child_tbl, child_col, label in LONELY_PARENT_CHECKS:
        count = session.execute(text(
            f"SELECT count(*) FROM {parent_tbl} p "
            f"WHERE NOT EXISTS (SELECT 1 FROM {child_tbl} c WHERE c.{child_col} = p.{parent_col})"
        )).scalar()
        lonely[label] = count

    return {"orphans": orphans, "lonely_parents": lonely}


# ===== Section 3: Link Accuracy =====

def _eval_link_accuracy(session: Session) -> dict:
    results = {}
    results["app_project"] = _eval_app_project(session)
    results["panellist_person"] = _eval_panellist_person(session)
    results["propagated"] = _eval_propagated_links(session)
    return results


def _eval_app_project(session: Session) -> dict:
    counts = session.execute(text(
        "SELECT count(*) AS total, "
        "count(*) FILTER (WHERE project_id IS NOT NULL) AS linked "
        "FROM applications"
    )).mappings().one()

    total_apps = counts["total"]
    linked = counts["linked"]

    sim_stats = session.execute(text("""
        SELECT
            count(*) AS n,
            avg(similarity(lower(a.application_title), lower(p.title))) AS mean,
            percentile_cont(0.05) WITHIN GROUP (ORDER BY similarity(lower(a.application_title), lower(p.title))) AS p5,
            percentile_cont(0.25) WITHIN GROUP (ORDER BY similarity(lower(a.application_title), lower(p.title))) AS p25,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY similarity(lower(a.application_title), lower(p.title))) AS p50,
            percentile_cont(0.75) WITHIN GROUP (ORDER BY similarity(lower(a.application_title), lower(p.title))) AS p75,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY similarity(lower(a.application_title), lower(p.title))) AS p95,
            count(*) FILTER (WHERE similarity(lower(a.application_title), lower(p.title)) < 0.5) AS below_50,
            count(*) FILTER (WHERE similarity(lower(a.application_title), lower(p.title)) < 0.3) AS below_30
        FROM applications a
        JOIN projects p ON a.project_id = p.id
        WHERE a.application_title IS NOT NULL AND p.title IS NOT NULL
    """)).mappings().one()

    org_match = session.execute(text("""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE similarity(
                lower(a.lead_organisation),
                lower(o.name)
            ) > 0.7) AS matched
        FROM applications a
        JOIN projects p ON a.project_id = p.id
        JOIN project_partners pp ON pp.project_id = p.id AND pp.role = 'LEAD_ORG'
        JOIN organisations o ON pp.organisation_id = o.id
        WHERE a.lead_organisation IS NOT NULL
    """)).mappings().one()

    funder_cases = " ".join(
        f"WHEN a.council = '{code}' AND lower(p.lead_funder) LIKE '%{pattern.lower()}%' THEN TRUE"
        for code, pattern in FUNDER_PATTERNS.items()
    )
    funder_match = session.execute(text(f"""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE CASE {funder_cases} ELSE FALSE END) AS matched
        FROM applications a
        JOIN projects p ON a.project_id = p.id
        WHERE a.council IS NOT NULL AND p.lead_funder IS NOT NULL
    """)).mappings().one()

    pi_match = session.execute(text("""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE
                lower(p2.surname) = lower(split_part(
                    CASE WHEN a.lead_applicant LIKE '%,%'
                         THEN split_part(a.lead_applicant, ',', 1)
                         ELSE split_part(a.lead_applicant, ' ', -1)
                    END, ' ', -1))
            ) AS matched
        FROM applications a
        JOIN projects p ON a.project_id = p.id
        JOIN project_members pm ON pm.project_id = p.id AND pm.role = 'PI_PER'
        JOIN persons p2 ON pm.person_id = p2.id
        WHERE a.lead_applicant IS NOT NULL AND p2.surname IS NOT NULL
    """)).mappings().one()

    agree_count = session.execute(text(f"""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE
                (org_ok IS NULL OR org_ok)
                AND (funder_ok IS NULL OR funder_ok)
                AND (pi_ok IS NULL OR pi_ok)
            ) AS all_agree
        FROM (
            SELECT a.id,
                CASE WHEN a.lead_organisation IS NOT NULL AND o.name IS NOT NULL
                     THEN similarity(lower(a.lead_organisation), lower(o.name)) > 0.7
                     ELSE NULL END AS org_ok,
                CASE {funder_cases} ELSE NULL END::boolean AS funder_ok,
                CASE WHEN a.lead_applicant IS NOT NULL AND p2.surname IS NOT NULL
                     THEN lower(p2.surname) = lower(split_part(
                         CASE WHEN a.lead_applicant LIKE '%,%'
                              THEN split_part(a.lead_applicant, ',', 1)
                              ELSE split_part(a.lead_applicant, ' ', -1)
                         END, ' ', -1))
                     ELSE NULL END AS pi_ok
            FROM applications a
            JOIN projects p ON a.project_id = p.id
            LEFT JOIN project_partners pp ON pp.project_id = p.id AND pp.role = 'LEAD_ORG'
            LEFT JOIN organisations o ON pp.organisation_id = o.id
            LEFT JOIN project_members pm ON pm.project_id = p.id AND pm.role = 'PI_PER'
            LEFT JOIN persons p2 ON pm.person_id = p2.id
            WHERE a.project_id IS NOT NULL
        ) sub
    """)).mappings().one()

    return {
        "total_apps": total_apps,
        "linked": linked,
        "sim": dict(sim_stats),
        "org_match": dict(org_match),
        "funder_match": dict(funder_match),
        "pi_match": dict(pi_match),
        "precision": {
            "agree": agree_count["all_agree"],
            "total": agree_count["total"],
        },
    }


def _eval_panellist_person(session: Session) -> dict:
    counts = session.execute(text(
        "SELECT count(*) AS total, "
        "count(*) FILTER (WHERE person_id IS NOT NULL) AS linked "
        "FROM panel_attendance"
    )).mappings().one()

    surname_check = session.execute(text("""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE
                lower(pa.source_panellist_name) LIKE '%' || lower(p.surname) || '%'
            ) AS surname_match
        FROM panel_attendance pa
        JOIN persons p ON pa.person_id = p.id
        WHERE pa.source_panellist_name IS NOT NULL AND p.surname IS NOT NULL
    """)).mappings().one()

    org_check = session.execute(text("""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE org_match) AS matched
        FROM (
            SELECT pa.id,
                EXISTS (
                    SELECT 1
                    FROM project_members pm
                    JOIN project_partners pp ON pm.project_id = pp.project_id
                        AND pp.role = 'LEAD_ORG'
                    JOIN organisations o ON pp.organisation_id = o.id
                    WHERE pm.person_id = pa.person_id
                      AND similarity(lower(pa.organisation_name), lower(o.name)) > 0.5
                ) AS org_match
            FROM panel_attendance pa
            WHERE pa.person_id IS NOT NULL
              AND pa.organisation_name IS NOT NULL
        ) sub
    """)).mappings().one()

    ambiguity = session.execute(text("""
        WITH linked AS (
            SELECT pa.id,
                   lower(p.surname) AS surname,
                   lower(left(p.first_name, 1)) AS initial
            FROM panel_attendance pa
            JOIN persons p ON pa.person_id = p.id
            WHERE p.surname IS NOT NULL AND p.first_name IS NOT NULL
        ),
        name_counts AS (
            SELECT lower(surname) AS surname, lower(left(first_name, 1)) AS initial,
                   count(*) AS n
            FROM persons
            WHERE surname IS NOT NULL AND first_name IS NOT NULL
            GROUP BY lower(surname), lower(left(first_name, 1))
        )
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE nc.n = 1) AS unique_match,
            count(*) FILTER (WHERE nc.n > 1) AS disambiguated
        FROM linked l
        LEFT JOIN name_counts nc ON l.surname = nc.surname AND l.initial = nc.initial
    """)).mappings().one()

    return {
        "total": counts["total"],
        "linked": counts["linked"],
        "surname_check": dict(surname_check),
        "org_check": dict(org_check),
        "ambiguity": dict(ambiguity),
    }


def _eval_propagated_links(session: Session) -> dict:
    proj_opp = session.execute(text("""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE p.opportunity_id = a.opportunity_id) AS consistent,
            count(*) FILTER (WHERE p.opportunity_id IS NOT NULL
                             AND a.opportunity_id IS NOT NULL
                             AND p.opportunity_id != a.opportunity_id) AS inconsistent
        FROM applications a
        JOIN projects p ON a.project_id = p.id
        WHERE a.opportunity_id IS NOT NULL OR p.opportunity_id IS NOT NULL
    """)).mappings().one()

    return {
        "proj_opp_consistency": dict(proj_opp),
    }


# ===== Section 4: Duplicates =====

def _eval_duplicates(session: Session) -> dict:
    people_dupes = session.execute(text("""
        SELECT count(*) AS groups, sum(n) AS affected_rows
        FROM (
            SELECT lower(first_name) AS fn, lower(surname) AS sn, count(*) AS n
            FROM persons
            WHERE first_name IS NOT NULL AND surname IS NOT NULL
            GROUP BY lower(first_name), lower(surname)
            HAVING count(*) > 1
        ) sub
    """)).mappings().one()

    project_dupes = session.execute(text("""
        SELECT count(*) AS groups, sum(n) AS affected_rows
        FROM (
            SELECT lower(title), count(*) AS n
            FROM projects
            WHERE title IS NOT NULL
            GROUP BY lower(title)
            HAVING count(*) > 1
        ) sub
    """)).mappings().one()

    meeting_dupes = session.execute(text("""
        SELECT count(*) AS groups, sum(n) AS affected_rows
        FROM (
            SELECT panel_reference, count(*) AS n
            FROM meetings
            WHERE panel_reference IS NOT NULL
            GROUP BY panel_reference
            HAVING count(*) > 1
        ) sub
    """)).mappings().one()

    meeting_case_dupes = session.execute(text("""
        SELECT count(*) AS groups, sum(n) AS affected_rows
        FROM (
            SELECT lower(panel_reference), count(*) AS n
            FROM meetings
            WHERE panel_reference IS NOT NULL
            GROUP BY lower(panel_reference)
            HAVING count(*) > 1 AND count(DISTINCT panel_reference) > 1
        ) sub
    """)).mappings().one()

    org_dupes = session.execute(text("""
        SELECT count(*) AS groups, sum(n) AS affected_rows
        FROM (
            SELECT lower(name), count(*) AS n
            FROM organisations
            WHERE name IS NOT NULL
            GROUP BY lower(name)
            HAVING count(*) > 1
        ) sub
    """)).mappings().one()

    return {
        "people": dict(people_dupes),
        "projects": dict(project_dupes),
        "meetings": dict(meeting_dupes),
        "meeting_case_variants": dict(meeting_case_dupes),
        "organisations": dict(org_dupes),
    }


# ===== Section 5: Consistency =====

def _eval_consistency(session: Session) -> dict:
    year_check = session.execute(text("""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE a.year = m.year) AS exact_match,
            count(*) FILTER (WHERE abs(a.year - m.year) <= 1) AS within_1yr,
            count(*) FILTER (WHERE abs(a.year - m.year) > 1) AS mismatch_gt_1yr
        FROM applications a
        JOIN meeting_applications ma ON ma.application_id = a.id
        JOIN meetings m ON ma.meeting_id = m.id
        WHERE a.year IS NOT NULL AND m.year IS NOT NULL
    """)).mappings().one()

    funder_check = session.execute(text("""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE a.council = m.council) AS match,
            count(*) FILTER (WHERE a.council != m.council) AS mismatch
        FROM applications a
        JOIN meeting_applications ma ON ma.application_id = a.id
        JOIN meetings m ON ma.meeting_id = m.id
        WHERE a.council IS NOT NULL AND m.council IS NOT NULL
    """)).mappings().one()

    amount_check = session.execute(text("""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE ma.awarded_amount = pf.amount) AS exact_match,
            count(*) FILTER (WHERE abs(ma.awarded_amount - pf.amount) < 1000) AS within_1k,
            avg(abs(ma.awarded_amount - pf.amount)) AS mean_diff
        FROM applications a
        JOIN meeting_applications ma ON ma.application_id = a.id
        JOIN projects p ON a.project_id = p.id
        JOIN project_funds pf ON pf.project_id = p.id
        WHERE ma.awarded_amount IS NOT NULL AND pf.amount IS NOT NULL
          AND ma.awarded_amount > 0 AND pf.amount > 0
    """)).mappings().one()

    outcomes_dist = session.execute(text("""
        SELECT
            count(*) AS projects_with_outcomes,
            avg(n) AS mean,
            percentile_cont(0.25) WITHIN GROUP (ORDER BY n) AS p25,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY n) AS p50,
            percentile_cont(0.75) WITHIN GROUP (ORDER BY n) AS p75,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY n) AS p95,
            max(n) AS max
        FROM (
            SELECT project_id, count(*) AS n
            FROM outcomes
            WHERE project_id IS NOT NULL
            GROUP BY project_id
        ) sub
    """)).mappings().one()

    return {
        "year": dict(year_check),
        "funder": dict(funder_check),
        "amount": dict(amount_check),
        "outcomes_dist": dict(outcomes_dist),
    }


# ===== Report Formatting =====

def _format_report(completeness: dict, integrity: dict, accuracy: dict,
                   duplicates: dict, consistency: dict,
                   elapsed: float) -> str:
    lines = []
    W = 71

    lines.append("=" * W)
    lines.append("DATA QUALITY AND ACCURACY REPORT")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Elapsed: {elapsed:.1f}s")
    lines.append("=" * W)

    # 1. Completeness
    lines.append("")
    lines.append("1. COMPLETENESS")
    lines.append("-" * W)
    for table, data in completeness.items():
        lines.append(f"  Table: {table} (N = {data['total']:,})")
        lines.append(f"    {'Field':<30} {'Present':>10} {'Missing':>10} {'Missing %':>10}")
        for f in data["fields"]:
            lines.append(
                f"    {f['field']:<30} {f['present']:>10,} {f['missing']:>10,} "
                f"{f['missing_pct']:>9.1f}%"
            )
        lines.append("")

    # 2. Referential Integrity
    lines.append("2. REFERENTIAL INTEGRITY")
    lines.append("-" * W)
    lines.append("  Orphan rows (FK points to missing parent):")
    any_orphan = False
    for desc, count in integrity["orphans"].items():
        if count > 0:
            lines.append(f"    {desc}: {count:,}")
            any_orphan = True
    if not any_orphan:
        lines.append("    (none)")

    lines.append("")
    lines.append("  Lonely parents (no children):")
    for desc, count in integrity["lonely_parents"].items():
        lines.append(f"    {desc}: {count:,}")
    lines.append("")

    # 3. Link Accuracy
    lines.append("3. LINK ACCURACY")
    lines.append("-" * W)

    ap = accuracy["app_project"]
    lines.append(f"  3.1 Application -> Project")
    lines.append(f"    Linked: {ap['linked']:,} / {ap['total_apps']:,} "
                 f"({_pct(ap['linked'], ap['total_apps'])})")

    sim = ap["sim"]
    if sim["n"]:
        lines.append(f"    Title similarity (N={sim['n']:,}):")
        lines.append(f"      P5={sim['p5']:.3f}  P25={sim['p25']:.3f}  "
                     f"P50={sim['p50']:.3f}  P75={sim['p75']:.3f}  P95={sim['p95']:.3f}  "
                     f"mean={sim['mean']:.3f}")
        lines.append(f"      Below 0.5: {sim['below_50']:,}  Below 0.3: {sim['below_30']:,}")

    om = ap["org_match"]
    fm = ap["funder_match"]
    pm = ap["pi_match"]
    lines.append(f"    Cross-validation:")
    lines.append(f"      Organisation match: {om['matched']:,}/{om['total']:,} "
                 f"({_pct(om['matched'], om['total'])})")
    lines.append(f"      Funder match:       {fm['matched']:,}/{fm['total']:,} "
                 f"({_pct(fm['matched'], fm['total'])})")
    lines.append(f"      PI name match:      {pm['matched']:,}/{pm['total']:,} "
                 f"({_pct(pm['matched'], pm['total'])})")

    prec = ap["precision"]
    lines.append(f"    Estimated precision: "
                 f"{_fmt_ci(prec['agree'], prec['total'])}")
    lines.append("")

    pp = accuracy["panellist_person"]
    lines.append(f"  3.2 Panellist -> Person")
    lines.append(f"    Linked: {pp['linked']:,} / {pp['total']:,} "
                 f"({_pct(pp['linked'], pp['total'])})")

    sc = pp["surname_check"]
    lines.append(f"    Surname containment: {sc['surname_match']:,}/{sc['total']:,} "
                 f"({_pct(sc['surname_match'], sc['total'])})")

    oc = pp["org_check"]
    lines.append(f"    Organisation match: {oc['matched']:,}/{oc['total']:,} "
                 f"({_pct(oc['matched'], oc['total'])})")

    amb = pp["ambiguity"]
    lines.append(f"    Ambiguity analysis (N={amb['total']:,}):")
    lines.append(f"      Unique surname+initial: {amb['unique_match']:,} "
                 f"({_pct(amb['unique_match'], amb['total'])})")
    lines.append(f"      Disambiguated:          {amb['disambiguated']:,} "
                 f"({_pct(amb['disambiguated'], amb['total'])})")
    lines.append("")

    prop = accuracy["propagated"]
    po = prop["proj_opp_consistency"]
    lines.append(f"  3.3 Propagated Links")
    lines.append(f"    Project.opportunity == App.opportunity: "
                 f"{po['consistent']:,}/{po['total']:,} consistent, "
                 f"{po['inconsistent']:,} inconsistent")
    lines.append("")

    # 4. Duplicates
    lines.append("4. DUPLICATES")
    lines.append("-" * W)
    for entity, data in duplicates.items():
        groups = data["groups"] or 0
        rows = data["affected_rows"] or 0
        lines.append(f"  {entity}: {groups:,} duplicate groups, {rows:,} affected rows")
    lines.append("")

    # 5. Consistency
    lines.append("5. CONSISTENCY")
    lines.append("-" * W)

    yr = consistency["year"]
    lines.append(f"  App year vs meeting year (N={yr['total']:,}):")
    lines.append(f"    Exact match: {yr['exact_match']:,} ({_pct(yr['exact_match'], yr['total'])})")
    lines.append(f"    Within 1 year: {yr['within_1yr']:,} ({_pct(yr['within_1yr'], yr['total'])})")
    lines.append(f"    Mismatch >1yr: {yr['mismatch_gt_1yr']:,}")

    fb = consistency["funder"]
    lines.append(f"  App council vs meeting council (N={fb['total']:,}):")
    lines.append(f"    Match: {fb['match']:,} ({_pct(fb['match'], fb['total'])})")
    lines.append(f"    Mismatch: {fb['mismatch']:,}")

    am = consistency["amount"]
    lines.append(f"  App awarded_amount vs project_fund amount (N={am['total']:,}):")
    lines.append(f"    Exact match: {am['exact_match']:,} ({_pct(am['exact_match'], am['total'])})")
    lines.append(f"    Within 1k: {am['within_1k']:,} ({_pct(am['within_1k'], am['total'])})")
    if am["mean_diff"] is not None:
        lines.append(f"    Mean difference: {float(am['mean_diff']):,.0f}")

    od = consistency["outcomes_dist"]
    lines.append(f"  Outcomes per project (N={od['projects_with_outcomes']:,} projects):")
    if od["mean"] is not None:
        lines.append(f"    mean={float(od['mean']):.1f}  P25={float(od['p25']):.0f}  "
                     f"P50={float(od['p50']):.0f}  P75={float(od['p75']):.0f}  "
                     f"P95={float(od['p95']):.0f}  max={od['max']}")
    lines.append("")
    lines.append("=" * W)
    lines.append("END OF REPORT")
    lines.append("=" * W)

    return "\n".join(lines)


# ===== Main Entry Point =====

def evaluate_data_quality(session: Session) -> str:
    """Run all data quality evaluations and return a formatted report."""
    t0 = time.time()

    session.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    completeness = _eval_completeness(session)
    integrity = _eval_referential_integrity(session)
    accuracy = _eval_link_accuracy(session)
    duplicates = _eval_duplicates(session)
    consistency = _eval_consistency(session)

    elapsed = time.time() - t0

    return _format_report(
        completeness, integrity, accuracy, duplicates, consistency, elapsed,
    )


def run_evaluate():
    """Run full data quality evaluation and print report."""
    with SessionLocal() as session:
        report = evaluate_data_quality(session)
        print(report)


# ===== CLI =====

def main():
    parser = argparse.ArgumentParser(description="Validation and data quality evaluation for the GtR database")
    parser.add_argument("--validate", action="store_true", help="FK integrity check only")
    parser.add_argument("--evaluate", action="store_true", help="Full data quality report")
    args = parser.parse_args()

    run_all = not any([args.validate, args.evaluate])

    if run_all or args.validate:
        run_validate()
    if run_all or args.evaluate:
        run_evaluate()


if __name__ == "__main__":
    main()
