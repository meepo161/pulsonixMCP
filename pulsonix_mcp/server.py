"""MCP server exposing Pulsonix 10.5 design data and commands.

Run:  python -m pulsonix_mcp            (stdio transport)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from .apidocs import ApiDocsError, ensure_docs
from .bridge import Bridge, PulsonixError

mcp = FastMCP(
    "pulsonix",
    instructions=(
        "Tools for Pulsonix 10.5 schematics/PCBs via its legacy ActiveX scripting API.\n"
        "Two modes: 'headless' (default) runs a hidden Pulsonix on a temporary COPY of the design file "
        "given in `design` (a .sch/.pcb path), so the original is never touched unless save=true "
        "(a timestamped .bak copy is made first) or save_as is given. Write tools without save/save_as "
        "are a dry run. 'live' talks over DDE to the Pulsonix window the user has open and acts on its "
        "in-memory design (design path optional = active design); the user saves from the GUI, or pass save=true.\n"
        "Do not save in headless mode a file that is currently open in the Pulsonix GUI.\n"
        "Coordinates are in millimetres. Component pins appear as 'REF:PIN' (e.g. 'KM1:9').\n"
        "For anything not covered, use pulsonix_run_script (JScript) and pulsonix_api_reference."
    ),
)

_bridge: Bridge | None = None

Mode = Annotated[
    Literal["headless", "live"],
    Field(description="'headless' = hidden Pulsonix on a temp copy of `design`; 'live' = the Pulsonix window that is open now"),
]
DesignPath = Annotated[
    str | None,
    Field(description="Path to the .sch/.pcb file. Required in headless mode; in live mode omit to use the active design"),
]
Save = Annotated[bool, Field(description="Persist changes. Headless: overwrite the original after making a .bak copy. Live: Document.Save()")]
SaveAs = Annotated[str | None, Field(description="Headless only: write the modified design to this new path instead")]


def _b() -> Bridge:
    global _bridge
    if _bridge is None:
        _bridge = Bridge()
    return _bridge


async def _run(op: str, args: dict | None = None, *, design: str | None = None, mode: str = "headless",
               save: bool = False, save_as: str | None = None) -> dict:
    def call() -> dict:
        return _b().run(op, args, design=design, mode=mode, save=save, save_as=save_as)

    try:
        out = await anyio.to_thread.run_sync(call)
    except PulsonixError as e:
        raise ToolError(str(e)) from e
    res = out.pop("result")
    if isinstance(res, dict):
        return {**res, "_meta": out}
    return {"result": res, "_meta": out}


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# ---- status / docs -----------------------------------------------------------------


@mcp.tool()
async def pulsonix_status() -> dict:
    """Show Pulsonix install path, whether a Pulsonix window is running (live mode available) and, if so, which designs are open."""
    b = _b()
    info: dict[str, Any] = {"exe": b.cfg.exe, "exe_exists": os.path.isfile(b.cfg.exe), "work_dir": str(b.cfg.work_dir)}
    running = await anyio.to_thread.run_sync(b.pulsonix_running)
    info["gui_running"] = running
    if running:
        try:
            info["gui"] = await _run("app_info", mode="live")
        except ToolError as e:
            info["gui_error"] = str(e)
    return info


@mcp.tool()
def pulsonix_api_reference(
    topic: Annotated[str | None, Field(description="Object name, e.g. 'Component', 'Net', 'Document', 'Collection'. Omit to list topics")] = None,
) -> str:
    """Pulsonix scripting API reference, extracted from the installed Scripting.chm. Use before writing pulsonix_run_script code."""
    b = _b()
    try:
        docs = ensure_docs(b.cfg.exe, b.cfg.work_dir / "apidocs")
    except ApiDocsError as e:
        raise ToolError(str(e)) from e
    if not topic:
        names = sorted(p.stem for p in docs.glob("*.txt"))
        return "Available topics:\n" + ", ".join(names)
    p = docs / f"{topic.strip().lower()}.txt"
    if not p.is_file():
        raise ToolError(f"No topic '{topic}'. Call without topic to list them.")
    return p.read_text(encoding="utf-8-sig")


# ---- read --------------------------------------------------------------------------


@mcp.tool()
async def pulsonix_design_summary(design: DesignPath = None, mode: Mode = "headless") -> dict:
    """Overview of a design: type, units, document properties, technology file, counts of components/nets/parts/pages, variants."""
    return await _run("summary", design=design, mode=mode)


@mcp.tool()
async def pulsonix_list_components(
    design: DesignPath = None,
    name: Annotated[str | None, Field(description="Wildcard on reference, e.g. 'QF*', 'R1?'")] = None,
    part: Annotated[str | None, Field(description="Wildcard on part name")] = None,
    attribute_name: Annotated[str | None, Field(description="Only components having this attribute")] = None,
    attribute_value: Annotated[str | None, Field(description="Wildcard the attribute value must match")] = None,
    include_attributes: bool = True,
    include_gates: Annotated[bool, Field(description="Add per-gate symbol/position list")] = False,
    limit: int = 1000,
    mode: Mode = "headless",
) -> dict:
    """List components with part, symbol, position (mm), rotation, fitted/placed state and attributes. Filters are case-insensitive wildcards."""
    args = _clean({"name": name, "part": part, "include_attributes": include_attributes,
                   "include_gates": include_gates, "limit": limit})
    if attribute_name:
        args["attribute"] = _clean({"name": attribute_name, "value": attribute_value})
    return await _run("list_components", args, design=design, mode=mode)


@mcp.tool()
async def pulsonix_get_component(
    name: Annotated[str, Field(description="Component reference, e.g. 'KM1'")],
    design: DesignPath = None,
    mode: Mode = "headless",
) -> dict:
    """Full details of one component: attributes, all gates, part info and pin -> net connections."""
    return await _run("get_component", {"name": name}, design=design, mode=mode)


@mcp.tool()
async def pulsonix_list_nets(
    design: DesignPath = None,
    name: Annotated[str | None, Field(description="Wildcard on net name")] = None,
    min_pins: Annotated[int | None, Field(description="Only nets with at least this many component pins")] = None,
    include_nodes: bool = True,
    all_node_types: Annotated[bool, Field(description="Include junctions, doc-symbol pins etc., not only component pins")] = False,
    limit: int = 1000,
    mode: Mode = "headless",
) -> dict:
    """List nets with class, pin count and connected component pins ('REF:PIN')."""
    args = _clean({"name": name, "min_pins": min_pins, "include_nodes": include_nodes,
                   "all_node_types": all_node_types, "limit": limit})
    return await _run("list_nets", args, design=design, mode=mode)


@mcp.tool()
async def pulsonix_get_net(name: str, design: DesignPath = None, mode: Mode = "headless") -> dict:
    """One net with its class, attributes and every node (type + name)."""
    return await _run("get_net", {"name": name}, design=design, mode=mode)


@mcp.tool()
async def pulsonix_netlist(
    design: DesignPath = None,
    include_empty: Annotated[bool, Field(description="Also list nets without component pins")] = False,
    mode: Mode = "headless",
) -> dict:
    """Complete netlist: every net with its component pins ('REF:PIN')."""
    return await _run("netlist", {"include_empty": include_empty}, design=design, mode=mode)


@mcp.tool()
async def pulsonix_list_parts(
    design: DesignPath = None,
    name: Annotated[str | None, Field(description="Wildcard on part name")] = None,
    mode: Mode = "headless",
) -> dict:
    """Parts used in the design with description, name stem, pin count and which components use them."""
    return await _run("list_parts", _clean({"name": name}), design=design, mode=mode)


@mcp.tool()
async def pulsonix_bom(
    design: DesignPath = None,
    group_by_attributes: Annotated[list[str] | None, Field(description="Attributes to group by in addition to part name (default ['Value'])")] = None,
    fitted_only: bool = True,
    mode: Mode = "headless",
) -> dict:
    """Bill of materials grouped by part name (+ attributes) with quantities and reference lists."""
    args = _clean({"group_by_attributes": group_by_attributes, "fitted_only": fitted_only})
    return await _run("bom", args, design=design, mode=mode)


@mcp.tool()
async def pulsonix_list_texts(
    design: DesignPath = None,
    text: Annotated[str | None, Field(description="Wildcard on the text string")] = None,
    mode: Mode = "headless",
) -> dict:
    """Free text items on the design with positions (mm)."""
    return await _run("list_texts", _clean({"text": text}), design=design, mode=mode)


@mcp.tool()
async def pulsonix_list_attribute_names(design: DesignPath = None, mode: Mode = "headless") -> dict:
    """All attribute names defined in the design."""
    return await _run("list_attribute_names", design=design, mode=mode)


@mcp.tool()
async def pulsonix_design_rule_check(design: DesignPath = None, mode: Mode = "headless") -> dict:
    """Run DRC (PCB) / ERC (schematic) with current settings and return the error list."""
    return await _run("design_rule_check", design=design, mode=mode)


@mcp.tool()
async def pulsonix_list_cam_plots(design: DesignPath = None, mode: Mode = "headless") -> dict:
    """CAM/Plot definitions stored in the design (name, device type, enabled)."""
    return await _run("list_cam_plots", design=design, mode=mode)


@mcp.tool()
async def pulsonix_library_parts(
    library: Annotated[str, Field(description="Part library path or file name (searched in the configured library folders)")],
    name: Annotated[str | None, Field(description="Wildcard on part name")] = None,
    limit: int = 2000,
) -> dict:
    """List parts in a Pulsonix part library (read-only, runs headless)."""
    return await _run("library_parts", _clean({"library": library, "name": name, "limit": limit}))


# ---- write -------------------------------------------------------------------------


@mcp.tool()
async def pulsonix_set_component_attributes(
    changes: Annotated[dict[str, dict[str, str | None]], Field(description="{'R1': {'Value': '10k', 'Old': null}} - null deletes the attribute")],
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Add/change/delete component attributes. Returns before/after values. Headless without save/save_as = dry run."""
    return await _run("set_attributes", {"changes": changes}, design=design, mode=mode, save=save, save_as=save_as)


@mcp.tool()
async def pulsonix_set_net_attributes(
    changes: Annotated[dict[str, dict[str, str | None]], Field(description="{'GND': {'Comment': 'x'}} - null deletes")],
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Add/change/delete attributes on nets."""
    return await _run("set_net_attributes", {"changes": changes}, design=design, mode=mode, save=save, save_as=save_as)


@mcp.tool()
async def pulsonix_rename_components(
    renames: Annotated[dict[str, str], Field(description="{'R1': 'R101', ...}")],
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Rename component references. Fails if a target name already exists."""
    return await _run("rename_components", {"renames": renames}, design=design, mode=mode, save=save, save_as=save_as)


@mcp.tool()
async def pulsonix_move_component(
    name: str,
    x_mm: float,
    y_mm: float,
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Move a component (its origin) to absolute X/Y in millimetres."""
    return await _run("move_component", {"name": name, "x_mm": x_mm, "y_mm": y_mm},
                      design=design, mode=mode, save=save, save_as=save_as)


@mcp.tool()
async def pulsonix_set_fitted(
    names: list[str],
    fitted: bool,
    variant: Annotated[str | None, Field(description="Variant to switch to first ('' or 'Master' = master design)")] = None,
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Set components fitted / not fitted (optionally in a given variant)."""
    return await _run("set_fitted", _clean({"names": names, "fitted": fitted, "variant": variant}),
                      design=design, mode=mode, save=save, save_as=save_as)


@mcp.tool()
async def pulsonix_add_component(
    part: Annotated[str, Field(description="Part name from the libraries")],
    name: Annotated[str | None, Field(description="Reference to give it; empty = automatic")] = None,
    part_rep: Annotated[str | None, Field(description="Part representation name (usually empty)")] = None,
    x_mm: float | None = None,
    y_mm: float | None = None,
    attributes: dict[str, str] | None = None,
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Add a new component instance of a library part, optionally positioned and with attributes."""
    args = _clean({"part": part, "name": name, "part_rep": part_rep, "x_mm": x_mm, "y_mm": y_mm, "attributes": attributes})
    return await _run("add_component", args, design=design, mode=mode, save=save, save_as=save_as)


@mcp.tool()
async def pulsonix_add_net(
    name: str,
    net_class: str | None = None,
    local: Annotated[bool | None, Field(description="Schematic only: make it a local net")] = None,
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Create a new net."""
    return await _run("add_net", _clean({"name": name, "net_class": net_class, "local": local}),
                      design=design, mode=mode, save=save, save_as=save_as)


@mcp.tool()
async def pulsonix_add_node(
    net: str,
    component: str,
    pin: str,
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Connect a component pin to a net (Net.AddNode). Mainly meaningful for PCB designs."""
    return await _run("add_node", {"net": net, "component": component, "pin": pin},
                      design=design, mode=mode, save=save, save_as=save_as)


@mcp.tool()
async def pulsonix_set_design_properties(
    properties: Annotated[dict[str, str], Field(description="Keys: title, subject, author, keywords, comments, last_saved_by")],
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Set document properties (title, author, ...)."""
    return await _run("set_properties", {"properties": properties}, design=design, mode=mode, save=save, save_as=save_as)


# ---- outputs -----------------------------------------------------------------------


@mcp.tool()
async def pulsonix_write_plots(
    design: DesignPath = None,
    plot_names: Annotated[list[str] | None, Field(description="Existing CAM plot names to run")] = None,
    device: Annotated[str | None, Field(description="Or run all plots of a device: PDF, Gerber, Excellon, Windows")] = None,
    output_path: Annotated[str | None, Field(description="Output folder/file; relative paths are relative to the design folder")] = None,
    mode: Mode = "headless",
) -> dict:
    """Generate outputs from CAM/Plot definitions already stored in the design (see pulsonix_list_cam_plots).
    In headless mode give an absolute output_path, because the design is a temporary copy."""
    args = _clean({"plot_names": plot_names, "device": device, "output_path": output_path})
    return await _run("write_plots", args, design=design, mode=mode)


@mcp.tool()
async def pulsonix_run_report(
    report_name: Annotated[str, Field(description="Name of a user report format defined in Pulsonix")],
    output_path: Annotated[str, Field(description="Absolute output file path")],
    design: DesignPath = None,
    mode: Mode = "headless",
) -> dict:
    """Run a named Pulsonix user report into a file."""
    return await _run("run_report", {"report_name": report_name, "output_path": output_path}, design=design, mode=mode)


@mcp.tool()
async def pulsonix_generate_output(
    kind: Literal["STEP", "ODB", "IPC2581"],
    output_path: str,
    settings_xml: Annotated[str | None, Field(description="Optional path to an XML settings file (see api reference 'document')")] = None,
    design: DesignPath = None,
    mode: Mode = "headless",
) -> dict:
    """PCB only: generate STEP, ODB++ or IPC-2581 output."""
    return await _run("generate", _clean({"kind": kind, "output_path": output_path, "settings_xml": settings_xml}),
                      design=design, mode=mode)


# ---- live-only commands / escape hatch ------------------------------------------------


@mcp.tool()
async def pulsonix_run_command(command: Annotated[str, Field(description="Pulsonix command name as in Run Command")]) -> dict:
    """Live mode: run a named Pulsonix UI command in the open Pulsonix window (may open dialogs)."""
    return await _run("run_command", {"command": command}, mode="live")


@mcp.tool()
async def pulsonix_run_macro(macro: Annotated[str, Field(description="Macro name or path")]) -> dict:
    """Live mode: run a recorded Pulsonix macro in the open Pulsonix window."""
    return await _run("run_macro", {"macro": macro}, mode="live")


@mcp.tool()
async def pulsonix_run_script(
    code: Annotated[str, Field(description=(
        "JScript function BODY. In scope: D (Document or null), Application, args, log(s), each(collection, fn), "
        "attrs(item), mm(dsu), dsu(mm), safe(fn, default), wildcard(p). `return` a JSON-able value "
        "(plain objects/arrays/strings/numbers). Convert COM values with String()/Number(). No JSON object in JScript."
    ))],
    args: dict[str, Any] | None = None,
    design: DesignPath = None,
    save: Save = False,
    save_as: SaveAs = None,
    mode: Mode = "headless",
) -> dict:
    """Run arbitrary JScript against the Pulsonix scripting API (escape hatch for anything the other tools miss).
    Example body: var r=[]; each(D.Components, function(c){ r.push(String(c.Name)); }); return r;"""
    return await _run("run_script", {"code": code, "args": args or {}}, design=design, mode=mode, save=save, save_as=save_as)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
