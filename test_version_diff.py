#!/usr/bin/env python3
"""
Unit/integration test script to verify template version diffing logic
and the corresponding Flask API endpoint.
"""
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import Template, TemplateVersion
from app.routes.api_routes import compare_template_snapshots

def run_diff_tests():
    print("=== TESTING TEMPLATE VERSION DIFF LOGIC ===")

    # 1. Test compare_template_snapshots directly with mock data
    s1 = {
        "card_orientation": "portrait",
        "language": "english",
        "card_width": 240,
        "font_settings": {
            "font_regular": "arial.ttf",
            "label_font_size": 12,
            "label_font_color": [0, 0, 0]
        },
        "photo_settings": {
            "photo_x": 10,
            "photo_y": 20
        },
        "layout_config": {
            "fields": {
                "name": {"x": 10, "y": 50, "visible": True},
                "address": {"x": 10, "y": 70, "visible": True}
            },
            "objects": [
                {"id": "rect1", "type": "rect", "x": 5, "y": 5, "width": 50, "height": 50}
            ]
        }
    }

    s2 = {
        "card_orientation": "landscape", # Changed
        "language": "english",
        "card_width": 240,
        "font_settings": {
            "font_regular": "helvetica.ttf", # Changed
            "label_font_size": 12,
            "label_font_color": [255, 0, 0] # Changed
        },
        "photo_settings": {
            "photo_x": 15, # Changed
            "photo_y": 20
        },
        "layout_config": {
            "fields": {
                "name": {"x": 12, "y": 50, "visible": True}, # Changed x
                "address": {"x": 10, "y": 70, "visible": False} # Changed visible
            },
            "objects": [
                # rect1 modified, new circle added
                {"id": "rect1", "type": "rect", "x": 5, "y": 5, "width": 60, "height": 50},
                {"id": "circle1", "type": "circle", "x": 100, "y": 100, "radius": 10}
            ]
        }
    }

    print("\nRunning compare_template_snapshots directly...")
    diffs = compare_template_snapshots(s1, s2)
    
    # Assertions
    assert "core" in diffs
    assert diffs["core"]["card_orientation"] == {"from": "portrait", "to": "landscape"}
    print("✓ Core differences tracked successfully.")

    assert "font_settings" in diffs
    assert diffs["font_settings"]["font_regular"] == {"from": "arial.ttf", "to": "helvetica.ttf"}
    assert diffs["font_settings"]["label_font_color"] == {"from": [0, 0, 0], "to": [255, 0, 0]}
    print("✓ Font settings differences tracked successfully.")

    assert "photo_settings" in diffs
    assert diffs["photo_settings"]["photo_x"] == {"from": 10, "to": 15}
    print("✓ Photo settings differences tracked successfully.")

    assert "layout_config" in diffs
    fields_diff = diffs["layout_config"]["fields"]
    assert fields_diff["name"]["x"] == {"from": 10, "to": 12}
    assert fields_diff["address"]["visible"] == {"from": True, "to": False}
    print("✓ Field coordinates and visibility differences tracked successfully.")

    objects_diff = diffs["layout_config"]["objects"]
    assert len(objects_diff["added"]) == 1
    assert objects_diff["added"][0]["id"] == "circle1"
    assert len(objects_diff["modified"]) == 1
    assert objects_diff["modified"][0]["id"] == "rect1"
    assert objects_diff["modified"][0]["changes"]["width"] == {"from": 50, "to": 60}
    print("✓ Custom layout objects (added/modified/deleted) tracked successfully.")

    # 2. Test Flask API endpoint using Flask Test Client
    print("\nTesting version-diff API endpoint via Flask client...")
    with app.test_client() as client:
        # Enable admin session
        with client.session_transaction() as sess:
            sess["admin"] = True
            sess["student_email"] = "admin@school.edu"

        # Query a real template in database, or create a mock template
        with app.app_context():
            template = Template.query.first()
            if not template:
                # Create a temporary test template if none exists
                template = Template(
                    school_name="Test School",
                    filename="test_template.png",
                    language="english",
                    card_orientation="portrait",
                    layout_config="{}"
                )
                db.session.add(template)
                db.session.commit()

            template_id = template.id
            
            # Create two test versions
            v1 = TemplateVersion(
                template_id=template_id,
                version_number=1,
                snapshot_json=s1,
                source="admin_settings",
                created_by="test-user"
            )
            v2 = TemplateVersion(
                template_id=template_id,
                version_number=2,
                snapshot_json=s2,
                source="admin_settings",
                created_by="test-user"
            )
            db.session.add(v1)
            db.session.add(v2)
            db.session.commit()
            
            v1_id = v1.id
            v2_id = v2.id

        # Make GET request to the diff API
        url = f"/admin/template/{template_id}/version-diff/{v1_id}/{v2_id}"
        response = client.get(url)
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["v1_number"] == 1
        assert data["v2_number"] == 2
        assert "diffs" in data
        assert "core" in data["diffs"]
        
        print("✓ API Endpoint returned valid diff JSON data successfully.")

        # Clean up database test entries
        with app.app_context():
            v1_db = TemplateVersion.query.get(v1_id)
            v2_db = TemplateVersion.query.get(v2_id)
            if v1_db: db.session.delete(v1_db)
            if v2_db: db.session.delete(v2_db)
            db.session.commit()
            print("✓ Database cleaned up.")

    print("\nALL BACKEND DIFF TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    try:
        run_diff_tests()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
