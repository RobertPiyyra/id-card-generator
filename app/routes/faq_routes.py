"""
FAQ Chatbot Routes
===================
Floating widget on all pages + dedicated FAQ page + chat API.
"""
import logging
from flask import Blueprint, render_template, request, jsonify, current_app

logger = logging.getLogger(__name__)

faq_bp = Blueprint('faq', __name__)


@faq_bp.route('/faq')
def faq_page():
    """Dedicated FAQ page with all categories."""
    from app.services.faq_service import get_all_categories, get_all_faqs
    categories = get_all_categories()
    faqs = get_all_faqs()
    return render_template('faq/faq_page.html', categories=categories, faqs=faqs)


@faq_bp.route('/api/faq', methods=['POST'])
def api_faq():
    """FAQ chat endpoint — accepts a question, returns a match."""
    data = request.get_json(silent=True) or {}
    question = (data.get('question') or '').strip()

    if not question:
        return jsonify({
            "success": False,
            "error": "No question provided",
            "answer": None,
        }), 400

    from app.services.faq_service import find_faq_answer
    result = find_faq_answer(question)

    if result:
        return jsonify({
            "success": True,
            "matched": True,
            "confidence": result["confidence"],
            "category": result["category"],
            "question": result["question"],
            "answer": result["answer"],
        })

    # No match — return helpful fallback
    return jsonify({
        "success": True,
        "matched": False,
        "confidence": 0,
        "category": None,
        "question": None,
        "answer": (
            "I couldn't find a direct answer to your question. "
            "Try rephrasing with keywords like 'upload', 'template', 'QR code', "
            "'generate', 'print', or 'bulk'. You can also browse the full FAQ page "
            "for a list of common questions."
        ),
    })


@faq_bp.route('/api/faq/search')
def api_faq_search():
    """Search FAQ entries by keyword (for autocomplete / search)."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])

    from app.services.faq_service import find_faq_answer
    result = find_faq_answer(q)
    if result:
        return jsonify([{
            "category": result["category"],
            "question": result["question"],
            "answer": result["answer"],
        }])
    return jsonify([])
