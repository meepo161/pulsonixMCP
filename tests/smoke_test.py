"""Smoke test against a real Pulsonix 10.5 install (headless mode, read-only).

Usage:  python tests/smoke_test.py [path\\to\\design.sch]
"""

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from pulsonix_mcp.bridge import Bridge  # noqa: E402


def show(title, r, n=1200):
    print(f"--- {title} ({r.get('elapsed_s')}s)")
    print(json.dumps(r["result"], ensure_ascii=False)[:n])


def main():
    root = Path(__file__).resolve().parents[1]
    design = sys.argv[1] if len(sys.argv) > 1 else glob.glob(str(root / "*.sch"))[0]
    b = Bridge()
    print("pulsonix GUI running:", b.pulsonix_running())
    show("summary", b.run("summary", design=design))
    first = b.run("list_components", {"limit": 1, "include_attributes": False}, design=design)["result"]["components"][0]["name"]
    show(f"get_component {first}", b.run("get_component", {"name": first}, design=design))
    r = b.run("list_components", {"name": "QF*", "include_attributes": True}, design=design)
    show("list_components QF*", r)
    r = b.run("list_nets", {"min_pins": 2, "limit": 5}, design=design)
    show("list_nets", r)
    r = b.run("bom", {}, design=design)
    show("bom", r, 800)
    r = b.run("netlist", {}, design=design)
    print("netlist nets:", r["result"]["nets"])
    r = b.run("run_script", {"code": "var n=0; each(D.Components, function(c){ if(c.CountGates()>1) n++; }); log('ok'); return n;"},
              design=design)
    show("run_script multi-gate count", r)
    try:
        b.run("get_component", {"name": "NOPE"}, design=design)
    except Exception as e:  # expected
        print("--- expected error:", e)


if __name__ == "__main__":
    main()
