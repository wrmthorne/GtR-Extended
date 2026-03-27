from enum import Enum


# ====================================
# VOCABULARY MODELS
# ====================================


# TODO someday look at Enum inheritance (if that exists?)
class OutcomeType(str, Enum):
    SPIN_OUT = "spinOut"
    PRODUCT = "product"
    KEY_FINDING = "keyFinding"
    PUBLICATION = "publication"
    COLLABORATION = "collaboration"
    DISSEMINATION = "dissemination"
    FURTHER_FUNDING = "futherfunding"
    EXPLOITATION = "exploitation"
    IMPACT_SUMMARY = "impactSummary"
    INTELLECTUAL_PROPERTY = "intellectualProperty"
    POLICY_INFLUENCE = "policyInfluence"
    OTHER_RESEARCH_ITEM = "otherResearchItem"
    RESEARCH_MATERIAL = "researchMaterial"
    ARTISTIC_AND_CREATIVE_PRODUCT = "artisticAndCreativeProduct"
    RESEARCH_DATABASE_AND_MODEL = "researchDatabaseAndModel"
    SOFTWARE_AND_TECHNICAL_PRODUCT = "softwareAndTechnicalProduct"


class ResourceType(str, Enum):
    """Enumeration for all resource types in the UKRI GtR database."""

    PERSON = "person"
    ORGANISATION = "organisation"
    PROJECT = "project"
    FUND = "fund"
    ADDRESS = "address"


class PersonRoles(str, Enum):
    """The linked person is the Principal Investigator."""

    PI_PER = "Principal Investigator"
    """The linked person is the Co-Investigator."""
    COLI_PER = "Co-Investigator"
    """The linked person is a Project Manager"""
    PM_PER = "Project Manager"
    """The linked person is a Fellow"""
    FELLOW_PER = "Fellow"
    """The linked person is an Employee"""
    EMPLOYEE = "Employee"
    """The link to the person's ORCID id details"""
    ORCID_ID = "ORCID ID"  # Not used as we have defined this as a property now
    RESEARCH_PER = "Researcher"
    TGH_PER = "Technical Investigator"  # TODO find out if this assumption is correct
    SUPER_PER = "Supervisor"
    RESEARCH_COI_PER = "Research Co-Investigator"
    STUDENT_PER = "Student"


class OrganisationRoles(str, Enum):
    """The linked organisation is the employer"""

    EMPLOYED = "Employed"
    """The linked organisation is the Lead Research Organisation"""
    LEAD_ORG = "Lead Organization"
    """The linked organisation is a Collaborating Organisation"""
    COLLAB_ORG = "Collaborative Organization"
    """The linked organisation is a Fellow Organisation"""
    FELLOW_ORG = "Fellow Organization"
    """The linked organisation is a Co-Funder"""
    COFUND_ORG = "CoFund Organization"
    """The linked organisation is a Project Partner"""
    PP_ORG = "Project Partner"
    """The linked organisation is a Funder"""
    FUNDER = "Funder"
    PARTICIPANT_ORG = "Participant Organization"
    STUDENT_PP_ORG = "Student Participant Organization"


# https://gtr.ukri.org/resources/GtR-2-API-v1.7.4.pdf
class RelationshipType(str, Enum):
    # persons
    """The linked person is the Principal Investigator."""

    PI_PER = "Principal Investigator"
    """The linked person is the Co-Investigator."""
    COLI_PER = "Co-Investigator"
    """The linked person is a Project Manager"""
    PM_PER = "Project Manager"
    """The linked person is a Fellow"""
    FELLOW_PER = "Fellow"
    """The linked person is an Employee"""
    EMPLOYEE = "Employee"
    """The link to the person's ORCID id details"""
    ORCID_ID = "ORCID ID"  # Not used as we have defined this as a property now
    RESEARCH_PER = "Researcher"
    TGH_PER = "Technical Investigator"  # TODO find out if this assumption is correct
    SUPER_PER = "Supervisor"
    RESEARCH_COI_PER = "Research Co-Investigator"

    # organisations
    """The linked organisation is the employer"""
    EMPLOYED = "Employed"
    """The linked organisation is the Lead Research Organisation"""
    LEAD_ORG = "Lead Organization"
    """The linked organisation is a Collaborating Organisation"""
    COLLAB_ORG = "Collaborative Organization"
    """The linked organisation is a Fellow Organisation"""
    FELLOW_ORG = "Fellow Organization"
    """The linked organisation is a Co-Funder"""
    COFUND_ORG = "CoFund Organization"
    """The linked organisation is a Project Partner"""
    PP_ORG = "Project Partner"
    """The linked organisation is a Funder"""
    FUNDER = "Funder"

    # outcomes
    """The linked outcome is an Artistic and Creative Product"""
    ARTISTIC_AND_CREATIVE_PRODUCT = "Artistic and Creative Product"
    """The linked outcome is a Collaboration"""
    COLLABORATION = "Collaboration"
    """The linked outcome is a Dissemination"""
    DISSEMINATION = "Dissemination"
    """The linked outcome is a Further Funding"""
    FURTHER_FUNDING = "Further Funding"
    """The linked outcome is an Impact Summary"""
    IMPACT_SUMMARY = "Impact Summary"
    """The linked outcome is an Intellectual Property"""
    IP = "Intellectual Property"
    """The linked outcome is a Key Finding"""
    KEY_FINDING = "Key Finding"
    """The linked outcome is a Policy Influence"""
    POLICY = "Policy Influence"
    """The linked outcome is a Product Intervention"""
    PRODUCT = "Product"
    """The linked outcome is a Publication"""
    PUBLICATION = "Publication"
    """The linked outcome is a Research Database and Model"""
    RESEARCH_DATABASE_AND_MODEL = "Research Database and Model"
    """The linked outcome is a Research Material"""
    RESEARCH_MATERIAL = "Research Material"
    """The linked outcome is a Software and Technical Product"""
    SOFTWARE_AND_TECHNICAL_PRODUCT = "Software and Technical Product"
    """The linked outcome is a Spin Out"""
    SPIN_OUT = "Spin Out"

    # funds
    """The linked Fund"""
    FUND = "Fund"

    # projects
    """The linked Project"""
    PROJECT = "Project"


# https://gtr.ukri.org/resources/GtR-User-Guide.docx
class Funder(str, Enum):
    AHRC = "AHRC"
    BBSRC = "BBSRC"
    EPSRC = "EPSRC"
    ESRC = "ESRC"
    MRC = "MRC"
    NERC = "NERC"
    STFC = "STFC"
    NC3RS = "NC3Rs"
    UKRI = "UKRI"
    COVID = "Covid"
    FIC = "FIC"
    FLF = "FLF"
    GCRF = "GCRF"
    HEG = "HEG"
    ISCF = "ISCF"
    NEWTON_FUND = "NewtonFund"
    SPF = "SPF"
    UUI = "UUI"
    INNOVATE_UK = "InnovateUK"
