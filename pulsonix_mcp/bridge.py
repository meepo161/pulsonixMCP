"""Bridge between Python and the Pulsonix 10.5 ActiveX scripting host.

Every operation is executed by generating a JScript file (runtime + ops +
a small entry point) and handing it to Pulsonix:

* ``headless`` – starts ``Pulsonix.exe -hidden -scriptfile <file>``; the script
  opens a *temporary copy* of the design, runs the op and quits. The user's
  original file is only replaced on an explicit save (with a backup first).
* ``live`` – sends ``[psx_script_file("<file>")]`` over DDE to the Pulsonix
  window the user already has open and works on its in-memory design.

The script writes its JSON result to a UTF-16 file which is read back here.
Scripts use the ``.jse`` extension because Pulsonix picks the script engine
from the file-extension registry entry, and ``.js`` is frequently re-associated
with editors/browsers (which then have no ScriptEngine key).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import dde

_JS_DIR = Path(__file__).parent / "jscript"
_DEFAULT_EXE_CANDIDATES = [
    r"C:\Program Files (x86)\Pulsonix10.5\Pulsonix.exe",
    r"C:\Program Files\Pulsonix10.5\Pulsonix.exe",
]

# Pulsonix is a single heavy GUI process; never run two scripts at once.
_LOCK = threading.Lock()


class PulsonixError(RuntimeError):
    pass


@dataclass
class Config:
    exe: str
    work_dir: Path
    timeout_s: float = 120.0

    @classmethod
    def from_env(cls) -> "Config":
        exe = os.environ.get("PULSONIX_EXE") or next(
            (p for p in _DEFAULT_EXE_CANDIDATES if os.path.isfile(p)), _DEFAULT_EXE_CANDIDATES[0]
        )
        work = Path(os.environ.get("PULSONIX_MCP_WORKDIR") or Path(tempfile.gettempdir()) / "pulsonix_mcp")
        work.mkdir(parents=True, exist_ok=True)
        timeout = float(os.environ.get("PULSONIX_MCP_TIMEOUT", "120"))
        return cls(exe=exe, work_dir=work, timeout_s=timeout)


_ENTRY = """
(function () {
  var R, D = null, HEADLESS = %(headless)s;
  try {
    if (HEADLESS) {
      if (P.design) { D = Application.OpenDocument(P.design); failIf(!D, 'Cannot open design: ' + P.design); }
    } else {
      D = __liveDoc();
    }
    var res = OPS[P.op](D, P.args || {});
    if (P.save) {
      failIf(!D, 'Nothing to save');
      failIf(!safe(function () { return D.CanSaveDesign(); }, true), 'Design cannot be saved (read-only, password or licence)');
      D.Save(); // return value is unreliable in 10.5; the caller verifies the file instead
    }
    R = { ok: true, result: res, log: __log };
  } catch (e) {
    R = { ok: false, error: String((e && e.message) || e), number: (e && e.number) || null, log: __log };
  }
  try { if (HEADLESS && D) D.Discard(); } catch (e2) {}
  __writeResult(R);
  if (HEADLESS) { try { Application.QuitApp(); } catch (e3) {} }
})();

function __liveDoc() {
  if (P.design) {
    var want = String(P.design).toLowerCase().replace(/\\//g, '\\\\'), found = null;
    each(Application.Documents, function (d) {
      if (String(d.FullPathName).toLowerCase() == want) { found = d; return false; }
    });
    failIf(!found, 'Design is not open in Pulsonix: ' + P.design);
    return found;
  }
  if (!P.needs_design) return safe(function () { return Application.ActiveDocument; }, null);
  var a = Application.ActiveDocument;
  failIf(!a, 'No active design in Pulsonix');
  return a;
}
"""

# ops that do not need a design document
_NO_DESIGN_OPS = {"library_parts", "app_info", "run_command", "run_macro"}


class Bridge:
    def __init__(self, config: Config | None = None) -> None:
        self.cfg = config or Config.from_env()
        self._runtime = (_JS_DIR / "runtime.jse").read_text(encoding="ascii")
        self._ops = (_JS_DIR / "ops.jse").read_text(encoding="ascii")

    # ---- public ----------------------------------------------------------------

    def pulsonix_running(self) -> bool:
        return dde.is_server_available()

    def run(
        self,
        op: str,
        args: dict | None = None,
        design: str | None = None,
        mode: str = "headless",
        save: bool = False,
        save_as: str | None = None,
        timeout_s: float | None = None,
    ) -> dict:
        """Run ``op``; returns {"result": ..., "log": [...], **meta}. Raises PulsonixError."""
        if mode not in ("headless", "live"):
            raise PulsonixError("mode must be 'headless' or 'live'")
        needs_design = op not in _NO_DESIGN_OPS
        timeout = timeout_s or self.cfg.timeout_s
        with _LOCK:
            if mode == "headless":
                return self._run_headless(op, args or {}, design, needs_design, save, save_as, timeout)
            if save_as:
                raise PulsonixError("save_as is only supported in headless mode")
            return self._run_live(op, args or {}, design, needs_design, save, timeout)

    # ---- headless --------------------------------------------------------------

    def _run_headless(self, op, args, design, needs_design, save, save_as, timeout) -> dict:
        if needs_design and not design:
            raise PulsonixError("headless mode needs 'design' (path to .sch/.pcb file)")
        job = self._new_job_dir()
        meta: dict = {"mode": "headless"}
        copy_path = None
        if design:
            src = Path(design).expanduser().resolve()
            if not src.is_file():
                raise PulsonixError(f"Design file not found: {src}")
            copy_path = job / src.name
            shutil.copy2(src, copy_path)
        script = self._build_script(job, op, args, str(copy_path) if copy_path else None, needs_design,
                                    save=bool(save or save_as), headless=True,
                                    original=str(src) if copy_path else None)
        if design:
            meta["design"] = str(src)
        if not os.path.isfile(self.cfg.exe):
            raise PulsonixError(f"Pulsonix.exe not found at {self.cfg.exe}; set PULSONIX_EXE")
        copy_mtime = copy_path.stat().st_mtime_ns if copy_path else None
        t0 = time.monotonic()
        proc = subprocess.Popen([self.cfg.exe, "-hidden", "-scriptfile", str(script)])
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise PulsonixError(
                f"Pulsonix did not finish within {timeout:.0f}s (hidden instance killed). A modal dialog "
                "may have blocked it, e.g. licence or 'check for updates' prompt; start Pulsonix once "
                "normally and dismiss it."
            )
        meta["elapsed_s"] = round(time.monotonic() - t0, 2)
        out = self._read_result(job / "result.json", wait_s=2)
        if (save or save_as) and copy_path is not None:
            if copy_path.stat().st_mtime_ns == copy_mtime:
                self._cleanup(job)
                raise PulsonixError("Pulsonix did not write the design file; nothing was saved")
            meta.update(self._commit_saved_copy(copy_path, Path(design), save_as))
        self._cleanup(job)
        return {**out, **meta}

    def _commit_saved_copy(self, copy_path: Path, original: Path, save_as: str | None) -> dict:
        original = original.expanduser().resolve()
        if save_as:
            target = Path(save_as).expanduser().resolve()
            if target == original:
                save_as = None
        if not save_as:
            target = original
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = original.with_name(f"{original.stem}.bak-{stamp}{original.suffix}")
            shutil.copy2(original, backup)
            shutil.copy2(copy_path, target)
            return {"saved_to": str(target), "backup": str(backup)}
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copy_path, target)
        return {"saved_to": str(target)}

    # ---- live ------------------------------------------------------------------

    def _run_live(self, op, args, design, needs_design, save, timeout) -> dict:
        job = self._new_job_dir()
        design_abs = str(Path(design).expanduser().resolve()) if design else None
        script = self._build_script(job, op, args, design_abs, needs_design, save=save, headless=False)
        cmd = f'[psx_script_file("{script.as_posix()}")]'
        t0 = time.monotonic()
        try:
            acked = dde.execute(cmd, timeout_ms=int(timeout * 1000))
        except dde.DdeError as e:
            raise PulsonixError(f"{e}. Open Pulsonix (live mode) or use mode='headless'.")
        remaining = max(2.0, timeout - (time.monotonic() - t0))
        out = self._read_result(job / "result.json", wait_s=remaining)
        out["mode"] = "live"
        out["dde_acknowledged"] = acked
        out["elapsed_s"] = round(time.monotonic() - t0, 2)
        self._cleanup(job)
        return out

    # ---- internals -------------------------------------------------------------

    def _new_job_dir(self) -> Path:
        job = self.cfg.work_dir / f"job-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        job.mkdir(parents=True)
        return job

    def _build_script(self, job: Path, op: str, args: dict, design: str | None, needs_design: bool,
                      save: bool, headless: bool, original: str | None = None) -> Path:
        params = {
            "op": op,
            "args": args,
            "design": design.replace("\\", "/") if design else None,
            "original": original or design,
            "needs_design": needs_design,
            "save": save,
            "out": str(job / "result.json").replace("\\", "/"),
        }
        header = "// Generated by pulsonix-mcp\nvar P = " + json.dumps(params, ensure_ascii=True) + ";\n"
        body = header + self._runtime + "\n" + self._ops + "\n" + _ENTRY % {"headless": "true" if headless else "false"}
        path = job / "job.jse"
        path.write_text(body, encoding="ascii")
        return path

    @staticmethod
    def _read_result(path: Path, wait_s: float) -> dict:
        deadline = time.monotonic() + wait_s
        while True:
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-16"))
                    break
                except (UnicodeError, json.JSONDecodeError, PermissionError):
                    pass  # still being written
            if time.monotonic() > deadline:
                raise PulsonixError(
                    "No result from Pulsonix script. Possible causes: script engine for .jse missing, "
                    "Pulsonix busy with a modal dialog, or the script crashed before writing output."
                )
            time.sleep(0.1)
        if not data.get("ok"):
            log = data.get("log") or []
            raise PulsonixError(f"Pulsonix script error: {data.get('error')}" + (f" | log: {log}" if log else ""))
        return {"result": data.get("result"), "log": data.get("log") or []}

    @staticmethod
    def _cleanup(job: Path) -> None:
        if os.environ.get("PULSONIX_MCP_KEEP_JOBS"):
            return
        shutil.rmtree(job, ignore_errors=True)
