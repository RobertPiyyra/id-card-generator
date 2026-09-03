import pytest
from app.services.smart_serial_service import (
    calculate_mod37_checksum,
    calculate_luhn_checksum,
    generate_smart_serial,
    validate_smart_serial,
    batch_generate_smart_serials,
)
from app.services.serial_batch_service import create_batch, _get_next_serial
from models import db, SerialBatch, Template


def test_mod37_checksum_calculation():
    # Deterministic check character calculation
    check1 = calculate_mod37_checksum("SCH-26-0001")
    assert len(check1) == 1
    assert check1.isalnum()

    # Same input produces identical checksum
    assert calculate_mod37_checksum("SCH-26-0001") == check1

    # Altering one character alters the checksum
    check2 = calculate_mod37_checksum("SCH-26-0002")
    assert check1 != check2


def test_luhn_checksum_calculation():
    check = calculate_luhn_checksum("7992739871")
    assert check == "3"


def test_smart_serial_generation_and_validation():
    # Generate serial
    serial = generate_smart_serial(
        pattern="{PREFIX}{YY}-{SEQ:4}{CHECK}",
        sequence_num=42,
        prefix="DPS-",
        year=2026,
        checksum_algo="mod37"
    )
    assert serial.startswith("DPS-26-0042")
    assert len(serial) == len("DPS-26-0042") + 1

    # Validate intact serial
    res = validate_smart_serial(serial, checksum_algo="mod37")
    assert res["is_valid"] is True
    assert res["actual_checksum"] == res["expected_checksum"]

    # Tampering with a character (e.g. changing 42 to 43 without changing check digit)
    tampered = serial[:-2] + "3" + serial[-1]
    res_tampered = validate_smart_serial(tampered, checksum_algo="mod37")
    assert res_tampered["is_valid"] is False


def test_custom_pattern_with_class_placeholder():
    serial = generate_smart_serial(
        pattern="{PREFIX}-{CLASS}-{SEQ:3}{CHECK}",
        sequence_num=5,
        prefix="SCH",
        class_name="10-B",
        checksum_algo="mod37"
    )
    assert serial.startswith("SCH-10B-005")
    assert validate_smart_serial(serial, checksum_algo="mod37")["is_valid"] is True


def test_batch_generation():
    serials = batch_generate_smart_serials(
        count=10,
        start_sequence=1,
        prefix="DPS-",
        checksum_algo="mod37"
    )
    assert len(serials) == 10
    assert len(set(serials)) == 10  # All unique

    for s in serials:
        assert validate_smart_serial(s, checksum_algo="mod37")["is_valid"] is True


def test_serial_batch_service_integration(app, db):
    with app.app_context():
        school_name = "Smart School"
        template = Template(school_name=school_name)
        db.session.add(template)
        db.session.commit()

        batch = create_batch(
            school_name=school_name,
            template_id=template.id,
            prefix="SMS-",
            class_name="9th",
            serial_pattern="{PREFIX}{YY}-{SEQ:3}{CHECK}",
            checksum_algorithm="mod37",
            start_sequence=1
        )

        next_serial = _get_next_serial(batch)
        assert next_serial.startswith("SMS-")
        assert validate_smart_serial(next_serial, checksum_algo="mod37")["is_valid"] is True
