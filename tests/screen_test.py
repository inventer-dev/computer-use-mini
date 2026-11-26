#!/usr/bin/env python3
"""Prueba `screen` con y sin imagen."""
import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "cu_mini.py"
CMD = [sys.executable, str(SERVER)]
if len(sys.argv) != 2:
    raise SystemExit(f"Uso: {Path(sys.argv[0]).name} <window_id>")
WID = int(sys.argv[1])


def run(messages):
    p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    out, _ = p.communicate("\n".join(json.dumps(m) for m in messages) + "\n", timeout=180)
    return [json.loads(l) for l in out.splitlines() if l.strip()]


for image in (False, True):
    res = run([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "screen", "arguments": {"window_id": WID, "image": image,
                                                    "max_elements": 60}}},
    ])
    r = res[1]["result"]
    blocks = r.get("content") or []
    kinds = [(b.get("type"), len(b.get("text") or b.get("data") or "")) for b in blocks]
    print(f"image={image}: blocks={kinds} isError={r.get('isError')}")
    for b in blocks:
        if b.get("type") == "text":
            print("  ", b["text"][:300].replace("\n", " | "))
