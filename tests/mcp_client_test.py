"""End-to-end test: start the MCP server over stdio and call tools (headless mode).

Never modifies the original design: write tests use save_as into a temp folder.
Usage:  python tests/mcp_client_test.py [path\\to\\design.sch]
"""

import asyncio
import glob
import json
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")


def payload(res):
    if res.isError:
        return {"ERROR": res.content[0].text}
    return json.loads(res.content[0].text)


async def main():
    design = sys.argv[1] if len(sys.argv) > 1 else glob.glob(str(ROOT / "*.sch"))[0]
    params = StdioServerParameters(command=sys.executable, args=["-m", "pulsonix_mcp"], cwd=str(ROOT))
    failures = 0

    def check(cond, msg):
        nonlocal failures
        print(("PASS " if cond else "FAIL ") + msg)
        failures += 0 if cond else 1

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            check(len(tools) >= 25, f"{len(tools)} tools listed")

            st = payload(await s.call_tool("pulsonix_status", {}))
            check(st.get("exe_exists") is True, f"status: {st}")

            ref = await s.call_tool("pulsonix_api_reference", {"topic": "Component"})
            check("MoveTo" in ref.content[0].text, "api reference Component")

            summ = payload(await s.call_tool("pulsonix_design_summary", {"design": design}))
            check(summ.get("counts", {}).get("components", 0) > 0, f"summary counts {summ.get('counts')}")
            check(summ.get("file", "").lower() == str(Path(design).resolve()).lower(), "summary reports original path")

            lst = payload(await s.call_tool("pulsonix_list_components", {"design": design, "include_attributes": False}))
            REF = max(lst["components"], key=lambda c: c["pins"] or 0)["name"]
            comp = payload(await s.call_tool("pulsonix_get_component", {"design": design, "name": REF}))
            check(len(comp.get("connections", [])) > 0, f"{REF} connections")

            # dry run: change must not persist
            dry = payload(await s.call_tool("pulsonix_set_component_attributes",
                                            {"design": design, "changes": {REF: {"MCP_TEST": "dry"}}}))
            check("changed" in dry and "saved_to" not in dry["_meta"], "dry-run attribute change")
            again = payload(await s.call_tool("pulsonix_get_component", {"design": design, "name": REF}))
            check("MCP_TEST" not in again["attributes"], "original untouched after dry run")

            # save_as to temp, then verify on the new file
            out = Path(tempfile.gettempdir()) / "pulsonix_mcp_test" / "saved_copy.sch"
            out.parent.mkdir(exist_ok=True)
            wr = payload(await s.call_tool("pulsonix_set_component_attributes",
                                           {"design": design, "changes": {REF: {"MCP_TEST": "Привет 42"}},
                                            "save_as": str(out)}))
            check(wr.get("_meta", {}).get("saved_to") == str(out), f"save_as -> {wr.get('_meta')}")
            ver = payload(await s.call_tool("pulsonix_get_component", {"design": str(out), "name": REF}))
            check(ver.get("attributes", {}).get("MCP_TEST") == "Привет 42", "attribute persisted in saved copy (unicode)")

            mv = payload(await s.call_tool("pulsonix_move_component",
                                           {"design": str(out), "name": REF, "x_mm": 150, "y_mm": 500, "save": True}))
            check(abs(mv["after"]["x_mm"] - 150) < 0.01 and "backup" in mv["_meta"], f"move+save with backup {mv}")

            rs = payload(await s.call_tool("pulsonix_run_script",
                                           {"design": design, "code": "return {n: D.Nets.Count, a: args.k};", "args": {"k": "ы"}}))
            check(rs.get("a") == "ы" and rs.get("n", 0) > 0, f"run_script {rs}")

            bad = payload(await s.call_tool("pulsonix_get_component", {"design": design, "name": "NOPE"}))
            check("ERROR" in bad and "not found" in bad["ERROR"], f"error surfaces: {bad}")

    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
