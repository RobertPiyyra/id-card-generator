import subprocess

try:
    # Run git diff for app/routes/api_routes.py
    out = subprocess.check_output(
        ["git", "diff", "app/routes/api_routes.py"],
        cwd="/home/robertpiyyra/id_project"
    )
    with open("git_diff_api_routes.txt", "w", encoding="utf-8") as f:
        f.write(out.decode("utf-8", errors="replace"))
    print("Git diff written to git_diff_api_routes.txt")
except Exception as e:
    # Try local path
    try:
        out = subprocess.check_output(
            ["git", "diff", "app/routes/api_routes.py"]
        )
        with open("git_diff_api_routes.txt", "w", encoding="utf-8") as f:
            f.write(out.decode("utf-8", errors="replace"))
        print("Git diff written to git_diff_api_routes.txt locally")
    except Exception as e2:
        print(f"Error: {e} | {e2}")
