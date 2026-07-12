"""
FAQ Chatbot Service
===================
Keyword-matching FAQ engine with optional AI fallback.
"""
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ─── FAQ Database ────────────────────────────────────────────────────────────
# Each entry: {category, question, answer, keywords (list of matchable terms)}
FAQ_ENTRIES = [
    {
        "category": "Photos & Uploads",
        "question": "How do I upload student photos?",
        "answer": (
            "Go to Admin → Batch Upload. Select a school, choose a template, "
            "then drag-and-drop or select student photos. Photos are matched "
            "to students by serial number or filename. Supported formats: JPG, PNG, WebP."
        ),
        "keywords": ["upload", "photo", "photos", "picture", "pictures", "image", "images", "batch", "import"],
    },
    {
        "category": "Photos & Uploads",
        "question": "What photo dimensions should I use?",
        "answer": (
            "Recommended: 300×400 pixels (3:4 ratio) at 300 DPI. "
            "The system auto-crops and resizes if needed, but correct dimensions "
            "give the best print quality on ID cards."
        ),
        "keywords": ["dimension", "size", "resolution", "dpi", "pixels", "crop", "resize", "300", "400"],
    },
    {
        "category": "ID Card Generation",
        "question": "How do I generate ID cards?",
        "answer": (
            "After uploading photos and assigning them to students, go to "
            "Dashboard → Generate Cards. Select the template, choose students "
            "(or select all), and click 'Generate'. Cards are generated in bulk "
            "and available for download as PDF or CorelDRAW (CDR) export."
        ),
        "keywords": ["generate", "id card", "id cards", "card", "cards", "create", "make", "produce", "pdf", "download"],
    },
    {
        "category": "ID Card Generation",
        "question": "Can I generate cards for one student only?",
        "answer": (
            "Yes! Go to the Students tab, find the student, and click "
            "'Generate Card' on their row. You can also generate from the "
            "student detail page."
        ),
        "keywords": ["single", "one", "individual", "specific", "just one"],
    },
    {
        "category": "Templates",
        "question": "How do I create a new ID card template?",
        "answer": (
            "Go to Admin → Templates → New Template. Design your card using "
            "the visual editor. You can set background images, text fields "
            "(name, class, roll number, etc.), photo placement, and QR code position. "
            "Save and assign to a school."
        ),
        "keywords": ["template", "templates", "design", "editor", "new template", "create template", "background"],
    },
    {
        "category": "Templates",
        "question": "How do I edit an existing template?",
        "answer": (
            "Go to Admin → Templates, click 'Edit' on any template. "
            "You can modify text, reposition elements, change fonts/colors, "
            "and update the background. Changes apply to future card generations "
            "(already-generated cards are not affected)."
        ),
        "keywords": ["edit", "modify", "change", "update", "template", "templates"],
    },
    {
        "category": "QR Codes",
        "question": "What is the QR code on the ID card?",
        "answer": (
            "Each ID card has a unique QR code containing a verification URL. "
            "When scanned, it shows the student's details and confirms the card "
            "is authentic. This prevents forgery and allows instant verification "
            "by school staff or security."
        ),
        "keywords": ["qr", "qr code", "qrcode", "scan", "verification", "verify", "barcode"],
    },
    {
        "category": "QR Codes",
        "question": "How do I verify an ID card using the QR code?",
        "answer": (
            "Open your phone's camera app and point it at the QR code. "
            "It will open a verification page showing the student's name, "
            "school, class, and photo. Alternatively, go to "
            "yoursite.com/verify and enter the serial number."
        ),
        "keywords": ["verify", "scan", "qr", "check", "authentic", "forgery", "fake"],
    },
    {
        "category": "Bulk Operations",
        "question": "How do I process multiple students at once?",
        "answer": (
            "Use the Batch Operations feature: Upload photos in bulk, "
            "assign them by serial number or filename pattern, then generate "
            "cards for all assigned students in one click. "
            "You can process hundreds of students in a single batch."
        ),
        "keywords": ["bulk", "multiple", "many", "batch", "mass", "hundreds", "all students"],
    },
    {
        "category": "Bulk Operations",
        "question": "How do I fetch a student by serial number?",
        "answer": (
            "Go to Admin → Students → Fetch by Serial. Enter the serial "
            "number and the student's details, photo, and card status will "
            "appear. You can also search by name or class."
        ),
        "keywords": ["serial", "fetch", "search", "find", "lookup", "roll number", "student id"],
    },
    {
        "category": "Export & Download",
        "question": "How do I export ID cards to CorelDRAW?",
        "answer": (
            "After generating cards, click 'Export' → 'CorelDRAW (CDR)'. "
            "This produces a .cdr file with all card elements as editable objects "
            "that you can open in CorelDRAW for further customization."
        ),
        "keywords": ["corel", "coreldraw", "cdr", "export", "download", "editable"],
    },
    {
        "category": "Export & Download",
        "question": "Can I download all cards as PDF?",
        "answer": (
            "Yes! After generating, click 'Download PDF'. You get a single "
            "printable PDF with all selected cards, ready for printing or "
            "sharing digitally."
        ),
        "keywords": ["pdf", "download", "print", "printable", "export all"],
    },
    {
        "category": "Account & Login",
        "question": "How do I create an admin account?",
        "answer": (
            "The first admin is created during setup. Additional admins can be "
            "created by going to Admin → Settings → Users → Add User. "
            "Set role to 'Admin' and assign to a specific school."
        ),
        "keywords": ["admin", "account", "create", "register", "signup", "sign up", "new user", "login"],
    },
    {
        "category": "Account & Login",
        "question": "I forgot my password. What should I do?",
        "answer": (
            "Click 'Forgot Password' on the login page. Enter your email "
            "and you'll receive a reset link. If that doesn't work, contact "
            "the super admin to reset your password from Admin → Settings → Users."
        ),
        "keywords": ["password", "forgot", "reset", "login", "locked", "access", "cannot login", "sign in"],
    },
    {
        "category": "Schools",
        "question": "How do I add a new school?",
        "answer": (
            "Go to Admin → Schools → Add School. Enter the school name, "
            "logo, address, and other details. Each school gets its own "
            "templates, students, and admin assignments."
        ),
        "keywords": ["school", "add school", "new school", "create school", "institution"],
    },
    {
        "category": "Schools",
        "question": "Can one school have multiple templates?",
        "answer": (
            "Yes! A school can have multiple templates (e.g., different cards "
            "for teachers vs. students, or for different grades). Create each "
            "template and assign it to the appropriate students."
        ),
        "keywords": ["multiple templates", "templates", "school", "different", "grade", "teacher"],
    },
    {
        "category": "RTL & Languages",
        "question": "Does this support Urdu/Arabic/Hindi on ID cards?",
        "answer": (
            "Yes! The system supports RTL (right-to-left) text rendering for "
            "Urdu, Arabic, Hindi, and other RTL languages. You can set the "
            "template language and use the built-in BiDi text support. "
            "Both the visual editor and PDF export handle RTL correctly."
        ),
        "keywords": ["urdu", "arabic", "hindi", "rtl", "right to left", "language", "bidi", "arabic script"],
    },
    {
        "category": "RTL & Languages",
        "question": "How do I set the template language to Urdu?",
        "answer": (
            "In the template editor, select 'Language' → 'Urdu' and choose "
            "the text direction as RTL. Enter student names in Urdu and they "
            "will render correctly on the ID card."
        ),
        "keywords": ["urdu", "language", "template", "rtl", "direction", "set language"],
    },
    {
        "category": "Printing",
        "question": "What paper size should I use for printing?",
        "answer": (
            "Standard CR80 cards are 85.6mm × 53.98mm (landscape A4 fits ~10 cards). "
            "For direct card printing, use CR80 blank cards. For paper printing, "
            "use A4 and cut after printing. The PDF export is pre-formatted for A4."
        ),
        "keywords": ["print", "paper", "a4", "cr80", "size", "card size", "cutting"],
    },
    {
        "category": "Printing",
        "question": "The printed colors look different from the screen. Why?",
        "answer": (
            "This is normal — screens use RGB (additive) and printers use CMYK (subtractive). "
            "For best results: 1) Use a calibrated monitor, 2) Export as PDF/X-1a for "
            "print, 3) Use a professional printer with color profile support."
        ),
        "keywords": ["color", "print", "different", "rgb", "cmyk", "faded", "vibrant"],
    },
    {
        "category": "Troubleshooting",
        "question": "The page is slow when loading many students. How can I fix this?",
        "answer": (
            "For large datasets (1000+ students), use pagination and filters. "
            "Also ensure your server has enough RAM and the database is indexed. "
            "Contact support if slowness persists with fewer than 500 students."
        ),
        "keywords": ["slow", "loading", "performance", "lag", "freeze", "hang", "speed"],
    },
    {
        "category": "Troubleshooting",
        "question": "I'm getting an error when uploading photos. What should I check?",
        "answer": (
            "Common causes: 1) File too large (max 10MB per photo), "
            "2) Unsupported format (use JPG/PNG/WebP), "
            "3) Filename doesn't match serial number pattern. "
            "Check Admin → Bulk Jobs for detailed error logs."
        ),
        "keywords": ["error", "upload", "fail", "broken", "issue", "problem", "bug", "crash"],
    },
    {
        "category": "Pricing & Plans",
        "question": "Is there a limit on the number of students?",
        "answer": (
            "Limits depend on your plan. Free tier: up to 500 students. "
            "Pro: unlimited students. Contact sales for enterprise pricing "
            "with multi-school support and priority features."
        ),
        "keywords": ["limit", "students", "how many", "maximum", "pricing", "plan", "cost", "free", "paid"],
    },
]


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase alphanumeric tokens."""
    return re.findall(r'[a-z0-9]+', text.lower())


def find_faq_answer(query: str, threshold: float = 0.25) -> dict | None:
    """
    Find the best matching FAQ entry for a user query.

    Scoring: fraction of FAQ keywords found in the query tokens.
    Returns the best match if score >= threshold, else None.
    """
    if not query or not query.strip():
        return None

    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return None

    best_entry = None
    best_score = 0.0

    for entry in FAQ_ENTRIES:
        keywords = entry["keywords"]
        if not keywords:
            continue

        matched = sum(1 for kw in keywords if kw in query_tokens)
        score = matched / len(keywords)

        # Bonus for matching the question itself
        question_tokens = set(_tokenize(entry["question"]))
        if question_tokens:
            question_match = len(query_tokens & question_tokens) / len(question_tokens)
            score = max(score, question_match * 0.8)

        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score >= threshold:
        return {
            "matched": True,
            "confidence": round(best_score, 2),
            "category": best_entry["category"],
            "question": best_entry["question"],
            "answer": best_entry["answer"],
        }

    return None


def get_all_categories() -> list[str]:
    """Return all unique FAQ categories."""
    return sorted(set(e["category"] for e in FAQ_ENTRIES))


def get_faqs_by_category(category: str) -> list[dict]:
    """Return all FAQ entries in a given category."""
    return [
        {"question": e["question"], "answer": e["answer"]}
        for e in FAQ_ENTRIES
        if e["category"] == category
    ]


def get_all_faqs() -> list[dict]:
    """Return all FAQ entries grouped by category."""
    result = {}
    for entry in FAQ_ENTRIES:
        cat = entry["category"]
        if cat not in result:
            result[cat] = []
        result[cat].append({
            "question": entry["question"],
            "answer": entry["answer"],
        })
    return result
