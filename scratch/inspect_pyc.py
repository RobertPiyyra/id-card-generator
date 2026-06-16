import marshal
import sys
import dis

def print_names_recursive(code_obj, depth=0):
    indent = "  " * depth
    print(f"{indent}- {code_obj.co_name} (lines {code_obj.co_firstlineno}+)")
    for const in code_obj.co_consts:
        if isinstance(const, type(code_obj)):
            print_names_recursive(const, depth + 1)

def main():
    pyc_path = "legacy_app_backup.pyc"
    with open(pyc_path, "rb") as f:
        f.read(16)
        try:
            code_obj = marshal.load(f)
        except Exception as e:
            print("Failed to marshal load:", e)
            return

    print("All functions recursively:")
    print_names_recursive(code_obj)

if __name__ == "__main__":
    main()
