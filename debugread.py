from pathlib import Path
p = Path("input_pii.txt")
print("Working dir:", Path.cwd())
print("Path exists:", p.exists())
print("Absolute path:", p.resolve() if p.exists() else "N/A")
if p.exists():
    b = p.read_bytes()
    print("Size (bytes):", len(b))
    print("First 200 bytes (repr):", repr(b[:200]))
    try:
        txt = p.read_text(encoding="utf-8")
        print("Read as UTF-8 OK. Lines repr:", [repr(line) for line in txt.splitlines()])
    except Exception as e:
        print("Read as UTF-8 failed:", e)
        # try other common encodings
        try:
            txt2 = p.read_text(encoding="utf-16")
            print("Read as UTF-16 OK. Lines repr:", [repr(line) for line in txt2.splitlines()])
        except Exception as e2:
            print("Also failed UTF-16:", e2)
