import subprocess
import os

def run_git_restore():
    print("Attempting to restore route files using git...")
    try:
        # Run git checkout for app/routes directory
        subprocess.check_call(
            ["git", "checkout", "app/routes/"],
            cwd="/home/robertpiyyra/id_project"
        )
        print("Successfully restored app/routes/ via WSL path!")
        return True
    except Exception as e:
        try:
            subprocess.check_call(
                ["git", "checkout", "app/routes/"]
            )
            print("Successfully restored app/routes/ locally!")
            return True
        except Exception as e2:
            print(f"Failed to restore: {e} | {e2}")
            return False

if __name__ == "__main__":
    run_git_restore()
