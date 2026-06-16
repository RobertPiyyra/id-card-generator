"""
Custom MCP Server for Python Backend Project
Gives Claude the ability to read & edit files in your project.
"""

import os
import shutil
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# ─── Configuration ────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/home/robertpiyyra/id_project"))

mcp = FastMCP("Python Backend MCP")


# ─── Safety Helper ────────────────────────────────────────────────────────────
def safe_path(relative_path: str) -> Path:
    """Resolve path and ensure it stays inside PROJECT_ROOT."""
    resolved = (PROJECT_ROOT / relative_path).resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT.resolve())):
        raise ValueError(f"Access denied: path is outside project root → {resolved}")
    return resolved


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_files(directory: str = ".") -> str:
    """
    List all files and folders inside a project directory.
    Use '.' for the project root.
    """
    target = safe_path(directory)
    if not target.exists():
        return f"Directory not found: {directory}"

    result = []
    for item in sorted(target.rglob("*")):
        parts = item.parts
        if any(p.startswith(".") or p in {"__pycache__", "node_modules", ".git", "venv", ".venv"} for p in parts):
            continue
        rel = item.relative_to(PROJECT_ROOT)
        prefix = "📁 " if item.is_dir() else "📄 "
        result.append(f"{prefix}{rel}")

    return "\n".join(result) if result else "No files found."


@mcp.tool()
def read_file(file_path: str) -> str:
    """
    Read the full content of a file in the project.
    Provide the path relative to the project root (e.g. 'app.py').
    """
    target = safe_path(file_path)
    if not target.exists():
        return f"File not found: {file_path}"
    if not target.is_file():
        return f"Not a file: {file_path}"

    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Cannot read binary file: {file_path}"


@mcp.tool()
def write_file(file_path: str, content: str) -> str:
    """
    Write (or overwrite) a file with new content.
    Creates parent directories if they don't exist.
    """
    target = safe_path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"✅ Written successfully: {file_path}"


@mcp.tool()
def edit_file(file_path: str, old_text: str, new_text: str) -> str:
    """
    Replace a specific block of text inside a file.
    - old_text: exact string to find and replace
    - new_text: what to replace it with
    """
    target = safe_path(file_path)
    if not target.exists():
        return f"File not found: {file_path}"

    original = target.read_text(encoding="utf-8")
    if old_text not in original:
        return f"❌ Text not found in {file_path}. Make sure it matches exactly."

    updated = original.replace(old_text, new_text, 1)
    target.write_text(updated, encoding="utf-8")
    return f"✅ Edit applied to {file_path}"


@mcp.tool()
def create_file(file_path: str, content: str = "") -> str:
    """
    Create a new file. Fails if the file already exists.
    """
    target = safe_path(file_path)
    if target.exists():
        return f"❌ File already exists: {file_path}. Use write_file to overwrite."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"✅ Created: {file_path}"


@mcp.tool()
def delete_file(file_path: str) -> str:
    """
    Delete a file from the project. Use with caution.
    """
    target = safe_path(file_path)
    if not target.exists():
        return f"File not found: {file_path}"
    if target.is_dir():
        return f"That's a directory. Use delete_folder instead."
    target.unlink()
    return f"🗑️ Deleted: {file_path}"


@mcp.tool()
def delete_folder(folder_path: str) -> str:
    """
    Delete a folder and everything inside it. Irreversible.
    """
    target = safe_path(folder_path)
    if not target.exists():
        return f"Folder not found: {folder_path}"
    if not target.is_dir():
        return f"Not a folder: {folder_path}"
    shutil.rmtree(target)
    return f"🗑️ Deleted folder: {folder_path}"


@mcp.tool()
def search_in_files(keyword: str, file_extension: str = ".py") -> str:
    """
    Search for a keyword across all project files of a given extension.
    """
    matches = []
    for file in PROJECT_ROOT.rglob(f"*{file_extension}"):
        parts = file.parts
        if any(p.startswith(".") or p in {"__pycache__", "venv", ".venv"} for p in parts):
            continue
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, start=1):
            if keyword.lower() in line.lower():
                rel = file.relative_to(PROJECT_ROOT)
                matches.append(f"{rel}:{i}  →  {line.strip()}")

    if not matches:
        return f"No matches found for '{keyword}' in *{file_extension} files."
    return "\n".join(matches)


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 MCP Server started | Project root: {PROJECT_ROOT}")
    mcp.run()
