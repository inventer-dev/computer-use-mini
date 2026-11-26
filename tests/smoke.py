#!/usr/bin/env python3
"""Smoke test del puente MCP: habla stdio con cu_mini.py."""
import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "cu_mini.py"
CMD = [sys.executable, str(SERVER)]


def run(messages):
    p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    out, _ = p.communicate("\n".join(json.dumps(m) for m in messages) + "\n", timeout=180)
    return [json.loads(l) for l in out.splitlines() if l.strip()]


msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2026-07-28", "capabilities": {}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "windows", "arguments": {}}},
]
res = run(msgs)
print("respuestas:", len(res))
print("init:", json.dumps(res[0].get("result", {}))[:160])
tools = res[1]["result"]["tools"]
print("tools:", len(tools), [t["name"] for t in tools])
w = res[2]["result"]
print("windows ->", (w.get("content") or [{}])[0].get("text", "")[:400])
