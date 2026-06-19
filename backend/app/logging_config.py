"""Logging configuration with PII masking."""

import logging
import re

# Sensitive keys that should be redacted from log strings/payloads
SENSITIVE_KEYS = [
    "reasoning",
    "spoken_summary",
    "card_summary",
    "extracted_fields",
    "proposed_record_data",
    "value",
    "content",
    "text",
    "ocr_text",
    "shadow_text",
    "document_text",
]

class PIIMaskingFilter(logging.Filter):
    """Filter that redacts potential PII from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        
        # Redact patterns like "reasoning='...'" or "content: ..."
        for key in SENSITIVE_KEYS:
            # Match both 'key=' and 'key:' with various quoting styles
            pattern = rf"({key}\s*[=:]\s*)(['\"]?)(.*?)(['\"]?)(?=\s|,|\)|$)"
            message = re.sub(pattern, r"\1\2[REDACTED]\4", message, flags=re.IGNORECASE)
        
        record.msg = message
        # Clear args since we've already formatted them into record.msg
        record.args = () 
        return True

def setup_logging():
    """Configure root logger with PII masking."""
    handler = logging.StreamHandler()
    handler.addFilter(PIIMaskingFilter())
    
    # Use standard format for GCP Cloud Logging
    formatter = logging.Formatter(
        "[%(levelname)s] %(asctime)s - %(name)s - %(message)s"
    )
    handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Clear existing handlers to avoid duplicates
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
        
    root_logger.addHandler(handler)
    
    # Silence chatty third-party loggers
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
