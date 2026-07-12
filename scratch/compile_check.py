import os
import py_compile
import sys

def check_all_files():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print(f"Project root: {project_root}")
    
    error_count = 0
    compiled_count = 0
    for root, dirs, files in os.walk(project_root):
        if "venv" in root or ".git" in root or "__pycache__" in root or ".pytest_cache" in root or "scratch" in root:
            continue
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, project_root)
                try:
                    py_compile.compile(file_path, doraise=True)
                    compiled_count += 1
                except Exception as e:
                    print(f"Error compiling {rel_path}: {e}")
                    error_count += 1
                    
    print(f"Checked: compiled {compiled_count} files successfully. Errors found: {error_count}")
    if error_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    check_all_files()
