"""
Translation and language service.

Handles Google Translate integration, language detection, and text direction.
Extracted from legacy_app.py.
"""

import logging
import re
from functools import lru_cache

import requests
from flask import current_app

logger = logging.getLogger(__name__)

SUPPORTED_TEMPLATE_LANGUAGES = {"english", "urdu", "hindi", "arabic"}
LANGUAGE_TO_TRANSLATE_CODE = {
    "english": "en",
    "urdu": "ur",
    "hindi": "hi",
    "arabic": "ar",
}
NON_TRANSLATABLE_FIELD_KEYS = {"DOB", "MOBILE"}
NON_TRANSLATABLE_FIELD_TYPES = {"date", "number", "tel", "email"}


def default_text_direction_for_language(language):
    """Return 'rtl' for Urdu/Arabic, 'ltr' otherwise."""
    return "rtl" if str(language or "").strip().lower() in {"urdu", "arabic"} else "ltr"


def validate_double_sided_language_pair(front_language, back_language):
    """Check if both languages are supported."""
    front = str(front_language or "english").strip().lower()
    back = str(back_language or "english").strip().lower()
    return front in SUPPORTED_TEMPLATE_LANGUAGES and back in SUPPORTED_TEMPLATE_LANGUAGES


def _should_skip_translation(raw_value, field_key=None, field_type=None):
    """Determine if a value should be skipped during translation."""
    text = str(raw_value or "").strip()
    if not text:
        return True

    normalized_key = str(field_key or "").strip().upper()
    normalized_type = str(field_type or "").strip().lower()

    if normalized_key in NON_TRANSLATABLE_FIELD_KEYS:
        return True
    if normalized_type in NON_TRANSLATABLE_FIELD_TYPES:
        return True
    if "@" in text or "://" in text:
        return True

    letters = re.findall(r"[A-Za-z\u0600-\u06FF\u0900-\u097F]", text)
    if not letters:
        return True

    compact = re.sub(r"\s+", "", text)
    if compact and re.fullmatch(r"[\d\W_]+", compact):
        return True

    return False


def _extract_google_translate_text(payload):
    """Extract translated text from Google Translate response."""
    if not isinstance(payload, list) or not payload:
        return ""
    segments = payload[0]
    if not isinstance(segments, list):
        return ""
    return "".join(
        str(segment[0])
        for segment in segments
        if isinstance(segment, list) and segment and segment[0] is not None
    ).strip()


def detect_translation_source_language(raw_text, fallback="english"):
    """Detect the language of a text snippet."""
    text = str(raw_text or "").strip()
    if not text:
        return str(fallback or "english").strip().lower()

    if re.search(r"[\u0900-\u097F]", text):
        return "hindi"
    if re.search(r"[\u0600-\u06FF]", text):
        hinted = str(fallback or "").strip().lower()
        if hinted in {"urdu", "arabic"}:
            return hinted
        return "urdu"
    if re.search(r"[A-Za-z]", text):
        return "english"
    return str(fallback or "english").strip().lower()


@lru_cache(maxsize=4096)
def _google_translate_text(raw_text, source_language, target_language):
    """Translate text using Google Translate API (cached)."""
    text = str(raw_text or "").strip()
    source = str(source_language or "").strip().lower()
    target = str(target_language or "").strip().lower()
    if not text or source == target:
        return text

    source_code = LANGUAGE_TO_TRANSLATE_CODE.get(source)
    target_code = LANGUAGE_TO_TRANSLATE_CODE.get(target)
    if not source_code or not target_code:
        return text

    try:
        google_api_key = current_app.config.get("GOOGLE_TRANSLATE_API_KEY")
        if google_api_key:
            response = requests.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": google_api_key},
                json={
                    "q": text,
                    "source": source_code,
                    "target": target_code,
                    "format": "text",
                },
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            translated = (
                payload.get("data", {})
                .get("translations", [{}])[0]
                .get("translatedText", "")
            )
            return str(translated or "").strip() or text

        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": source_code,
                "tl": target_code,
                "dt": "t",
                "q": text,
            },
            timeout=8,
        )
        response.raise_for_status()
        translated = _extract_google_translate_text(response.json())
        return translated or text
    except Exception as exc:
        logger.warning(
            "Google translation failed for %s -> %s: %s",
            source, target, exc,
        )
        return text


def get_template_language_direction_from_obj(template_obj, side="front"):
    """Get language and text direction for a template side."""
    side_name = str(side or "front").strip().lower()
    if side_name == "back":
        lang = (
            getattr(template_obj, "back_language", None)
            or getattr(template_obj, "language", "english")
            or "english"
        ).strip().lower()
        direction = (
            getattr(template_obj, "back_text_direction", None)
            or getattr(template_obj, "text_direction", "ltr")
            or "ltr"
        ).strip().lower()
    else:
        lang = (
            getattr(template_obj, "language", "english") or "english"
        ).strip().lower()
        direction = (
            getattr(template_obj, "text_direction", "ltr") or "ltr"
        ).strip().lower()
    if direction == "rtl" and lang == "english":
        lang = "urdu"
    return lang, direction


def translate_value_for_template_side(template_obj, side, raw_value, *, field_key=None, field_type=None):
    """Translate a field value for a template side if needed."""
    text = str(raw_value or "")
    if not template_obj:
        return text

    target_language, _ = get_template_language_direction_from_obj(template_obj, side=side)
    source_hint = (
        getattr(template_obj, "language", "english") or "english"
    ).strip().lower()
    source_language = detect_translation_source_language(text, fallback=source_hint)

    if source_language == target_language:
        return text
    if source_language not in SUPPORTED_TEMPLATE_LANGUAGES or target_language not in SUPPORTED_TEMPLATE_LANGUAGES:
        return text
    if _should_skip_translation(text, field_key=field_key, field_type=field_type):
        return text

    return _google_translate_text(text, source_language, target_language)
