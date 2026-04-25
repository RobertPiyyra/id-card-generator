import unittest

from corel_routes import (
    DEFAULT_EXPORT_MODE,
    LAYOUT_DPI,
    PRINT_DPI,
    parse_pdf_export_mode,
    _render_profile,
    _is_probably_pdf_source,
    _queue_hb_run,
)
from utils import format_label_for_drawing, split_label_and_colon, colon_anchor_for_value, get_default_font_config


class CorelExportModeTests(unittest.TestCase):
    def test_parse_mode_defaults_to_print(self):
        self.assertEqual(parse_pdf_export_mode(None), DEFAULT_EXPORT_MODE)
        self.assertEqual(parse_pdf_export_mode(""), DEFAULT_EXPORT_MODE)

    def test_parse_mode_accepts_known_values(self):
        self.assertEqual(parse_pdf_export_mode("editable"), "editable")
        self.assertEqual(parse_pdf_export_mode("PRINT"), "print")

    def test_parse_mode_rejects_unknown_values(self):
        self.assertIsNone(parse_pdf_export_mode("vector"))
        self.assertIsNone(parse_pdf_export_mode("anything-else"))

    def test_render_profile_for_editable(self):
        profile = _render_profile("editable")
        self.assertEqual(profile["layout_dpi"], LAYOUT_DPI)
        self.assertEqual(profile["asset_dpi"], LAYOUT_DPI)
        self.assertEqual(profile["raster_multiplier"], 1)

    def test_render_profile_for_print(self):
        profile = _render_profile("print")
        self.assertEqual(profile["layout_dpi"], LAYOUT_DPI)
        self.assertEqual(profile["asset_dpi"], PRINT_DPI)
        self.assertEqual(profile["raster_multiplier"], 2)

    def test_pdf_source_detection(self):
        self.assertTrue(_is_probably_pdf_source("https://x/y/file.pdf"))
        self.assertTrue(_is_probably_pdf_source("https://x/y/raw/upload/abc", content_type="application/pdf"))
        self.assertTrue(_is_probably_pdf_source("https://x/y/raw/upload/abc", content=b"%PDF-1.7 ..."))
        self.assertFalse(_is_probably_pdf_source("https://x/y/image.png", content_type="image/png", content=b"\x89PNG"))

    def test_queue_hb_run_adds_rtl_run(self):
        runs = []
        _queue_hb_run(
            runs,
            page_index=0,
            card_x=10,
            card_w_pt=200,
            card_bottom_y=20,
            card_h_pt=120,
            x_px=40,
            y_px=30,
            max_w_pt=120,
            box_h_pt=30,
            scale=72.0 / 300.0,
            direction="rtl",
            text="محمد",
            font_file="ARABIAN.TTF",
            font_size_pt=10,
            color_rgb=(0, 0, 0),
        )
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["align"], "right")
        self.assertEqual(run["direction"], "rtl")
        self.assertGreater(run["x1"], run["x0"])

    def test_label_colon_toggle(self):
        self.assertEqual(format_label_for_drawing("NAME", "english", "ltr", include_colon=True), "NAME:")
        self.assertEqual(format_label_for_drawing("NAME", "english", "ltr", include_colon=False), "NAME")

    def test_label_colon_alignment_split(self):
        label_text, colon_text = split_label_and_colon("NAME", "english", "ltr", include_colon=True, align_colon=True)
        self.assertEqual(label_text, "NAME")
        self.assertEqual(colon_text, ":")

    def test_colon_anchor_defaults(self):
        self.assertEqual(colon_anchor_for_value(280, "ltr", gap_px=8), (272.0, "right"))
        self.assertEqual(colon_anchor_for_value(280, "rtl", gap_px=8), (288.0, "left"))

    def test_default_font_config_has_colon_color(self):
        cfg = get_default_font_config()
        self.assertIn("colon_font_color", cfg)


if __name__ == "__main__":
    unittest.main()
