"""Report EC AI/ML and multilingual chatbot component (Member 6).

Main entry points:
    ReportEcAIService          - everything in one object (integration/service.py)
    ConversationEngine         - the chatbot (chatbot/conversation.py)
    analyze_complaint          - category + priority suggestions
    find_potential_duplicates  - possible duplicate reports
    analyze_verification       - advisory triage of confirm/dispute responses
    summarize_trends           - privacy-conscious trend summaries
"""
from report_ec_ai.config import MODEL_VERSION

__version__ = "0.1.0"

__all__ = ["MODEL_VERSION", "__version__"]
