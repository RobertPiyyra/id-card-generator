"""
Digit localization helpers for multilingual card rendering.
"""

from __future__ import annotations


ASCII_TO_LOCALIZED_DIGITS = {
    "urdu": str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"),
    "arabic": str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"),
    "hindi": str.maketrans("0123456789", "०१२३४५६७८९"),
}


def normalize_template_language(language: str | None) -> str:
    return (language or "english").strip().lower()


def _should_preserve_ascii_digits(text: str, *, field_type: str | None = None) -> bool:
    normalized_type = str(field_type or "").strip().lower()
    if normalized_type == "email":
        return True
    return "@" in text or "://" in text


def localize_digits_for_language(
    raw_text,
    language: str | None,
    *,
    field_type: str | None = None,
):
    text = str(raw_text or "")
    target_language = normalize_template_language(language)
    digit_map = ASCII_TO_LOCALIZED_DIGITS.get(target_language)
    if not text or not digit_map:
        return text
    if _should_preserve_ascii_digits(text, field_type=field_type):
        return text
    return text.translate(digit_map)
