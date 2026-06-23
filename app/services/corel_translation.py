"""
Translation functions for CorelDRAW export.

Extracted from app/services/corel_export_service.py — handles language
detection, text translation, and translation value extraction.

USAGE: These functions are identical copies of those in corel_export_service.py.
The original definitions shadow these imports at runtime.
"""
import logging
import re
from functools import lru_cache

import requests

logger = logging.getLogger(__name__)

# Constants from corel_export_service.py
NON_TRANSLATABLE_FIELD_KEYS = {"DOB", "MOBILE"}
NON_TRANSLATABLE_FIELD_TYPES = {"date", "number", "tel", "email"}
LANGUAGE_TO_TRANSLATE_CODE = {
    "english": "en",
    "urdu": "ur",
    "hindi": "hi",
    "arabic": "ar",
}
GOOGLE_TRANSLATE_API_KEY = ""
def _detect_translation_source_language(raw_text: str, fallback: str = "english") -> str:
    text = str(raw_text or "").strip()
    if not text:
        return _normalize_language(fallback)
    if re.search(r"[\u0900-\u097F]", text):
        return "hindi"
    if re.search(r"[\u0600-\u06FF]", text):
        hinted = _normalize_language(fallback)
        return hinted if hinted in {"urdu", "arabic"} else "urdu"
    if re.search(r"[A-Za-z]", text):
        return "english"
    return _normalize_language(fallback)




def _should_skip_translation(raw_value, field_key=None, field_type=None):
    text = str(raw_value or "").strip()
    if not text:
        return True
    normalized_key = str(field_key or "").strip().upper()
    normalized_type = str(field_type or "").strip().lower()
    if normalized_key in NON_TRANSLATABLE_FIELD_KEYS or normalized_type in NON_TRANSLATABLE_FIELD_TYPES:
        return True
    if "@" in text or "://" in text:
        return True
    letters = re.findall(r"[A-Za-z\u0600-\u06FF\u0900-\u097F]", text)
    if not letters:
        return True
    compact = re.sub(r"\s+", "", text)
    return bool(compact and re.fullmatch(r"[\d\W_]+", compact))




def _extract_google_translate_text(payload):
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


@lru_cache(maxsize=4096)


def _google_translate_text(raw_text: str, source_language: str, target_language: str) -> str:
    text = str(raw_text or "").strip()
    source = _normalize_language(source_language)
    target = _normalize_language(target_language)
    if not text or source == target:
        return text
    source_code = LANGUAGE_TO_TRANSLATE_CODE.get(source)
    target_code = LANGUAGE_TO_TRANSLATE_CODE.get(target)
    if not source_code or not target_code:
        return text
    try:
        if GOOGLE_TRANSLATE_API_KEY:
            response = requests.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": GOOGLE_TRANSLATE_API_KEY},
                json={"q": text, "source": source_code, "target": target_code, "format": "text"},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            translated = payload.get("data", {}).get("translations", [{}])[0].get("translatedText", "")
            return str(translated or "").strip() or text

        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": source_code, "tl": target_code, "dt": "t", "q": text},
            timeout=8,
        )
        response.raise_for_status()
        translated = _extract_google_translate_text(response.json())
        return translated or text
    except Exception as exc:
        logger.warning("Vector export translation failed for %s -> %s: %s", source, target, exc)
        return text




def _translate_value_for_export(raw_value, *, source_language: str, target_language: str, field_key=None, field_type=None):
    text = str(raw_value or "")
    actual_source = _detect_translation_source_language(text, fallback=source_language)
    actual_target = _normalize_language(target_language)
    if actual_source == actual_target:
        return text
    if _should_skip_translation(text, field_key=field_key, field_type=field_type):
        return text
    return _google_translate_text(text, actual_source, actual_target)




def _normalize_language(language: str) -> str:
    return (language or "english").strip().lower()


_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

_ORDER_TO_KEY = {
    10: "NAME",
    20: "F_NAME",
    30: "CLASS",
    40: "DOB",
    50: "MOBILE",
    60: "ADDRESS",
}



