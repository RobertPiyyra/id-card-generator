#!/usr/bin/env python3
"""
Quick test script to verify template loading from Cloudinary
"""
import os
import sys
from dotenv import load_dotenv

# Load env vars first
load_dotenv()

# Now import app
from app import app, db
from models import Template

def test_templates():
    with app.app_context():
        print("\n=== TESTING TEMPLATE LOADING ===\n")
        
        # Get all templates
        templates = Template.query.all()
        print(f"Total templates in DB: {len(templates)}\n")
        
        for tmpl in templates:
            print(f"Template ID: {tmpl.id}")
            print(f"  Name: {tmpl.name}")
            print(f"  Filename: {tmpl.filename}")
            print(f"  Template URL: {tmpl.template_url}")
            print(f"  School ID: {tmpl.school_id}")
            
            # Check if URL is valid
            if tmpl.template_url:
                if tmpl.template_url.startswith('http'):
                    print(f"  ✓ Has valid Cloudinary URL")
                else:
                    print(f"  ✗ URL doesn't look like Cloudinary: {tmpl.template_url[:50]}")
            else:
                print(f"  ✗ No template_url set, falling back to local file: {tmpl.filename}")
            
            # Try to load it
            print(f"  Testing load...")
            try:
                from utils import load_template_smart, get_template_path
                path = get_template_path(tmpl.id)
                print(f"    get_template_path returned: {path[:80] if path else 'None'}")
                
                if path:
                    img = load_template_smart(path)
                    print(f"    ✓ Successfully loaded: {img.size}")
                else:
                    print(f"    ✗ get_template_path returned None")
            except Exception as e:
                print(f"    ✗ Error: {e}")
            
            print()

if __name__ == '__main__':
    test_templates()
