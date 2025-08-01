"""
Review module for annex4ac - analyze PDF documents for compliance issues.

This module provides library functions for analyzing technical documentation
for EU AI Act Annex IV and GDPR compliance issues.
"""

import re
from pathlib import Path
from typing import List, Tuple

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


def analyze_documents(docs_texts: List[Tuple[str, str]]) -> List[dict]:
    """
    Analyze the given documents (list of tuples (name, text)) and return a list of structured issue descriptions.
    
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
        1: "system overview",       # General system overview
        2: "development process",   # Development process
        3: "system monitoring",     # System monitoring
        4: "performance metrics",   # Performance metrics
        5: "risk management",       # Risk management
        6: "changes and versions",  # Changes and versions
        7: "standards applied",     # Applied standards
        8: "compliance declaration",# Compliance declaration
        9: "post-market",           # Post-market monitoring (partial keyword match)
    }
    
    for doc_name, text in docs_texts:
        text_lower = text.lower()
        
        # Check for missing required sections
        for section_num, keyword in required_sections.items():
            if keyword not in text_lower:
                # Missing entire Annex IV section - this is an ERROR
                issues.append({
                    "type": "error",
                    "section": str(section_num),
                    "file": doc_name,
                    "message": f"Missing content for Annex IV section {section_num} ({keyword})."
                })
        
        # If document is declared as high-risk, check special requirements:
        if "high-risk" in text_lower or "high risk" in text_lower:
            # For example, section 9 (post-market plan) is mandatory for high-risk systems
            if "post-market" not in text_lower:
                issues.append({
                    "type": "error",
                    "section": "9",
                    "file": doc_name,
                    "message": "High-risk system declared, but no post-market monitoring plan (Annex IV §9)."
                })
        
        # Look for high-risk use case mentions (Annex III) without high-risk declaration:
        high_risk_keywords = ["biometric", "law enforcement", "AI system for law enforcement", "biometric identification"]
        if any(kw in text_lower for kw in high_risk_keywords) and "high-risk" not in text_lower:
            issues.append({
                "type": "error",
                "section": None,
                "file": doc_name,
                "message": "Potential high-risk use (e.g. biometric or law enforcement) mentioned, but system not labeled high-risk."
            })
        
        # GDPR checks: simple cases of principle violations
        if "personal data" in text_lower:
            # If it says data is stored indefinitely or without limitation
            if re.search(r"indefinite|forever|no retention limit", text_lower):
                issues.append({
                    "type": "error",
                    "section": None,
                    "file": doc_name,
                    "message": "Personal data retention is indefinite (violates GDPR storage limitation)."
                })
            
            # If no mention of legal basis or consent when collecting personal data
            if "consent" not in text_lower and "lawful basis" not in text_lower:
                issues.append({
                    "type": "warning",
                    "section": None,
                    "file": doc_name,
                    "message": "Personal data use without mention of consent or lawful basis (possible GDPR issue)."
                })
            
            # If no mention of data subject rights (deletion, correction, etc.)
            if "delete" not in text_lower and "erasure" not in text_lower:
                issues.append({
                    "type": "warning",
                    "section": None,
                    "file": doc_name,
                    "message": "No mention of data deletion or subject access rights (check GDPR compliance)."
                })
    
    # 2. Search for contradictions within and between documents.
    # Simplified: look for phrase pairs like "no X" vs "X" in the same document.
    contradiction_patterns = [
        (r"\bno personal data\b", r"\bpersonal data\b"),
        (r"\bnot stored\b", r"\bstored\b"),
        (r"\bno longer\b", r"\bstill\b"),
        (r"\bnot high risk\b", r"\bhigh risk\b"),
        (r"\bnot monitored\b", r"\bmonitored\b"),
        (r"\bnot compliant\b", r"\bcompliant\b")
    ]
    
    for doc_name, text in docs_texts:
        text_lower = text.lower()
        for pattern_no, pattern_yes in contradiction_patterns:
            if re.search(pattern_no, text_lower) and re.search(pattern_yes, text_lower):
                issues.append({
                    "type": "error",
                    "section": None,
                    "file": doc_name,
                    "message": f"Contradictory statements around '{pattern_no}' vs '{pattern_yes}'."
                })
    
    # Cross-document contradictions: compare main statements between documents.
    if len(docs_texts) > 1:
        # Example: if documents call the system different names, or different risk levels.
        names = [doc_name for doc_name, _ in docs_texts]
        
        # Check: if one document calls the risk high, and another doesn't.
        risk_flags = [("high-risk" in text.lower() or "high risk" in text.lower()) for _, text in docs_texts]
        if any(risk_flags) and not all(risk_flags):
            issues.append({
                "type": "error",
                "section": None,
                "file": "",
                "message": "Contradiction: Not all documents agree on whether the system is high-risk or not."
            })
        
        # Check for system name consistency
        system_names = []
        for _, text in docs_texts:
            # Simple heuristic: look for "AI system" or "system" followed by a name
            system_matches = re.findall(r"AI system[:\s]+([A-Za-z0-9\s\-]+)", text, re.IGNORECASE)
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
        for _, text in docs_texts:
            version_matches = re.findall(r"version[:\s]+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
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
    for doc_name, text in docs_texts:
        text_lower = text.lower()
        
        # Check for transparency requirements
        transparency_keywords = ["transparency", "explainability", "interpretability", "black box"]
        if "AI system" in text_lower and not any(kw in text_lower for kw in transparency_keywords):
            issues.append({
                "type": "warning",
                "section": None,
                "file": doc_name,
                "message": "No mention of transparency or explainability (important for AI Act compliance)."
            })
        
        # Check for bias and fairness
        bias_keywords = ["bias", "discrimination", "fairness", "equity", "discriminatory"]
        if "AI system" in text_lower and not any(kw in text_lower for kw in bias_keywords):
            issues.append({
                "type": "warning",
                "section": None,
                "file": doc_name,
                "message": "No mention of bias detection or fairness measures."
            })
        
        # Check for security measures
        security_keywords = ["security", "robustness", "reliability", "safety measures"]
        if "AI system" in text_lower and not any(kw in text_lower for kw in security_keywords):
            issues.append({
                "type": "warning",
                "section": None,
                "file": doc_name,
                "message": "No mention of security or robustness measures."
            })
    
    return issues


def review_documents(pdf_files: List[Path]) -> List[dict]:
    """
    Review PDF documents for compliance issues.
    
    Args:
        pdf_files: List of Path objects pointing to PDF files
        
    Returns:
        List of structured issue dictionaries with keys: type, section, file, message
    """
    # Check if PDF processing libraries are available
    if not PDF2_AVAILABLE and not PDFPLUMBER_AVAILABLE and not PYMUPDF_AVAILABLE:
        raise ImportError("No PDF processing library available. Install PyPDF2, pdfplumber, or PyMuPDF")
    
    # Extract text from PDF files
    docs_texts = []
    for file in pdf_files:
        try:
            text = extract_text_from_pdf(file)
            docs_texts.append((file.name, text))
        except Exception as e:
            raise RuntimeError(f"Failed to process {file}: {e}")
    
    # Analyze documents for issues
    return analyze_documents(docs_texts)


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
    docs_texts = [(filename, text)]
    return analyze_documents(docs_texts)


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
    docs_texts = []
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
                text = extract_text_from_pdf(Path(tmp.name))
                docs_texts.append((file_name, text))
            except Exception as e:
                # Add error for this file
                docs_texts.append((file_name, f"Error extracting text: {str(e)}"))
            finally:
                # Clean up temp file
                import os
                try:
                    os.unlink(tmp.name)
                except:
                    pass
    
    # Analyze documents
    issues = analyze_documents(docs_texts)
    
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