"""
Review module for annex4ac - analyze PDF documents for compliance issues.

This module provides library functions for analyzing technical documentation
for EU AI Act Annex IV and GDPR compliance issues.
"""

import re
from pathlib import Path
from typing import List, Tuple

# Import spaCy and negspaCy for advanced contradiction detection
try:
    import spacy
    from negspacy.negation import Negex
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

# Import PDF processing libraries with fallbacks
try:
    import PyPDF2
    PDF2_AVAILABLE = True
except ImportError:
    PDF2_AVAILABLE = False

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

# ---------------- spaCy / negspaCy singleton ----------------
_nlp = None

# Function for improved negation detection with word boundaries
def _check_negation_regex(text: str, term: str) -> bool:
    """
    Checks for term negation using regex and word boundaries.
    Supports both regular spaces and hyphens in terms.
    
    Args:
        text: Text to analyze (already in lowercase)
        term: Term to check
        
    Returns:
        True if term is negated, False otherwise
        
    Examples:
        "not only personal data" -> False (not negation)
        "no personal data stored" -> True (negation)
        "without personal data" -> True (negation)
        "no personal-data stored" -> True (negation with hyphen)
    """
    import re
    
    # List of negating words
    negation_words = ["no", "not", "without", "never", "none", "neither", "nor"]
    
    # Normalize term for search (replace hyphens with spaces)
    normalized_term = _normalize_term(term)
    
    # Escape special characters in the term
    escaped_term = re.escape(normalized_term)
    
    # Create pattern with word boundaries
    # \b - word boundary
    # (?:...) - non-capturing group
    # \s+ - one or more spaces
    pattern = rf'\b(?:{"|".join(negation_words)})\s+{escaped_term}\b'
    
    # Check in normalized text
    text_normalized = text.replace('-', ' ')
    return bool(re.search(pattern, text_normalized, re.IGNORECASE))

# Define key terms for compliance analysis
# Only meaningful bigrams/trigrams (single-word stop-tokens removed)
# Deduplicated by prefixes for PhraseMatcher optimization
KEY_TERMS = [
    # Bigrams and trigrams - only meaningful terms
    "personal data", "high risk", "high-risk", "biometric identification", "data protection",
    "risk assessment", "compliance check", "data processing", "user consent",
    "lawful basis", "data retention", "access control", "audit trail",
    "privacy policy", "data breach", "security measure", "encryption key",
    "access right", "data subject", "processing purpose", "legal basis",
    "post-market monitoring", "market surveillance", "market access",
    "data controller", "data processor", "data protection officer", "DPO",
    "data protection impact assessment", "DPIA", "privacy impact assessment",
    "data minimization", "purpose limitation", "storage limitation", "accuracy",
    "integrity", "confidentiality", "availability", "accountability",
    "transparency", "fairness", "lawfulness", "legitimate interest",
    "vital interest", "public interest", "legal obligation", "contract",
    "consent withdrawal", "data portability", "right to erasure", "right to rectification",
    "right to restriction", "right to object", "automated decision-making",
    "profiling", "special categories", "sensitive data", "criminal data",
    "cross-border processing", "international transfer", "third country",
    "adequacy decision", "standard contractual clauses", "binding corporate rules",
    "certification mechanism", "code of conduct", "supervisory authority",
    "lead supervisory authority", "one-stop-shop", "consistency mechanism",
    "data protection by design", "data protection by default", "privacy by design",
    "technical measures", "organizational measures", "security controls",
    "access controls", "authentication", "authorization", "encryption at rest",
    "encryption in transit", "backup procedures", "disaster recovery",
    "incident response", "breach notification", "data breach notification",
    "72-hour notification", "documentation requirements", "record keeping",
    "audit requirements", "compliance monitoring", "regular review",
    "periodic assessment", "risk management", "risk mitigation", "risk controls"
]

def _normalize_term(term: str) -> str:
    """
    Normalizes a term by converting hyphenated variants to a unified form.
    
    Args:
        term: Original term
        
    Returns:
        Normalized term
        
    Examples:
        "high-risk" -> "high risk"
        "data-protection" -> "data protection"
        "personal data" -> "personal data" (no changes)
    """
    # Replace hyphens with spaces for consistency
    normalized = term.replace("-", " ")
    # Remove extra spaces
    normalized = " ".join(normalized.split())
    return normalized

def _deduplicate_terms(terms):
    """
    Removes terms that are prefixes of other terms or normalized variants.
    Uses simple normalization for reliability.
    
    Args:
        terms: List of terms to deduplicate
        
    Returns:
        List of unique terms
    """
    # Use simple normalization for reliability
    normalized_terms = {}
    for term in terms:
        normalized = _normalize_term(term)
        # Save the longest variant as the main one
        if normalized not in normalized_terms or len(term) > len(normalized_terms[normalized]):
            normalized_terms[normalized] = term
    
    # Apply prefix deduplication
    sorted_terms = sorted(normalized_terms.values(), key=len, reverse=True)
    filtered_terms = []
    
    for term in sorted_terms:
        # Check if this term is a prefix of already added terms
        is_prefix = False
        for existing in filtered_terms:
            if existing.startswith(term + " "):
                is_prefix = True
                break
        if not is_prefix:
            filtered_terms.append(term)
    
    return filtered_terms

# Apply deduplication
KEY_TERMS = _deduplicate_terms(KEY_TERMS)

# 1 – Create term_matcher component with lemmatized matching
from spacy.language import Language
from spacy.matcher import PhraseMatcher
from spacy.tokens import Span
from spacy.util import filter_spans

@Language.factory("term_matcher")
def create_term_matcher(nlp, name):
    # Use LEMMA for lemmatized search (store/stored/storing)
    matcher = PhraseMatcher(nlp.vocab, attr="LEMMA")
    
    # Add terms with lemmatization and normalization
    # PhraseMatcher with LEMMA works with prepared documents
    patterns = []
    for term in KEY_TERMS:
        # Create document for each term (single word or bigram)
        doc_pattern = nlp(term)
        if len(doc_pattern) > 0:
            patterns.append(doc_pattern)
        
        # Also add normalized version for better coverage
        normalized_term = _normalize_term(term)
        if normalized_term != term:
            doc_normalized = nlp(normalized_term)
            if len(doc_normalized) > 0:
                patterns.append(doc_normalized)
    
    matcher.add("COMPLIANCE_TERM", patterns)

    def component(doc):
        matches = matcher(doc)
        spans = [Span(doc, start, end, label="TERM") for _, start, end in matches]
        
        # Use built-in filter_spans function for optimization
        # Priority to longer spans (default)
        filtered_spans = filter_spans(spans)
        
        # Use doc.set_ents to preserve NER result integrity
        # default="unmodified" - preserve existing entities (PERSON/ORG/NER)
        # This is critical for negspaCy, which may use these labels for negation detection
        doc.set_ents(filtered_spans, default="unmodified")
        return doc
    return component

# Custom NegEx rules for GDPR/compliance context
from negspacy.negation import Negex

# Custom rules for GDPR/compliance context
CUSTOM_PSEUDO_NEGATIONS = [
    "lawful", "legal", "legitimate", "authorized", "permitted", 
    "valid", "proper", "correct", "appropriate"
]  # «no _lawful_ basis» ≠ negation

CUSTOM_PRECEDING_NEGATIONS = [
    "without", "absence of", "lack of", "failure to", "not", "no", 
    "never", "none", "neither", "nor"
]  # GDPR cases

CUSTOM_FOLLOWING_NEGATIONS = [
    "denied", "rejected", "prohibited", "forbidden", "excluded"
]  # Additional following negations for GDPR

def _get_nlp(batch_size: int = 128):
    """
    Get or create spaCy NLP pipeline with custom components.
    
    Args:
        batch_size: Number of pages to process in each batch (default: 128)
        
    Returns:
        spaCy NLP pipeline with custom components
    """
    global _nlp
    
    # If _nlp is already created but with different batch_size, recreate it
    if _nlp is not None and hasattr(_nlp, '_batch_size') and _nlp._batch_size != batch_size:
        _nlp = None
    
    if _nlp is not None:
        return _nlp
    
    if not SPACY_AVAILABLE:
        return None
    
    # Load spaCy model
    _nlp = spacy.blank("en")
    _nlp.initialize()    
    # Add custom tokenizer that preserves hyphenated terms
    from spacy.tokenizer import Tokenizer
    from spacy.util import compile_prefix_regex, compile_suffix_regex, compile_infix_regex
    
    def custom_tokenizer(nlp):
        """Custom tokenizer that doesn't split hyphenated words"""
        prefix_re = compile_prefix_regex(nlp.Defaults.prefixes)
        suffix_re = compile_suffix_regex(nlp.Defaults.suffixes)
        infix = list(nlp.Defaults.infixes)
        
        # Remove hyphen from infix split so "high-risk" remains one token
        if r"(?<=[0-9])[+\-\*^](?=[0-9-])" in infix:
            infix.remove(r"(?<=[0-9])[+\-\*^](?=[0-9-])")
        
        # Also remove general hyphen split
        if r"(?<=[a-zA-Z])[-\u2013\u2014](?=[a-zA-Z])" in infix:
            infix.remove(r"(?<=[a-zA-Z])[-\u2013\u2014](?=[a-zA-Z])")
        
        infix_re = compile_infix_regex(infix)
        
        # Use correct API for creating tokenizer
        try:
            # For newer spaCy versions
            return Tokenizer(nlp.vocab, prefix_search=prefix_re.search, 
                           suffix_search=suffix_re.search, infix_finditer=infix_re.finditer)
        except TypeError:
            # For older spaCy versions
            return Tokenizer(nlp.vocab, prefix_re=prefix_re, suffix_re=suffix_re, infix_re=infix_re)
    
    _nlp.tokenizer = custom_tokenizer(_nlp)
    
    _nlp.add_pipe("sentencizer")                          # 1️⃣
    _nlp.add_pipe("term_matcher", after="sentencizer")    # 2️⃣
    
    # Create custom termset for GDPR/compliance context
    ts_custom = {
        "pseudo_negations": CUSTOM_PSEUDO_NEGATIONS,
        "preceding_negations": CUSTOM_PRECEDING_NEGATIONS,
        "following_negations": CUSTOM_FOLLOWING_NEGATIONS,
        # Use standard termination terms
        "termination": ["but", "however", "nevertheless", "except"]
    }
    
    # Add negex with custom configuration
    _nlp.add_pipe(
        "negex",
        config={"neg_termset": ts_custom},
        last=True
    )  # 3️⃣
    
    # Save batch_size in nlp object for use in other functions
    _nlp._batch_size = batch_size
    
    return _nlp
# -----------------------------------------------------------


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from PDF file using available libraries."""
    if not PDF2_AVAILABLE and not PDFPLUMBER_AVAILABLE and not PYMUPDF_AVAILABLE:
        raise ImportError("No PDF processing library available. Install PyPDF2, pdfplumber, or PyMuPDF")
    
    text = ""
    
    # Try pdfplumber first (better text extraction)
    if PDFPLUMBER_AVAILABLE:
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text.strip()
        except Exception as e:
            # Continue to next method
            pass
    
    # Fallback to PyPDF2
    if PDF2_AVAILABLE:
        try:
            import PyPDF2
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text.strip()
        except Exception as e:
            # Continue to next method
            pass
    
    # Fallback to fitz (PyMuPDF)
    if PYMUPDF_AVAILABLE:
        try:
            import fitz
            with fitz.open(pdf_path) as pdf:
                for page in pdf:
                    text += page.get_text() + "\n"
            return text.strip()
        except Exception as e:
            # Continue to next method
            pass
    
    raise RuntimeError(f"Could not extract text from {pdf_path}")


def extract_pdf_pages(pdf_path: Path) -> List[str]:
    """
    Extract text from PDF file page by page for batched processing.
    
    Args:
        pdf_path: Path to PDF file
        
    Returns:
        List of page texts (one string per page)
    """
    if not PDF2_AVAILABLE and not PDFPLUMBER_AVAILABLE and not PYMUPDF_AVAILABLE:
        raise ImportError("No PDF processing library available. Install PyPDF2, pdfplumber, or PyMuPDF")
    
    pages = []
    
    # Try pdfplumber first (better text extraction)
    if PDFPLUMBER_AVAILABLE:
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        pages.append(page_text.strip())
            return pages
        except Exception as e:
            # Continue to next method
            pass
    
    # Fallback to PyPDF2
    if PDF2_AVAILABLE:
        try:
            import PyPDF2
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        pages.append(page_text.strip())
            return pages
        except Exception as e:
            # Continue to next method
            pass
    
    # Fallback to fitz (PyMuPDF)
    if PYMUPDF_AVAILABLE:
        try:
            import fitz
            with fitz.open(pdf_path) as pdf:
                for page in pdf:
                    page_text = page.get_text()
                    if page_text and page_text.strip():
                        pages.append(page_text.strip())
            return pages
        except Exception as e:
            # Continue to next method
            pass
    
    raise RuntimeError(f"Could not extract text from {pdf_path}")


def analyze_documents(docs_pages: List[Tuple[str, List[str]]], batch_size: int = 128) -> List[dict]:
    """
    Analyze the given documents (list of tuples (name, pages)) and return a list of structured issue descriptions.
    
    Args:
        docs_pages: [(filename, [page0_text, page1_text, ...]), ...]
        batch_size: Number of pages to process in each batch (default: 128)
        
    Returns:
        List of dictionaries with keys: type, section, file, message
        - type: "error" or "warning"
        - section: "1"-"9" for Annex IV sections, None for general issues
        - file: filename or "" for cross-document issues
        - message: description of the issue
    """
    issues = []
    
    # 1. Check Annex IV compliance: all required sections present?
    required_sections = {
        1: ["system overview", "system overview:", "overview"],       # General system overview
        2: ["development process", "development process:", "development"],   # Development process
        3: ["system monitoring", "system monitoring:", "monitoring"],     # System monitoring
        4: ["performance metrics", "performance metrics:", "performance"],   # Performance metrics
        5: ["risk management", "risk management:", "risk"],       # Risk management
        6: ["changes and versions", "changes and versions:", "changes", "versions"],  # Changes and versions
        7: ["standards applied", "standards applied:", "standards"],     # Applied standards
        8: ["compliance declaration", "compliance declaration:", "compliance"],# Compliance declaration
        9: ["post-market", "post-market plan", "post market"],           # Post-market monitoring
    }
    
    for doc_name, pages in docs_pages:
        # ❶ fast global checks still need "whole doc" text
        whole_text = " ".join(pages)
        whole_text_lower = whole_text.lower()
        
        # Check for missing required sections with flexible matching
        for section_num, keywords in required_sections.items():
            section_found = any(keyword in whole_text_lower for keyword in keywords)
            if not section_found:
                # Missing entire Annex IV section - this is an ERROR
                primary_keyword = keywords[0]  # Use the first keyword for the error message
                issues.append({
                    "type": "error",
                    "section": str(section_num),
                    "file": doc_name,
                    "message": f"Missing content for Annex IV section {section_num} ({primary_keyword})."
                })
        
        # If document is declared as high-risk, check special requirements:
        if "high-risk" in whole_text_lower or "high risk" in whole_text_lower:
            # For example, section 9 (post-market plan) is mandatory for high-risk systems
            if "post-market" not in whole_text_lower:
                issues.append({
                    "type": "error",
                    "section": "9",
                    "file": doc_name,
                    "message": "High-risk system declared, but no post-market monitoring plan (Annex IV §9)."
                })
        
        # Look for high-risk use case mentions (Annex III) without high-risk declaration:
        high_risk_keywords = ["biometric", "law enforcement", "AI system for law enforcement", "biometric identification"]
        if any(kw in whole_text_lower for kw in high_risk_keywords) and "high-risk" not in whole_text_lower:
            issues.append({
                "type": "error",
                "section": None,
                "file": doc_name,
                "message": "Potential high-risk use (e.g. biometric or law enforcement) mentioned, but system not labeled high-risk."
            })
        
        # GDPR checks: simple cases of principle violations
        if "personal data" in whole_text_lower:
            # If it says data is stored indefinitely or without limitation
            if re.search(r"indefinite|forever|no retention limit", whole_text_lower):
                issues.append({
                    "type": "error",
                    "section": None,
                    "file": doc_name,
                    "message": "Personal data retention is indefinite (violates GDPR storage limitation)."
                })
            
            # If no mention of legal basis or consent when collecting personal data
            lawful_basis_keywords = ["lawful basis", "legal basis", "legitimate interest", "legitimate basis"]
            if "consent" not in whole_text_lower and not any(kw in whole_text_lower for kw in lawful_basis_keywords):
                issues.append({
                    "type": "warning",
                    "section": None,
                    "file": doc_name,
                    "message": "Personal data use without mention of consent or lawful basis (possible GDPR issue)."
                })
            
            # If no mention of data subject rights (deletion, correction, etc.)
            data_rights_keywords = ["delete", "erasure", "deletion", "right to erasure", "data subject rights", "access rights", "rectification"]
            if not any(kw in whole_text_lower for kw in data_rights_keywords):
                issues.append({
                    "type": "warning",
                    "section": None,
                    "file": doc_name,
                    "message": "No mention of data deletion or subject access rights (check GDPR compliance)."
                })
    
    # --------------- Document analysis with negspaCy ----------------
    # OPTIMIZED: batch processing for memory efficiency
    nlp = _get_nlp(batch_size)

    # Collect data and documents in single pass
    presence_all = {name: {"pos": set(), "neg": set()} for name, _ in docs_pages}

    # SINGLE PASS: collect data and analyze contradictions
    for file_name, pages in docs_pages:
        if nlp is not None:
            # ❷ batched NLP – keeps page info
            for page_no, doc in enumerate(nlp.pipe(pages, batch_size=batch_size)):
                for ent in [e for e in doc.ents if e.label_ == "TERM"]:
                    bucket = "neg" if ent._.negex else "pos"
                    presence_all[file_name][bucket].add((ent.text.lower(), page_no))
            
            # Analysis of internal contradictions in document
            pos_terms = {term for term, _ in presence_all[file_name]["pos"]}
            neg_terms = {term for term, _ in presence_all[file_name]["neg"]}
            internal_contradictions = pos_terms & neg_terms
            
            # Also check cases of only negative mentions
            only_neg_terms = neg_terms - pos_terms
            
            # Process internal contradictions (pos ∩ neg)
            for term in internal_contradictions:
                # Determine severity: lower level only if all mentions are negative
                pos_mentions = [page_no for t, page_no in presence_all[file_name]["pos"] if t == term]
                neg_mentions = [page_no for t, page_no in presence_all[file_name]["neg"] if t == term]
                
                # Check if there are only negative mentions (no positive ones)
                if not pos_mentions:  # every hit is negated
                    severity = "warning"
                else:
                    severity = "error"
                message = _create_page_aware_message(term, presence_all, file_name)
                issues.append({
                    "type": severity,
                    "file": file_name,
                    "section": None,
                    "message": message
                })
            
            # Process cases of only negative mentions
            for term in only_neg_terms:
                message = _create_page_aware_message(term, presence_all, file_name)
                issues.append({
                    "type": "warning",
                    "file": file_name,
                    "section": None,
                    "message": message
                })
                
        else:
            # fallback-regex block, now page aware
            for page_no, page_text in enumerate(pages):
                txt = page_text.lower().replace('-', ' ')
                for term in KEY_TERMS:
                    if term in txt:
                        negated = _check_negation_regex(txt, term)
                        bucket = "neg" if negated else "pos"
                        presence_all[file_name][bucket].add((term, page_no))

    # Cross-document contradictions
    term_global = {t: {"pos": set(), "neg": set()} for t in KEY_TERMS}
    for doc_name, stats in presence_all.items():
        for bucket in ("pos", "neg"):
            for term_tuple in stats[bucket]:
                # Extract just the term name (without page number) for global analysis
                term = term_tuple[0] if isinstance(term_tuple, tuple) else term_tuple
                term_global[term][bucket].add(doc_name)
    
    for term, glob in term_global.items():
        if glob["pos"] and glob["neg"]:
            issues.append({
                "type": "error",
                "section": None,
                "file": "",
                "message": f"Inconsistent stance on '{term}' across documents: {', '.join(glob['pos'])} vs negated in {', '.join(glob['neg'])}."
            })
    
    # Cross-document contradictions: compare main statements between documents.
    if len(docs_pages) > 1:
        # Example: if documents call the system different names, or different risk levels.
        names = [doc_name for doc_name, _ in docs_pages]
        
        # Check: if one document calls the risk high, and another doesn't.
        risk_flags = []
        for _, pages in docs_pages:
            whole_text = " ".join(pages)
            risk_flags.append("high-risk" in whole_text.lower() or "high risk" in whole_text.lower())
        
        if any(risk_flags) and not all(risk_flags):
            issues.append({
                "type": "error",
                "section": None,
                "file": "",
                "message": "Contradiction: Not all documents agree on whether the system is high-risk or not."
            })
        
        # Check for system name consistency
        system_names = []
        for _, pages in docs_pages:
            whole_text = " ".join(pages)
            # Simple heuristic: look for "AI system" or "system" followed by a name
            system_matches = re.findall(r"AI system[:\s]+([A-Za-z0-9\s\-]+)", whole_text, re.IGNORECASE)
            if system_matches:
                system_names.extend(system_matches)
        
        if len(set(system_names)) > 1:
            issues.append({
                "type": "error",
                "section": None,
                "file": "",
                "message": "Contradiction: Different system names found across documents."
            })
        
        # Check for version consistency
        versions = []
        for _, pages in docs_pages:
            whole_text = " ".join(pages)
            version_matches = re.findall(r"version[:\s]+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", whole_text, re.IGNORECASE)
            if version_matches:
                versions.extend(version_matches)
        
        if len(set(versions)) > 1:
            issues.append({
                "type": "error",
                "section": None,
                "file": "",
                "message": "Contradiction: Different version numbers found across documents."
            })
    
    # 3. Additional compliance checks (warnings)
    for doc_name, pages in docs_pages:
        whole_text = " ".join(pages)
        whole_text_lower = whole_text.lower()
        
        # Check for transparency requirements
        transparency_keywords = ["transparency", "explainability", "interpretability", "black box"]
        if "AI system" in whole_text_lower and not any(kw in whole_text_lower for kw in transparency_keywords):
            issues.append({
                "type": "warning",
                "section": None,
                "file": doc_name,
                "message": "No mention of transparency or explainability (important for AI Act compliance)."
            })
        
        # Check for bias and fairness
        bias_keywords = ["bias", "discrimination", "fairness", "equity", "discriminatory"]
        if "AI system" in whole_text_lower and not any(kw in whole_text_lower for kw in bias_keywords):
            issues.append({
                "type": "warning",
                "section": None,
                "file": doc_name,
                "message": "No mention of bias detection or fairness measures."
            })
        
        # Check for security measures
        security_keywords = ["security", "robustness", "reliability", "safety measures"]
        if "AI system" in whole_text_lower and not any(kw in whole_text_lower for kw in security_keywords):
            issues.append({
                "type": "warning",
                "section": None,
                "file": doc_name,
                "message": "No mention of security or robustness measures."
            })
    
    return issues


def review_documents(pdf_files: List[Path], batch_size: int = 128) -> List[dict]:
    """
    Review PDF documents for compliance issues.
    Extract pages up-front so downstream code can work page-wise.
    
    Args:
        pdf_files: List of Path objects pointing to PDF files
        batch_size: Number of pages to process in each batch (default: 128)
        
    Returns:
        List of structured issue dictionaries with keys: type, section, file, message
    """
    # Check if PDF processing libraries are available
    if not PDF2_AVAILABLE and not PDFPLUMBER_AVAILABLE and not PYMUPDF_AVAILABLE:
        raise ImportError("No PDF processing library available. Install PyPDF2, pdfplumber, or PyMuPDF")
    
    # ⬇️  NEW: list of tuples (name, pages)
    docs_pages: List[Tuple[str, List[str]]] = []
    for f in pdf_files:
        try:
            pages = extract_pdf_pages(f)   # <— already in the module
            docs_pages.append((f.name, pages))
        except Exception as e:
            raise RuntimeError(f"Failed to process {f}: {e}")
    
    return analyze_documents(docs_pages, batch_size)   # <-- pass batch_size


def review_single_document(pdf_file: Path) -> List[dict]:
    """
    Review a single PDF document for compliance issues.
    
    Args:
        pdf_file: Path object pointing to a PDF file
        
    Returns:
        List of structured issue dictionaries with keys: type, section, file, message
    """
    return review_documents([pdf_file])


def analyze_text(text: str, filename: str = "document") -> List[dict]:
    """
    Analyze text content for compliance issues.
    
    Args:
        text: Text content to analyze
        filename: Name of the document (for issue reporting)
        
    Returns:
        List of structured issue dictionaries with keys: type, section, file, message
    """
    # Convert single text to pages format for consistency
    docs_pages = [(filename, [text])]
    return analyze_documents(docs_pages)


# Convenience function for backward compatibility
def extract_and_analyze_text(text: str, filename: str = "document") -> List[str]:
    """Alias for analyze_text for backward compatibility."""
    return analyze_text(text, filename)


def analyze_annex_payload(payload: dict) -> List[dict]:
    """
    Analyze Annex IV payload for compliance issues.
    
    Args:
        payload: Dictionary containing Annex IV sections
        
    Returns:
        List of structured issue dictionaries
    """
    # Convert payload to text format for analysis
    docs_texts = []
    
    # Extract text from payload sections
    text_parts = []
    for key, value in payload.items():
        if isinstance(value, str) and value.strip():
            text_parts.append(f"{key}: {value}")
    
    # Combine all text
    combined_text = "\n".join(text_parts)
    
    # Analyze the combined text
    issues = analyze_text(combined_text, "annex_payload")
    
    return issues


def handle_multipart_review_request(headers: dict, body: bytes) -> dict:
    """
    Handle multipart/form-data request for document review.
    
    Args:
        headers: HTTP headers dictionary
        body: Raw request body bytes
        
    Returns:
        Structured response dictionary with issues and metadata
    """
    import tempfile
    import cgi
    from io import BytesIO
    
    # Parse content type
    content_type = headers.get('Content-Type', '')
    if 'multipart/form-data' not in content_type:
        raise ValueError("Content-Type must be multipart/form-data")
    
    # Create environment for cgi.FieldStorage
    environ = {
        'REQUEST_METHOD': 'POST',
        'CONTENT_TYPE': content_type,
        'CONTENT_LENGTH': str(len(body))
    }
    
    # Parse multipart data
    fp = BytesIO(body)
    fs = cgi.FieldStorage(fp=fp, headers=headers, environ=environ)
    
    # Extract files
    file_fields = fs.getlist("files") if hasattr(fs, 'getlist') else [fs["files"]]
    if not isinstance(file_fields, list):
        file_fields = [file_fields]
    
    # Process each file
    docs_pages = []
    processed_files = []
    
    for field in file_fields:
        if hasattr(field, 'filename') and field.filename:
            file_name = field.filename
        else:
            file_name = "document.pdf"
        
        file_bytes = field.file.read()
        processed_files.append(file_name)
        
        # Save to temporary file for text extraction
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            
            try:
                pages = extract_pdf_pages(Path(tmp.name))
                docs_pages.append((file_name, pages))
            except Exception as e:
                # Add error for this file
                docs_pages.append((file_name, [f"Error extracting text: {str(e)}"]))
            finally:
                # Clean up temp file
                import os
                try:
                    os.unlink(tmp.name)
                except:
                    pass
    
    # Analyze documents
    issues = analyze_documents(docs_pages)
    
    # Create structured response
    return create_review_response(issues, processed_files)


def handle_text_review_request(text_content: str, filename: str = "document.txt") -> dict:
    """
    Handle text review request.
    
    Args:
        text_content: Text content to analyze
        filename: Name of the document
        
    Returns:
        Structured response dictionary with issues and metadata
    """
    issues = analyze_text(text_content, filename)
    return create_review_response(issues, [filename])


def create_review_response(issues: List[dict], processed_files: List[str]) -> dict:
    """
    Create structured response for review results.
    
    Args:
        issues: List of issue dictionaries
        processed_files: List of processed file names
        
    Returns:
        Structured response dictionary
    """
    # Count issues by type
    errors = [issue for issue in issues if issue["type"] == "error"]
    warnings = [issue for issue in issues if issue["type"] == "warning"]
    
    return {
        "success": True,
        "processed_files": processed_files,
        "total_files": len(processed_files),
        "issues": issues,
        "summary": {
            "total_issues": len(issues),
            "errors": len(errors),
            "warnings": len(warnings)
        }
    } 

# Function for creating messages with page information
def _create_page_aware_message(term: str, presence_all: dict, file_name: str) -> str:
    """
    Creates a message with page information for contradictions.
    
    Args:
        term: Term for which contradiction was found
        presence_all: Dictionary with information about term presence
        file_name: File name
        
    Returns:
        Message with page information
    """
    pos_pages = [page_no for t, page_no in presence_all[file_name]["pos"] if t == term]
    neg_pages = [page_no for t, page_no in presence_all[file_name]["neg"] if t == term]
    
    # ↓ pos_page_str/neg_page_str declare before returns
    if pos_pages and neg_pages:
        # There are both positive and negative mentions
        pos_page_str = f"page {min(pos_pages) + 1}" if len(pos_pages) == 1 else f"pages {min(pos_pages) + 1}-{max(pos_pages) + 1}"
        neg_page_str = f"page {min(neg_pages) + 1}" if len(neg_pages) == 1 else f"pages {min(neg_pages) + 1}-{max(neg_pages) + 1}"
        return f"Contradictory statements about '{term}' (affirmed on {pos_page_str}, negated on {neg_page_str})."
    elif pos_pages:
        pos_page_str = f"page {pos_pages[0] + 1}"
        return f"Term '{term}' found on {pos_page_str}."
    elif neg_pages:
        neg_page_str = f"page {neg_pages[0] + 1}"
        return f"Term '{term}' negated on {neg_page_str}."
    else:
        return f"Term '{term}' mentioned in document." 