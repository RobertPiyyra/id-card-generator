from app.services.face_service import _face_crop_preferences, _fit_crop_box_to_image


def test_face_crop_is_shifted_inside_source_without_changing_ratio():
    box = _fit_crop_box_to_image((-31, 17, 295, 409), 278, 331, 260 / 313)
    x1, y1, x2, y2 = box

    assert 0 <= x1 <= x2 <= 278
    assert 0 <= y1 <= y2 <= 331
    assert abs(((x2 - x1) / (y2 - y1)) - (260 / 313)) < 1e-9


def test_face_crop_keeps_a_smaller_in_bounds_crop():
    box = _fit_crop_box_to_image((40, 50, 140, 170), 500, 700, 260 / 313)
    x1, y1, x2, y2 = box

    assert 99 < (x2 - x1) < 100
    assert abs(((x2 - x1) / (y2 - y1)) - (260 / 313)) < 1e-9


def test_face_crop_preferences_adapt_to_card_shape():
    assert _face_crop_preferences(260 / 324) == (0.50, 0.46)
    assert _face_crop_preferences(1.0) == (0.54, 0.38)
    assert _face_crop_preferences(1.5) == (0.46, 0.40)
