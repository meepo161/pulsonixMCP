"""Live-mode test: needs a Pulsonix window open with a design (read-only ops only).

Usage:  python tests/live_test.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from pulsonix_mcp.bridge import Bridge  # noqa: E402

b = Bridge()
print("DDE server available:", b.pulsonix_running())
first = b.run("list_components", {"limit": 1, "include_attributes": False}, mode="live")["result"]["components"][0]["name"]
for op, args in [("app_info", {}), ("summary", {}), ("get_component", {"name": first})]:
    r = b.run(op, args, mode="live", timeout_s=30)
    print(f"--- {op}: ack={r.get('dde_acknowledged')} {r.get('elapsed_s')}s")
    print(json.dumps(r["result"], ensure_ascii=False)[:600])
