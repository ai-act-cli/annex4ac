# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.2] - 2024-12-19

### Changed
- **BREAKING**: Review functionality moved to separate `annex4nlp` package
- **BREAKING**: Removed NLP dependencies (spacy, negspacy, nltk, PyPDF2, pdfplumber, PyMuPDF)
- **BREAKING**: CLI command `review` deprecated - use `annex4nlp` instead
- Updated package description to reflect core functionality focus
- Updated author name from "Aleksandr Racionaluss" to "Aleksandr Racionalus"

### Removed
- `review.py` module and all review-related functions
- NLP-related imports and dependencies
- Review command from CLI interface
- Review function exports from `__init__.py`

### Technical
- Package refactoring to separate concerns
- Core functionality (generate, validate, fetch-schema) remains in annex4ac
- Review functionality extracted to independent annex4nlp package
- Reduced package size and dependencies
- Improved maintainability through separation of concerns

### Migration
- Users requiring review functionality should install `annex4nlp` package
- Replace `annex4ac review` with `annex4nlp`
- Python API: import from `annex4nlp` instead of `annex4ac.review`

## [1.3.1] - Previous version

### Added
- Initial release with full functionality including review capabilities
- PDF generation, HTML/DOCX export
- YAML validation and schema management
- NLP-based compliance analysis
- CLI interface with Typer
- GitHub Actions integration
- License system for Pro features 