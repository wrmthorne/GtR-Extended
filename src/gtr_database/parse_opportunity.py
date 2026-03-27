from dataclasses import dataclass, field
from lxml import html as lxml_html
from uuid import UUID, uuid5
import html2text
import dateparser
import re
from typing import Optional
from datetime import datetime

_NAMESPACE = UUID("a3f1b2c4-5d6e-7f80-9a1b-2c3d4e5f6a7b")


_h = html2text.HTML2Text()
_h.ignore_links = False
_h.bypass_tables = False
_h.body_width = 0

_na_pattern = re.compile(r"(?i)^n/?a$")
_tz_pattern = re.compile(r"(?i)\s+uk\s+time\b")
_abbrev_pattern = re.compile(r'\n\s*\*\[[^]]+]:[^\n]*')

_UKRI_FUNDER_MAP = [
    ("Engineering and Physical Sciences", "EPSRC"),
    ("Arts and Humanities", "AHRC"),
    ("Biotechnology and Biological", "BBSRC"),
    ("Economic and Social", "ESRC"),
    ("Innovate UK", "IUK"),
    ("Medical Research Council", "MRC"),
    ("Natural Environment", "NERC"),
    ("Science and Technology Facilities", "STFC"),
    ("UK Research and Innovation", "UKRI"),
    ("Research England", "RE"),
]

_METADATA_FIELD_MAP = {
    "funding_type": "funding_type",
    "opportunity_status": "status",
    "funders": "funders",
    "co_funders": "co_funders",
    "publication_date": "publication_date",
    "opening_date": "opening_date",
    "closing_date": "closing_date",
    "minimum_award": "minimum_award",
    "maximum_award": "maximum_award",
    "award_range": "minimum_award",
    "award": "maximum_award",
    "total_fund": "total_funding",
}

_LIST_FIELDS = {"funders", "co_funders"}


def _strip_abbreviations(text: str) -> str:
    return _abbrev_pattern.sub('', text).rstrip()


def _standardise_funder(name: str) -> str:
    name_lower = name.lower()
    for pattern, code in _UKRI_FUNDER_MAP:
        if pattern.lower() in name_lower:
            return code
    return name


def _to_markdown(element) -> str:
    raw = lxml_html.tostring(element, encoding="utf-8").decode("utf-8")
    md = _h.handle(raw).strip()
    md = re.sub(r'^[ \t]+([*\-] |\d+\. )', r'\1', md, flags=re.MULTILINE)
    return re.sub(r'\n{3,}', '\n\n', md)


def _strip_links(text: str) -> str:
    return re.sub(r'\[([^]]+)]\([^)]+\)', r'\1', text)


def _parse_metadata_value(key: str, raw: str) -> str | list[str] | datetime | None:
    stripped = _strip_links(raw).strip()
    if key in _LIST_FIELDS:
        return [s.strip() for s in re.split(r'[,;]\s*', stripped) if s.strip()]
    if "date" in key:
        if _na_pattern.match(raw):
            return None
        return dateparser.parse(
            _tz_pattern.sub("", raw),
            settings={"TIMEZONE": "Europe/London", "RETURN_AS_TIMEZONE_AWARE": True},
        )
    return stripped


@dataclass
class Section:
    header: str
    level: int
    content: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "header": self.header,
            "level": self.level,
            "content": [
                item.to_dict() if isinstance(item, Section) else {"_text": item}
                for item in self.content
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Section":
        content = []
        for item in d.get("content", []):
            if isinstance(item, dict):
                if item.get("header") is not None:
                    content.append(cls.from_dict(item))
                elif "_text" in item:
                    content.append(item["_text"])
            elif isinstance(item, str):
                content.append(item)
        return cls(header=d["header"], level=d["level"], content=content)

    @property
    def markdown(self) -> str:
        parts = ["#" * self.level + f" {self.header}"]
        for item in self.content:
            parts.append(item.markdown if isinstance(item, Section) else item)
        return "\n\n".join(parts).rstrip()


@dataclass
class ParsedOpportunity:
    title: Optional[str] = None
    summary: Optional[str] = None
    sections: list[Section] = field(default_factory=list)
    metadata: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParsedOpportunity":
        return cls(
            title=d.get("title"),
            summary=d.get("summary"),
            sections=[Section.from_dict(s) for s in d.get("sections", [])],
            metadata=d.get("metadata"),
        )

    @property
    def markdown(self) -> str:
        parts = [f"# {self.title}"]
        if self.metadata:
            parts.append(self.metadata)
        if self.summary:
            parts.append(self.summary)
        if self.sections:
            parts.append("---")
            parts.append("\n\n---\n\n".join(s.markdown for s in self.sections))
        return "\n\n".join(parts)


def _render_metadata_table(metadata: dict) -> str | None:
    if not metadata:
        return None
    key_w = max(len(k) for k in metadata)
    val_w = max(len(_strip_links(v)) for v in metadata.values())
    header = f"| {'Field':<{key_w}} | {'Value':<{val_w}} |"
    sep = f"|{'-' * (key_w + 2)}|{'-' * (val_w + 2)}|"
    rows = "\n".join(
        f"| {k.replace('_', ' ').title():<{key_w}} | {v:<{val_w}} |"
        for k, v in metadata.items()
    )
    return f"{header}\n{sep}\n{rows}"


def _build_full_markdown(title: Optional[str], metadata_table: str | None, summary: Optional[str],
                         sections: list[Section], updates: list[dict]) -> str:
    parts = [f"# {title}"]
    if metadata_table:
        parts.append(metadata_table)
    if summary:
        parts.append(summary)
    if sections:
        parts.append("---")
        parts.append("\n\n---\n\n".join(s.markdown for s in sections))
    if updates:
        lines = [f"**{u['date']}** <br/>\n{u['text']}" for u in updates]
        parts.append("## Updates\n\n" + "\n\n".join(lines))
    return "\n\n".join(parts)


def parse_opportunity(html_string: str, base_url: str = "") -> dict | None:
    """Parse a UKRI opportunity HTML page into a dict. Returns None if unrecognised."""
    tree = lxml_html.fromstring(html_string)
    if base_url:
        tree.make_links_absolute(base_url)

    canonical_els = tree.xpath('//link[@rel="canonical"]/@href')
    main = tree.xpath('//*[@id="main-content"]')
    if not main:
        return None
    main = main[0]

    title_el = main.xpath('.//h1[@id="skipnav-target"]')
    title = title_el[0].text_content().strip() if title_el else None

    # Parse summary metadata table
    record: dict = {}
    metadata: dict = {}
    for row in main.xpath('.//dl[contains(@class,"opportunity__summary")]//div[contains(@class,"govuk-table__row")]'):
        dt = row.xpath("./dt/text()")
        if not dt:
            continue
        field_name = dt[0].strip().rstrip(":").replace(" ", "_").replace("-", "_").lower()
        raw_value = _strip_abbreviations(_to_markdown(row.xpath("./dd")[0]))
        schema_col = _METADATA_FIELD_MAP.get(field_name)
        if schema_col and schema_col not in record:
            record[schema_col] = _parse_metadata_value(field_name, raw_value)
            display = _strip_links(raw_value).strip()
            metadata[field_name] = ', '.join(
                re.sub(r'^[*\-]\s*', '', line).strip()
                for line in display.splitlines() if line.strip()
            )

    desc_els = main.xpath('.//div[contains(@class,"description")]')
    summary = _strip_abbreviations(_to_markdown(desc_els[0])) if desc_els else None

    # Accordion sections
    sections = []
    for sec_elem in main.xpath('.//div[contains(@class,"govuk-accordion__section")]'):
        header_el = sec_elem.xpath('.//*[contains(@class,"accordion__section-heading")]')
        content_el = sec_elem.xpath('.//*[contains(@class,"accordion__section-content")]')
        if not header_el or not content_el:
            continue
        section = Section(header=header_el[0].text_content().strip(), level=2)
        current_sub = None
        for elem in content_el[0]:
            if elem.tag in {f"h{i}" for i in range(1, 7)}:
                current_sub = Section(header=elem.text_content().strip(), level=int(elem.tag[1]))
                section.content.append(current_sub)
            else:
                md = _strip_abbreviations(_to_markdown(elem))
                (current_sub.content if current_sub else section.content).append(md)
        sections.append(section)

    # Updates
    updates = []
    for ul in main.xpath('.//ul[@class="opportunity-updates"]'):
        for li in ul:
            children = list(li)
            if len(children) >= 2:
                updates.append({"date": children[0].text or "", "text": children[1].tail or ""})

    # Standardise funder names
    for funder_key in ("funders", "co_funders"):
        if funder_key in record and isinstance(record[funder_key], list):
            record[funder_key] = [_standardise_funder(f) for f in record[funder_key]]

    # Strip abbreviation artefacts from text fields
    for key in ("minimum_award", "maximum_award", "total_funding", "funding_duration"):
        if key in record and isinstance(record[key], str):
            record[key] = _strip_abbreviations(record[key])

    metadata_table = _render_metadata_table(metadata)

    full_text = _strip_abbreviations(
        _build_full_markdown(title, metadata_table, summary, sections, updates)
    )

    href = canonical_els[0] if canonical_els else None
    id_source = href or title or full_text
    opp_id = uuid5(_NAMESPACE, id_source)

    record.update({
        "id": str(opp_id),
        "title": title,
        "raw_html": html_string,
        "summary": summary,
        "full_text": full_text,
        "updates": updates or None,
        "sections": [s.to_dict() for s in sections],
        "metadata": _render_metadata_table(metadata),
    })
    if href:
        record["href"] = href

    return record