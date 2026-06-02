import subprocess

try:
    # Run git log and get the last few commits for this file
    out = subprocess.check_output(
        ["git", "log", "-p", "-n", "3", "app/routes/corel_routes.py"],
        cwd="/home/robertpiyyra/id_project"
    )
    with open("/tmp/git_history_corel.txt", "w", encoding="utf-8") as f:
        f.write(out.decode("utf-8", errors="replace"))
    print("Git history written to /tmp/git_history_corel.txt")
except Exception as e:
    print(f"Error: {e}")
