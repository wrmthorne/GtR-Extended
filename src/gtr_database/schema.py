from typing import List, Optional
from uuid import UUID, uuid4
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import (
    ForeignKey, String, Text, Numeric, Enum, JSON,
    Boolean, BigInteger, Integer, Interval, ARRAY
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import DateTime, Date

from .vocabularies import OutcomeType


class Base(DeclarativeBase):
    pass


# --- CORE HIERARCHY ---

class Opportunity(Base):
    __tablename__ = "opportunities"

    # --- Identifiers & Links ---
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    opportunity_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    href: Mapped[str] = mapped_column(String)

    # --- Content ---
    title: Mapped[Optional[str]] = mapped_column(String)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    full_text: Mapped[Optional[str]] = mapped_column(Text)
    raw_html: Mapped[Optional[str]] = mapped_column(Text)
    metadata_table: Mapped[Optional[str]] = mapped_column(Text)
    updates: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    sections: Mapped[Optional[List[dict]]] = mapped_column(JSON, nullable=True)

    # --- Status & Dates ---
    status: Mapped[Optional[str]] = mapped_column(String)
    publication_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    opening_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    closing_date: Mapped[Optional[datetime]] = mapped_column(DateTime)

    funding_type: Mapped[Optional[str]] = mapped_column(String)

    # --- Participants ---
    funders: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String))
    co_funders: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String))

    # --- Extracted Numeric Fields ---
    minimum_award: Mapped[Optional[int]] = mapped_column(BigInteger)
    maximum_award: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_funding: Mapped[Optional[int]] = mapped_column(BigInteger)
    funding_percentage: Mapped[Optional[int]] = mapped_column(Integer)
    minimum_funding_duration: Mapped[Optional[timedelta]] = mapped_column(Interval)
    maximum_funding_duration: Mapped[Optional[timedelta]] = mapped_column(Interval)

    # --- Relationships ---
    projects: Mapped[List["Project"]] = relationship(back_populates="opportunity")
    applications: Mapped[List["Application"]] = relationship(back_populates="opportunity")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    opportunity_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("opportunities.id"))
    title: Mapped[str] = mapped_column(String)
    abstract: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(String)
    grant_category: Mapped[Optional[str]] = mapped_column(String)
    lead_funder: Mapped[Optional[str]] = mapped_column(String)
    lead_organisation_department: Mapped[Optional[str]] = mapped_column(String)
    grant_reference: Mapped[Optional[str]] = mapped_column(String, index=True)

    funds: Mapped[List["ProjectFund"]] = relationship(back_populates="project")
    outcomes: Mapped[List["Outcome"]] = relationship(back_populates="project")
    taxonomies: Mapped[List["ProjectTaxonomy"]] = relationship(back_populates="project")
    applications: Mapped[List["Application"]] = relationship(back_populates="project")
    opportunity: Mapped[Optional["Opportunity"]] = relationship(back_populates="projects")
    partners: Mapped[List["ProjectPartner"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    members: Mapped[List["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    relationships_from: Mapped[List["ProjectRelationship"]] = relationship(
        back_populates="from_project", foreign_keys="ProjectRelationship.from_project_id"
    )
    relationships_to: Mapped[List["ProjectRelationship"]] = relationship(
        back_populates="to_project", foreign_keys="ProjectRelationship.to_project_id"
    )


class ProjectRelationship(Base):
    __tablename__ = "project_relationships"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    from_project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    to_project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    relationship_type: Mapped[str] = mapped_column(String, index=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)

    from_project: Mapped["Project"] = relationship(back_populates="relationships_from", foreign_keys=[from_project_id])
    to_project: Mapped["Project"] = relationship(back_populates="relationships_to", foreign_keys=[to_project_id])


class ProjectFund(Base):
    __tablename__ = "project_funds"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)

    category: Mapped[Optional[str]] = mapped_column(String)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    currency_code: Mapped[Optional[str]] = mapped_column(String(3), default="GBP")
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    href: Mapped[Optional[str]] = mapped_column(String)

    project: Mapped["Project"] = relationship("Project", back_populates="funds")


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    first_name: Mapped[Optional[str]] = mapped_column(String)
    surname: Mapped[Optional[str]] = mapped_column(String)

    orcid: Mapped[Optional[str]] = mapped_column(String, unique=True)

    organisation_links: Mapped[List["PersonOrganisationLink"]] = relationship(back_populates="person")
    project_history: Mapped[List["ProjectMember"]] = relationship(back_populates="person")
    panellist_appearances: Mapped[List["PanelAttendance"]] = relationship(back_populates="person")


class ProjectMember(Base):
    __tablename__ = "project_members"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), index=True)
    partner_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("project_partners.id"))
    role: Mapped[Optional[str]] = mapped_column(String)

    project: Mapped["Project"] = relationship(back_populates="members")
    person: Mapped["Person"] = relationship(back_populates="project_history")
    partner: Mapped[Optional["ProjectPartner"]] = relationship(back_populates="members")


class Organisation(Base):
    __tablename__ = "organisations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String, index=True)
    reg_number: Mapped[Optional[str]] = mapped_column(String)
    website: Mapped[Optional[str]] = mapped_column(String)

    addresses: Mapped[List["Address"]] = relationship(back_populates="organisation")
    project_links: Mapped[List["ProjectPartner"]] = relationship(back_populates="organisation")
    person_links: Mapped[List["PersonOrganisationLink"]] = relationship(back_populates="organisation")


class Address(Base):
    __tablename__ = "addresses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organisation_id: Mapped[UUID] = mapped_column(ForeignKey("organisations.id"), index=True)
    type: Mapped[Optional[str]] = mapped_column(String)
    line1: Mapped[Optional[str]] = mapped_column(String)
    line2: Mapped[Optional[str]] = mapped_column(String)
    line3: Mapped[Optional[str]] = mapped_column(String)
    line4: Mapped[Optional[str]] = mapped_column(String)
    line5: Mapped[Optional[str]] = mapped_column(String)
    city: Mapped[Optional[str]] = mapped_column(String)
    county: Mapped[Optional[str]] = mapped_column(String)
    post_code: Mapped[Optional[str]] = mapped_column(String)
    region: Mapped[Optional[str]] = mapped_column(String)
    country: Mapped[Optional[str]] = mapped_column(String)

    organisation: Mapped["Organisation"] = relationship(back_populates="addresses")


class ProjectPartner(Base):
    __tablename__ = "project_partners"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    organisation_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("organisations.id"))
    role: Mapped[Optional[str]] = mapped_column(String)
    project_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    grant_offer: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    source_organisation_name: Mapped[Optional[str]] = mapped_column(String)

    project: Mapped["Project"] = relationship(back_populates="partners")
    organisation: Mapped[Optional["Organisation"]] = relationship(back_populates="project_links")
    members: Mapped[List["ProjectMember"]] = relationship(back_populates="partner")


class PersonOrganisationLink(Base):
    __tablename__ = "person_organisation_links"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), index=True)
    organisation_id: Mapped[UUID] = mapped_column(ForeignKey("organisations.id"), index=True)
    role: Mapped[Optional[str]] = mapped_column(String)
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)

    person: Mapped["Person"] = relationship(back_populates="organisation_links")
    organisation: Mapped["Organisation"] = relationship(back_populates="person_links")


# --- OUTCOMES (Joined Table Inheritance) ---

class Outcome(Base):
    __tablename__ = "outcomes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        index=True
    )

    title: Mapped[Optional[str]] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text)
    impact: Mapped[Optional[str]] = mapped_column(Text)
    supporting_url: Mapped[Optional[str]] = mapped_column(String)
    outcome_type: Mapped[OutcomeType] = mapped_column(Enum(OutcomeType, native_enum=True), index=True)

    project: Mapped[Optional["Project"]] = relationship(back_populates="outcomes")

    __mapper_args__ = {
        "polymorphic_on": "outcome_type",
        "polymorphic_identity": "outcome_base"
    }


class KeyFinding(Outcome):
    __tablename__ = "outcome_key_findings"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    non_academic_uses: Mapped[Optional[str]] = mapped_column(Text)
    exploitation_pathways: Mapped[Optional[str]] = mapped_column(Text)
    sectors: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))

    __mapper_args__ = {"polymorphic_identity": OutcomeType.KEY_FINDING}


class Publication(Outcome):
    __tablename__ = "outcome_publications"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    abstract_text: Mapped[Optional[str]] = mapped_column(Text)
    other_information: Mapped[Optional[str]] = mapped_column(Text)
    journal_title: Mapped[Optional[str]] = mapped_column(String)
    date_published: Mapped[Optional[datetime]] = mapped_column(DateTime)
    publication_url: Mapped[Optional[str]] = mapped_column(String)
    pub_med_id: Mapped[Optional[str]] = mapped_column(String)
    isbn: Mapped[Optional[str]] = mapped_column(String)
    issn: Mapped[Optional[str]] = mapped_column(String)
    doi: Mapped[Optional[str]] = mapped_column(String)
    author: Mapped[Optional[str]] = mapped_column(String)

    series_number: Mapped[Optional[str]] = mapped_column(String)
    series_title: Mapped[Optional[str]] = mapped_column(String)
    sub_title: Mapped[Optional[str]] = mapped_column(String)
    volume_title: Mapped[Optional[str]] = mapped_column(String)
    volume_number: Mapped[Optional[str]] = mapped_column(String)
    issue: Mapped[Optional[str]] = mapped_column(String)
    total_pages: Mapped[Optional[str]] = mapped_column(String)
    edition: Mapped[Optional[str]] = mapped_column(String)
    chapter_number: Mapped[Optional[str]] = mapped_column(String)
    chapter_title: Mapped[Optional[str]] = mapped_column(String)
    page_reference: Mapped[Optional[str]] = mapped_column(String)
    conference_event: Mapped[Optional[str]] = mapped_column(String)
    conference_location: Mapped[Optional[str]] = mapped_column(String)
    conference_number: Mapped[Optional[str]] = mapped_column(String)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.PUBLICATION}


class Collaboration(Outcome):
    __tablename__ = "outcome_collaborations"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    parent_organisation: Mapped[Optional[str]] = mapped_column(String)
    child_organisation: Mapped[Optional[str]] = mapped_column(String)
    principal_investigator_contribution: Mapped[Optional[str]] = mapped_column(Text)
    partner_contribution: Mapped[Optional[str]] = mapped_column(Text)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sector: Mapped[Optional[str]] = mapped_column(String)
    country: Mapped[Optional[str]] = mapped_column(String)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.COLLABORATION}


class Dissemination(Outcome):
    __tablename__ = "outcome_disseminations"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    form: Mapped[Optional[str]] = mapped_column(String)
    primary_audience: Mapped[Optional[str]] = mapped_column(String)
    years_of_dissemination: Mapped[Optional[str]] = mapped_column(String)
    results: Mapped[Optional[str]] = mapped_column(Text)
    type_of_presentation: Mapped[Optional[str]] = mapped_column(String)
    geographic_reach: Mapped[Optional[str]] = mapped_column(String)
    part_of_official_scheme: Mapped[Optional[bool]] = mapped_column(Boolean)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.DISSEMINATION}


class FurtherFunding(Outcome):
    __tablename__ = "outcome_further_funding"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    narrative: Mapped[Optional[str]] = mapped_column(Text)
    organisation: Mapped[Optional[str]] = mapped_column(String)
    department: Mapped[Optional[str]] = mapped_column(String)
    funding_id: Mapped[Optional[str]] = mapped_column(String)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sector: Mapped[Optional[str]] = mapped_column(String)
    country: Mapped[Optional[str]] = mapped_column(String)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    currency_code: Mapped[Optional[str]] = mapped_column(String(3), default="GBP")

    __mapper_args__ = {"polymorphic_identity": OutcomeType.FURTHER_FUNDING}


class Exploitation(Outcome):
    __tablename__ = "outcome_exploitations"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    method: Mapped[Optional[str]] = mapped_column(String)
    other_involvement: Mapped[Optional[str]] = mapped_column(Text)
    ip_exploited: Mapped[Optional[bool]] = mapped_column(Boolean)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.EXPLOITATION}


class ImpactSummary(Outcome):
    __tablename__ = "outcome_impact_summaries"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    impact_types: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    beneficiaries: Mapped[Optional[str]] = mapped_column(Text)
    contribution_method: Mapped[Optional[str]] = mapped_column(Text)
    sector: Mapped[Optional[str]] = mapped_column(String)
    first_year_of_impact: Mapped[Optional[int]] = mapped_column(Integer)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.IMPACT_SUMMARY}


class IntellectualProperty(Outcome):
    __tablename__ = "outcome_intellectual_properties"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    protection: Mapped[Optional[str]] = mapped_column(String)
    patent_id: Mapped[Optional[str]] = mapped_column(String)
    year_protection_granted: Mapped[Optional[int]] = mapped_column(Integer)
    licensed: Mapped[Optional[str]] = mapped_column(String)
    patent_url: Mapped[Optional[str]] = mapped_column(String)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.INTELLECTUAL_PROPERTY}


class PolicyInfluence(Outcome):
    __tablename__ = "outcome_policy_influences"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    influence: Mapped[Optional[str]] = mapped_column(Text)
    policy_type: Mapped[Optional[str]] = mapped_column(String)
    guideline_title: Mapped[Optional[str]] = mapped_column(String)
    methods: Mapped[Optional[str]] = mapped_column(Text)
    areas: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    geographic_reach: Mapped[Optional[str]] = mapped_column(String)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.POLICY_INFLUENCE}


class Product(Outcome):
    __tablename__ = "outcome_products"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    stage: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[Optional[str]] = mapped_column(String)
    clinical_trial: Mapped[Optional[bool]] = mapped_column(Boolean)
    ukcrn_isctn_id: Mapped[Optional[str]] = mapped_column(String)
    year_development_completed: Mapped[Optional[int]] = mapped_column(Integer)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.PRODUCT}


class ResearchMaterial(Outcome):
    __tablename__ = "outcome_research_materials"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    software_developed: Mapped[Optional[bool]] = mapped_column(Boolean)
    software_open_sourced: Mapped[Optional[bool]] = mapped_column(Boolean)
    provided_to_others: Mapped[Optional[bool]] = mapped_column(Boolean)
    year_first_provided: Mapped[Optional[int]] = mapped_column(Integer)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.RESEARCH_MATERIAL}


class ArtisticAndCreativeProduct(Outcome):
    __tablename__ = "outcome_artistic_products"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    year_first_provided: Mapped[Optional[int]] = mapped_column(Integer)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.ARTISTIC_AND_CREATIVE_PRODUCT}


class ResearchDatabaseAndModel(Outcome):
    __tablename__ = "outcome_research_databases"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    provided_to_others: Mapped[Optional[bool]] = mapped_column(Boolean)
    year_first_provided: Mapped[Optional[int]] = mapped_column(Integer)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.RESEARCH_DATABASE_AND_MODEL}


class SoftwareAndTechnicalProduct(Outcome):
    __tablename__ = "outcome_software_products"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    software_open_sourced: Mapped[Optional[bool]] = mapped_column(Boolean)
    open_source_license: Mapped[Optional[bool]] = mapped_column(Boolean)
    provided_to_others: Mapped[Optional[bool]] = mapped_column(Boolean)
    year_first_provided: Mapped[Optional[int]] = mapped_column(Integer)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.SOFTWARE_AND_TECHNICAL_PRODUCT}


class SpinOut(Outcome):
    __tablename__ = "outcome_spin_outs"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    company_name: Mapped[Optional[str]] = mapped_column(String)
    company_description: Mapped[Optional[str]] = mapped_column(Text)
    website: Mapped[Optional[str]] = mapped_column(String)
    registration_number: Mapped[Optional[str]] = mapped_column(String)
    year_established: Mapped[Optional[str]] = mapped_column(String)
    ip_exploited: Mapped[Optional[bool]] = mapped_column(Boolean)
    joint_venture: Mapped[Optional[bool]] = mapped_column(Boolean)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.SPIN_OUT}


class OtherResearchItem(Outcome):
    __tablename__ = "outcome_other_items"

    id: Mapped[UUID] = mapped_column(ForeignKey("outcomes.id"), primary_key=True)
    sub_title: Mapped[Optional[str]] = mapped_column(String)
    series_title: Mapped[Optional[str]] = mapped_column(String)
    series_number: Mapped[Optional[str]] = mapped_column(String)
    other_information: Mapped[Optional[str]] = mapped_column(Text)
    edition: Mapped[Optional[str]] = mapped_column(String)
    doi: Mapped[Optional[str]] = mapped_column(String)
    publisher: Mapped[Optional[str]] = mapped_column(String)

    __mapper_args__ = {"polymorphic_identity": OutcomeType.OTHER_RESEARCH_ITEM}


# --- MEETINGS ---

class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    meeting_name: Mapped[Optional[str]] = mapped_column(String)
    panel_reference: Mapped[Optional[str]] = mapped_column(String, index=True)
    meeting_start: Mapped[Optional[datetime]] = mapped_column(DateTime)
    meeting_end: Mapped[Optional[datetime]] = mapped_column(DateTime)
    meeting_convenor: Mapped[Optional[str]] = mapped_column(String)
    council: Mapped[Optional[str]] = mapped_column(String)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    source: Mapped[Optional[str]] = mapped_column(String)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    panel_attendance: Mapped[List["PanelAttendance"]] = relationship(back_populates="meeting")
    meeting_applications: Mapped[List["MeetingApplication"]] = relationship(back_populates="meeting")


class PanelAttendance(Base):
    __tablename__ = "panel_attendance"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    meeting_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("meetings.id"), index=True)
    person_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("persons.id"), index=True)

    source_panellist_name: Mapped[Optional[str]] = mapped_column(String)
    organisation_name: Mapped[Optional[str]] = mapped_column(String)
    panel_role: Mapped[Optional[str]] = mapped_column(String)
    subpanel: Mapped[Optional[str]] = mapped_column(String)
    council: Mapped[Optional[str]] = mapped_column(String)
    source: Mapped[Optional[str]] = mapped_column(String)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    meeting: Mapped[Optional["Meeting"]] = relationship(back_populates="panel_attendance")
    person: Mapped[Optional["Person"]] = relationship(back_populates="panellist_appearances")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    opportunity_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("opportunities.id"), index=True)
    project_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("projects.id"), index=True)

    application_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    award_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    application_title: Mapped[Optional[str]] = mapped_column(Text)
    lead_applicant: Mapped[Optional[str]] = mapped_column(String)
    lead_organisation: Mapped[Optional[str]] = mapped_column(String)
    opportunity_number: Mapped[Optional[str]] = mapped_column(String)
    opportunity_name: Mapped[Optional[str]] = mapped_column(String)
    council: Mapped[Optional[str]] = mapped_column(String)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    source: Mapped[Optional[str]] = mapped_column(String)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    opportunity: Mapped[Optional["Opportunity"]] = relationship(back_populates="applications")
    project: Mapped[Optional["Project"]] = relationship(back_populates="applications")
    meeting_appearances: Mapped[List["MeetingApplication"]] = relationship(back_populates="application")


class MeetingApplication(Base):
    __tablename__ = "meeting_applications"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    meeting_id: Mapped[UUID] = mapped_column(ForeignKey("meetings.id"), index=True)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)

    rank: Mapped[Optional[int]] = mapped_column(Integer)
    rank_group: Mapped[Optional[str]] = mapped_column(String)
    outcome: Mapped[Optional[str]] = mapped_column(String)
    awarded_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    score: Mapped[Optional[str]] = mapped_column(String)
    source: Mapped[Optional[str]] = mapped_column(String)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    meeting: Mapped["Meeting"] = relationship(back_populates="meeting_applications")
    application: Mapped["Application"] = relationship(back_populates="meeting_appearances")


# --- TAXONOMIES ---

class ProjectTaxonomy(Base):
    __tablename__ = "project_taxonomies"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))

    taxonomy_type: Mapped[str] = mapped_column(String, index=True)
    label: Mapped[Optional[str]] = mapped_column(String)
    percentage: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))

    project: Mapped["Project"] = relationship(back_populates="taxonomies")
