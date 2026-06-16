#!/usr/bin/env python
"""
Test script to verify that address_max_lines setting is properly respected
when rendering addresses in different rendering modes.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.render_service import fit_wrapped_text_pil
from utils import load_font_dynamic

def _load_font_pil(name, size):
    return load_font_dynamic(name, "X", None, size)

def test_fit_wrapped_text_pil():
    """Test that fit_wrapped_text_pil respects max_lines limit."""
    print("\n=== Testing fit_wrapped_text_pil ===")
    
    # Test case: Long address that would wrap to many lines
    long_address = "This is a very long address that would normally wrap to many lines when rendered at the specified width"
    
    # Create a simple font loader
    font_loader = lambda size_px: _load_font_pil("arial.ttf", size_px)
    
    test_cases = [
        (40, 1, "1 line max"),
        (40, 2, "2 lines max"),
        (40, 3, "3 lines max"),
    ]
    
    for start_size_px, max_lines, description in test_cases:
        best_size, lines = fit_wrapped_text_pil(
            long_address,
            font_loader,
            start_size_px,
            min_size_px=10,
            max_width_px=400,
            max_lines=max_lines,
            char_spacing=0,
            lang='english'
        )
        
        result = f"  {description}: {len(lines)} lines returned"
        if len(lines) <= max_lines:
            result += " ✓ PASS"
        else:
            result += f" ✗ FAIL (expected max {max_lines})"
        print(result)

def test_corel_fit_wrapped_text():
    """Test that _fit_wrapped_text in corel_routes respects max_lines limit."""
    print("\n=== Testing _fit_wrapped_text (corel_routes) ===")
    
    from app.routes.corel_routes import _fit_wrapped_text
    
    # Test case: Long address text
    long_address = "This is a very long address that would normally wrap to many lines when rendered at the specified width"
    
    test_cases = [
        (12, 1, "1 line max"),
        (12, 2, "2 lines max"),
        (12, 3, "3 lines max"),
    ]
    
    for start_size_pt, max_lines, description in test_cases:
        best_size, lines = _fit_wrapped_text(
            long_address,
            font_name='Helvevetica',
            start_size_pt=start_size_pt,
            min_size_pt=8,
            max_width_pt=200,
            max_lines=max_lines,
            max_height_pt=None,
            line_height_factor=1.15,
        )
        
        result = f"  {description}: {len(lines)} lines returned"
        if len(lines) <= max_lines:
            result += " ✓ PASS"
        else:
            result += f" ✗ FAIL (expected max {max_lines})"
        print(result)

if __name__ == "__main__":
    print("Testing address_max_lines functionality...")
    
    try:
        test_corel_fit_wrapped_text()
    except Exception as e:
        print(f"Error testing corel_routes: {e}")
    
    try:
        test_fit_wrapped_text_pil()
    except Exception as e:
        print(f"Error testing render_service: {e}")
    
    print("\nDone!")
