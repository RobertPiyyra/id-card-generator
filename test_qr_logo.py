import sys, os
sys.path.insert(0, '/home/robertpiyyra/id_project')

from utils import generate_qr_code

# Find the most recent logo
logos_dir = '/home/robertpiyyra/id_project/static/logos'
logos = [f for f in os.listdir(logos_dir) if f.startswith('qr_logo_')]
logos.sort()
latest_logo = logos[-1] if logos else None
print(f"Using logo: {latest_logo}")

qr_settings = {
    "qr_style": "square",
    "qr_border": 2,
    "qr_fill_color": [0, 0, 0],
    "qr_back_color": [255, 255, 255],
    "qr_include_logo": True,
    "qr_logo_path": f"logos/{latest_logo}" if latest_logo else "",
}

# Test at size=120 (default)
img = generate_qr_code("TEST_DATA", qr_settings, size=120)
print(f"Output size: {img.size}")
img.save('/tmp/test_qr_logo_120.png')
print("Saved: /tmp/test_qr_logo_120.png")

# Test at size=200
img2 = generate_qr_code("TEST_DATA", qr_settings, size=200)
print(f"Output size (200): {img2.size}")
img2.save('/tmp/test_qr_logo_200.png')
print("Saved: /tmp/test_qr_logo_200.png")
