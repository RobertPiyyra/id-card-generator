import sys
import os
print("Hello before create_app")
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
print("Imported app successfully")
app = create_app()
print("Created app successfully")
os._exit(0)
