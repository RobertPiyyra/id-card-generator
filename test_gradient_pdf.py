#!/usr/bin/env python3
"""
Diagnostic: test whether the ReportLab gradient text approach works
and what errors occur. Run with:
    python3 /home/robertpiyyra/id_project/test_gradient_pdf.py
"""
import sys, io, traceback
sys.path.insert(0, "/home/robertpiyyra/id_project")

print("=== ReportLab gradient text diagnostic ===\n")

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import Color
    import reportlab
    print(f"ReportLab version: {reportlab.Version}")
except ImportError as e:
    print(f"FAIL: Cannot import ReportLab: {e}")
    sys.exit(1)

# ── Test 1: does _addShading exist? ──────────────────────────────────────────
buf = io.BytesIO()
c = canvas.Canvas(buf, pagesize=(595, 842))
has_add_shading = hasattr(c, "_addShading")
print(f"Test 1 – c._addShading exists: {has_add_shading}")

# ── Test 2: what does textObject._code contain after textOut? ─────────────────
try:
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase import pdfmetrics
    import os

    FONTS_FOLDER = "/home/robertpiyyra/id_project/static/fonts"
    font_file = None
    for fn in os.listdir(FONTS_FOLDER):
        if fn.lower().endswith(".ttf"):
            font_file = os.path.join(FONTS_FOLDER, fn)
            break

    if font_file:
        print(f"\nTest 2 – using font: {os.path.basename(font_file)}")
        pdfmetrics.registerFont(TTFont("TestFont", font_file))
        c.setFont("TestFont", 24)
        t = c.beginText()
        t.setFont("TestFont", 24)
        t.setTextOrigin(50, 400)
        t.textOut("Test Text")
        raw = list(getattr(t, "_code", []))
        print(f"  t._code ({len(raw)} ops): {raw}")
        show_ops = [op for op in raw
                    if op.strip().endswith(("Tj", "TJ"))
                    or op.strip().endswith((">Tj", ">TJ"))]
        print(f"  show_ops extracted: {show_ops}")
    else:
        print("\nTest 2 – no TTF font found in fonts folder, skipping")
except Exception as e:
    print(f"\nTest 2 FAIL: {e}")
    traceback.print_exc()

# ── Test 3: full gradient PDF round-trip ─────────────────────────────────────
print("\nTest 3 – generate gradient PDF...")
try:
    from reportlab.pdfbase.pdfdoc import PDFAxialShading
    from reportlab.pdfgen.canvas import _normalizeColors, _buildColorFunction, _gradientExtendStr

    buf2 = io.BytesIO()
    c2 = canvas.Canvas(buf2, pagesize=(595, 842), pdfVersion=(1, 4))

    if font_file:
        pdfmetrics.registerFont(TTFont("GradFont", font_file))
        c2.setFont("GradFont", 36)
        rl_alias = getattr(c2, "_fontname", "GradFont")
        fn_name = "GradFont"
    else:
        c2.setFont("Helvetica", 36)
        rl_alias = "Helvetica"
        fn_name = "Helvetica"

    x_pt, y_pt = 50.0, 400.0
    font_size_pt = 36.0
    grad_y_top = y_pt + font_size_pt
    grad_y_bot = y_pt

    colors_tuple = (Color(1, 0, 0), Color(0, 0, 1))   # red → blue
    colorSpace, ncolors = _normalizeColors(colors_tuple)
    fcn = _buildColorFunction(ncolors, (0.0, 1.0))
    shading = PDFAxialShading(x_pt, grad_y_top, x_pt, grad_y_bot,
                              Function=fcn, ColorSpace=colorSpace,
                              Extend=_gradientExtendStr(True))

    if has_add_shading:
        shading_name = c2._addShading(shading)
        print(f"  shading registered as: {shading_name!r}")
    else:
        print("  SKIP: _addShading not available")
        sys.exit(0)

    # Build encoded text ops
    t2 = c2.beginText()
    t2.setFont(fn_name, font_size_pt)
    t2.setTextOrigin(x_pt, y_pt)
    t2.textOut("Hello Gradient")
    raw2 = list(getattr(t2, "_code", []))
    show_ops2 = [op for op in raw2
                 if op.strip().endswith(("Tj", "TJ"))
                 or op.strip().endswith((">Tj", ">TJ"))]
    if not show_ops2:
        esc = "Hello Gradient".replace("\\","\\\\").replace("(","\\(").replace(")","\\)")
        show_ops2 = [f"({esc}) Tj"]
    print(f"  show_ops: {show_ops2}")

    # Emit PDF operators
    c2.saveState()
    bt_opened = False
    try:
        c2._code.append("BT")
        bt_opened = True
        c2._code.append(f"/{rl_alias} {font_size_pt:g} Tf")
        c2._code.append("7 Tr")
        c2._code.append(f"1 0 0 1 {x_pt:g} {y_pt:g} Tm")
        for op in show_ops2:
            c2._code.append(op)
        c2._code.append("ET")
        bt_opened = False
        c2._code.append(f"/{shading_name} sh")
        print("  Stream ops injected OK")
    except Exception as e2:
        if bt_opened:
            c2._code.append("ET")
        print(f"  Stream inject FAIL: {e2}")
    finally:
        c2.restoreState()

    c2.save()
    pdf_bytes = buf2.getvalue()
    print(f"  PDF generated: {len(pdf_bytes)} bytes")

    out_path = "/tmp/gradient_test.pdf"
    with open(out_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"  Saved to: {out_path}")
    print("\n=== PASS: PDF generated without crash ===")

except Exception as e:
    print(f"  Test 3 FAIL: {e}")
    traceback.print_exc()
