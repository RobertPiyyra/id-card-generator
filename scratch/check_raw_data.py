import os
import glob

db_files = glob.glob('instance/*.db')
targets = [
    b'6163650db4584052a3938a4e03e955e8',
    b'510aa258ae9a46bc8ab0d85529cf424f',
    b'eb4c6bdfc88146e8a8132cfdb4718221'
]

for path in db_files:
    try:
        with open(path, 'rb') as f:
            content = f.read()
        results = []
        for target in targets:
            count = content.count(target)
            if count > 0:
                results.append(f"{target.decode()}: {count}")
        if results:
            print(f"File {path} contains hashes -> {', '.join(results)}")
    except Exception as e:
        print(f"Error reading {path}: {e}")
print("Scan complete.")
