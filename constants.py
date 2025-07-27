"""
Constants for Annex4AC project.
"""

# Schema version (automatically added if not provided)
SCHEMA_VERSION = "20240726"

# Document control fields (common metadata)
DOC_CTRL_FIELDS = [
    ("AI system placed on market / put into service",   "placed_on_market"),
    ("Retention until (Art. 18)",                       "retention_until"),
    ("Enterprise size (Art. 11(6))",                    "enterprise_size"),
    ("Risk level (Art. 6 / Annex III)",                 "risk_level"),
    ("Technical documentation last updated",            "last_updated"),
    ("Schema version (Annex IV)",                       "_schema_version"),
    ("Document generated (timestamp)",                  "generation_date"),
]

# Legal reference for retention period calculation
# Article 18(1): "...for a period ending 10 years after the AI system has been placed on the market or put into service..."
# Source: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32024R1689

# Annex IV section titles
SECTION_TITLES = [
    "1. A general description of the AI system including:",
    "2. A detailed description of the elements of the AI system and of the process for its development, including:",
    "3. Detailed information about the monitoring, functioning and control of the AI system, in particular with regard to:",
    "4. A description of the appropriateness of the performance metrics for the specific AI system:",
    "5. A detailed description of the risk management system in accordance with Article 9:",
    "6. A description of relevant changes made by the provider to the system through its lifecycle:",
    "7. A list of the harmonised standards applied in full or in part the references of which have been published in the Official Journal of the European Union; where no such harmonised standards have been applied, a detailed description of the solutions adopted to meet the requirements set out in Chapter III, Section 2, including a list of other relevant standards and technical specifications applied:",
    "8. A copy of the EU declaration of conformity referred to in Article 47:",
    "9. A detailed description of the system in place to evaluate the AI-system performance in the post-market phase in accordance with Article 72, including the post-market monitoring plan referred to in Article 72(3):",
]

# Annex IV section keys
SECTION_KEYS = [
    "system_overview",
    "development_process", 
    "system_monitoring",
    "performance_metrics",
    "risk_management",
    "changes_and_versions",
    "standards_applied",
    "compliance_declaration",
    "post_market_plan",
]

# Create mapping from titles and keys
SECTION_MAPPING = list(zip(SECTION_TITLES, SECTION_KEYS)) 