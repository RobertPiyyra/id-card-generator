import sys
import os

print("Hello from test_db_json.py start!", flush=True)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from models import Student

print("Imported app and models successfully", flush=True)
app = create_app()
print("App created successfully", flush=True)

with app.app_context():
    try:
        q = Student.query.filter(Student.custom_data['serial_no'].as_string() == 'test')
        print("SQL Query representation:", flush=True)
        print(str(q), flush=True)
        print("Executing query...", flush=True)
        results = q.all()
        print(f"Success! Results length: {len(results)}", flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        print(f"CRASHED: {e}", flush=True)

sys.stdout.flush()
os._exit(0)
