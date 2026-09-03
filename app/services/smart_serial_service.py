"""
Smart Serial Number & Checksum Service.

Provides tamper-evident serial generation and validation using:
- ISO 7064 Mod 37, 36 (Alphanumeric checksums)
- Luhn Mod 10 (Numeric checksums)
- Configurable pattern templates (e.g., {PREFIX}{YY}-{SEQ:4}{CHECK})
"""

import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def calculate_mod37_checksum(input_str: str) -> str:
    """
    Computes ISO/IEC 7064 Mod 37, 36 check character over alphanumeric input.
    Ignores hyphens and special punctuation.
    """
    cleaned = "".join(c.upper() for c in str(input_str or "") if c.isalnum())
    if not cleaned:
        return "0"

    val = 0
    for char in cleaned:
        char_val = ALPHABET.index(char) if char in ALPHABET else 0
        val = ((val + char_val) * 2) % 37

    check_val = (38 - val) % 37
    if check_val == 36:
        return "X"
    return ALPHABET[check_val % 36]


def calculate_luhn_checksum(digits: str) -> str:
    """Computes Luhn (Mod 10) check digit over digits."""
    cleaned = "".join(c for c in str(digits or "") if c.isdigit())
    if not cleaned:
        return "0"

    total = 0
    reverse_digits = cleaned[::-1]
    for i, d in enumerate(reverse_digits):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n

    check = (10 - (total % 10)) % 10
    return str(check)


def format_sequence_placeholder(pattern: str, seq_num: int) -> str:
    """Replaces {SEQ} or {SEQ:digits} with zero-padded sequence numbers."""
    def _repl(match):
        digits_str = match.group(1)
        if digits_str:
            digits = int(digits_str)
            return f"{seq_num:0{digits}d}"
        return str(seq_num)

    return re.sub(r'\{SEQ(?::(\d+))?\}', _repl, pattern, flags=re.IGNORECASE)


def generate_smart_serial(
    pattern: Optional[str] = None,
    sequence_num: int = 1,
    prefix: str = "SCH-",
    year: Optional[int] = None,
    class_name: Optional[str] = None,
    checksum_algo: str = "mod37"
) -> str:
    """
    Generates a single checksummed serial string according to the given pattern.
    """
    if not pattern:
        pattern = "{PREFIX}{YY}-{SEQ:4}{CHECK}"

    now = datetime.now(timezone.utc)
    current_year = year or now.year
    yy = f"{current_year % 100:02d}"
    yyyy = f"{current_year:04d}"
    clean_prefix = prefix or ""
    clean_class = "".join(c for c in str(class_name or "") if c.isalnum()).upper()

    # Step 1: Substitute static fields
    formatted = pattern
    formatted = re.sub(r'\{PREFIX\}', clean_prefix, formatted, flags=re.IGNORECASE)
    formatted = re.sub(r'\{YYYY\}', yyyy, formatted, flags=re.IGNORECASE)
    formatted = re.sub(r'\{YY\}', yy, formatted, flags=re.IGNORECASE)
    formatted = re.sub(r'\{CLASS\}', clean_class, formatted, flags=re.IGNORECASE)

    # Step 2: Sequence substitution
    formatted = format_sequence_placeholder(formatted, sequence_num)

    # Step 3: Checksum calculation
    has_check_tag = bool(re.search(r'\{CHECK\}', formatted, flags=re.IGNORECASE))
    body_for_checksum = re.sub(r'\{CHECK\}', '', formatted, flags=re.IGNORECASE)

    check_char = ""
    if checksum_algo == "mod37":
        check_char = calculate_mod37_checksum(body_for_checksum)
    elif checksum_algo == "luhn":
        check_char = calculate_luhn_checksum(body_for_checksum)

    if has_check_tag:
        return re.sub(r'\{CHECK\}', check_char, formatted, flags=re.IGNORECASE)
    elif checksum_algo in ("mod37", "luhn") and check_char:
        return f"{formatted}{check_char}"

    return formatted


def validate_smart_serial(
    serial_str: str,
    checksum_algo: str = "mod37"
) -> Dict[str, Any]:
    """
    Validates the integrity of a serial number by extracting and recalculating its check digit.
    """
    cleaned = str(serial_str or "").strip()
    if not cleaned:
        return {"is_valid": False, "error": "Empty serial number"}

    if checksum_algo == "none":
        return {"is_valid": True, "checksum_algo": "none", "serial": cleaned}

    if len(cleaned) < 2:
        return {"is_valid": False, "error": "Serial too short"}

    # Payload is everything except last char, last char is check char
    payload = cleaned[:-1]
    actual_check = cleaned[-1].upper()

    expected_check = ""
    if checksum_algo == "mod37":
        expected_check = calculate_mod37_checksum(payload)
    elif checksum_algo == "luhn":
        expected_check = calculate_luhn_checksum(payload)
    else:
        return {"is_valid": False, "error": f"Unknown checksum algorithm '{checksum_algo}'"}

    is_match = (actual_check == expected_check)
    return {
        "is_valid": is_match,
        "serial": cleaned,
        "payload": payload,
        "actual_checksum": actual_check,
        "expected_checksum": expected_check,
        "checksum_algo": checksum_algo
    }


def batch_generate_smart_serials(
    count: int,
    start_sequence: int = 1,
    pattern: Optional[str] = None,
    prefix: str = "SCH-",
    class_name: Optional[str] = None,
    checksum_algo: str = "mod37"
) -> List[str]:
    """Generates a batch of sequential smart serial numbers."""
    serials = []
    for i in range(count):
        seq = start_sequence + i
        s = generate_smart_serial(
            pattern=pattern,
            sequence_num=seq,
            prefix=prefix,
            class_name=class_name,
            checksum_algo=checksum_algo
        )
        serials.append(s)
    return serials
