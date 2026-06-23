"""
app/utils/ - Utility package for the ID Card Generator.

Submodules:
  - paths: Path constants and directory setup
  - print_sizes: Print dimension constants (DPI, card sizes, sheet sizes)
  - color_utils: Color parsing and conversion
  - font_utils: Font loading, validation, and caching
  - template_utils: Template resolution and settings helpers
  - text_utils: Text processing, bidi, and drawing helpers
  - image_utils: Image loading, processing, and PDF conversion
  - qr_utils: QR code and barcode generation helpers
  - data_utils: Data hashing and localization helpers
  - layout_utils: Layout config parsing and field positioning
  - helper_utils: General helper functions
  - security_utils: Security helpers
  - fonts: Font management
"""

# Re-export commonly used items for backward compatibility
from utils import *  # noqa: F401,F403
