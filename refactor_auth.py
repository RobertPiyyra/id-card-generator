import re, glob

for file in glob.glob("app/routes/*.py"):
    with open(file, "r") as f:
        content = f.read()
    
    if "from app.legacy_app import login_required" not in content:
        content = "from app.legacy_app import login_required\n" + content

    pattern = re.compile(
        r'(@[\w_]+\.route\([^)]+\)\n(?:@[^\n]+\n)*)'  # Route decorator(s)
        r'(def [^\(]+\([^)]*\):[ \t]*\n)'             # Function definition
        r'((?:[ \t]*\"\"\"[\s\S]*?\"\"\"[ \t]*\n)?)'  # Optional docstring
        r'([ \t]+)if not session\.get\("admin"\)[^:]*:[ \t]*\n' # The check
        r'(?:[ \t]+[A-Za-z_][^\n]+\n)*'               # any following lines that are part of the if block
        r'(?:[ \t]+return[^\n]+\n(?:[^\S\n]+return[^\n]+\n)*)' # The return statement
    )
    
    def replacer(m):
        route_decs = m.group(1)
        func_def = m.group(2)
        docstring = m.group(3)
        return f"{route_decs}@login_required\n{func_def}{docstring}"

    new_content = pattern.sub(replacer, content)
    
    # some checks can be on the same line: if not session.get("admin"): return redirect(...)
    pattern2 = re.compile(
        r'(@[\w_]+\.route\([^)]+\)\n(?:@[^\n]+\n)*)'
        r'(def [^\(]+\([^)]*\):[ \t]*\n)'
        r'((?:[ \t]*\"\"\"[\s\S]*?\"\"\"[ \t]*\n)?)'
        r'([ \t]+)if not session\.get\("admin"\)[^:]*:.*return[^\n]*\n'
    )

    new_content = pattern2.sub(replacer, new_content)

    if content != new_content:
        with open(file, "w") as f:
            f.write(new_content)
        print(f"Updated {file}")
