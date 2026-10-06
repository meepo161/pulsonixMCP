"""Extract the scripting API reference from the locally installed Pulsonix.

The reference (Scripting.chm) is copyrighted by WestDev Ltd, so it is not
shipped with this package: it is decompiled from the user's own installation
into a cache directory on first use.
"""

from __future__ import annotations

import ctypes
import html
import re
import shutil
import subprocess
from pathlib import Path


class ApiDocsError(RuntimeError):
    pass


def _short_path(p: Path) -> str:
    # hh.exe -decompile cannot handle paths with spaces; use the 8.3 short form.
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, len(buf))
    return buf.value if n else str(p)


def _html_to_text(src: str) -> str:
    t = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", src)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(p|tr|h\d|li|div|pre)>", "\n", t, flags=re.I)
    t = re.sub(r"</td>", "\t", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + "\n"


def ensure_docs(pulsonix_exe: str, cache_dir: Path) -> Path:
    """Return a directory of <topic>.txt files, building it on first call."""
    if cache_dir.is_dir() and any(cache_dir.glob("*.txt")):
        return cache_dir
    chm = Path(pulsonix_exe).with_name("Scripting.chm")
    if not chm.is_file():
        raise ApiDocsError(f"Scripting.chm not found next to {pulsonix_exe}")
    work = cache_dir.parent / "chm_extract"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    shutil.copy2(chm, work / "s.chm")
    out = work / "out"
    out.mkdir()
    subprocess.run(["hh.exe", "-decompile", _short_path(out), _short_path(work / "s.chm")],
                   check=False, timeout=60)
    pages = list(out.rglob("*.htm"))
    if not pages:
        raise ApiDocsError("Could not decompile Scripting.chm with hh.exe")
    cache_dir.mkdir(parents=True, exist_ok=True)
    for page in pages:
        text = _html_to_text(page.read_text(encoding="cp1252", errors="replace"))
        (cache_dir / f"{page.stem.lower()}.txt").write_text(text, encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return cache_dir
