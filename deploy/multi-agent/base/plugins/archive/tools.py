"""Bounded archive tools — list / extract / create, on container-local paths.

Runs parent-side. Read/extract go through `lsar`/`unar` (cover zip, rar, RAR5,
7z, tar, gz, bz2, xz, …); create uses Python stdlib (zip / tar.*). A clean tool
surface instead of ad-hoc code_execution scripts.
"""
from __future__ import annotations

import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

from tools.registry import tool_error, tool_result

_MAX_ENTRIES = 1000


def _is_file(p: str) -> bool:
    return bool(p) and os.path.isfile(p)


ARCHIVE_LIST = {
    "name": "archive_list",
    "description": "List the contents of an archive (zip/rar/7z/tar/gz/…) without extracting.",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the archive file."}},
        "required": ["path"],
    },
}

ARCHIVE_EXTRACT = {
    "name": "archive_extract",
    "description": "Extract an archive (zip/rar/RAR5/7z/tar/gz/bz2/xz/…) into a directory. Returns the extracted file list.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the archive file."},
            "dest": {"type": "string", "description": "Output directory (default: '<archive>_extracted')."},
        },
        "required": ["path"],
    },
}

ARCHIVE_CREATE = {
    "name": "archive_create",
    "description": "Create an archive from files/dirs. Format from out_path extension: .zip / .tar.gz / .tgz / .tar.bz2 / .tar.xz / .tar.",
    "parameters": {
        "type": "object",
        "properties": {
            "out_path": {"type": "string", "description": "Output archive path (extension picks the format)."},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "Files/dirs to include."},
        },
        "required": ["out_path", "paths"],
    },
}


def handle_archive_list(args: dict, **kw) -> str:
    path = str(args.get("path") or "").strip()
    if not _is_file(path):
        return tool_error(
            f"Archive not found: '{path}'. Pass an ABSOLUTE path to an existing archive file "
            f"(e.g. /opt/data/cache/documents/foo.zip). Files sent in chat are cached under "
            f"/opt/data/cache/documents/."
        )
    try:
        r = subprocess.run(["lsar", path], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return tool_error("lsar/unar not installed in image")
    except subprocess.TimeoutExpired:
        return tool_error("lsar timed out")
    if r.returncode != 0:
        return tool_error(f"lsar failed: {(r.stderr or r.stdout)[:500]}")
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    entries = lines[1:] if lines else []      # first line is the archive name
    return tool_result({"archive": path, "count": len(entries), "entries": entries[:_MAX_ENTRIES]})


def handle_archive_extract(args: dict, **kw) -> str:
    path = str(args.get("path") or "").strip()
    dest = str(args.get("dest") or "").strip()
    if not _is_file(path):
        return tool_error(
            f"Archive not found: '{path}'. Pass an ABSOLUTE path to an existing archive file "
            f"(e.g. /opt/data/cache/documents/foo.zip). Files sent in chat are cached under "
            f"/opt/data/cache/documents/."
        )
    if not dest:
        dest = str(Path(path).with_suffix("")) + "_extracted"
    os.makedirs(dest, exist_ok=True)
    try:
        r = subprocess.run(["unar", "-f", "-o", dest, path],
                           capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return tool_error("unar not installed in image")
    except subprocess.TimeoutExpired:
        return tool_error("unar timed out")
    if r.returncode != 0:
        return tool_error(f"unar failed: {(r.stderr or r.stdout)[:500]}")
    files = [
        os.path.relpath(os.path.join(root, f), dest)
        for root, _, fs in os.walk(dest) for f in fs
    ]
    return tool_result({"archive": path, "dest": dest, "file_count": len(files), "files": sorted(files)[:_MAX_ENTRIES]})


def handle_archive_create(args: dict, **kw) -> str:
    out = str(args.get("out_path") or "").strip()
    raw = args.get("paths") or []
    if isinstance(raw, str):
        raw = [raw]
    paths = [p for p in (str(x).strip() for x in raw) if p]
    if not out or not paths:
        return tool_error(
            "archive_create needs 'out_path' (with a format extension) and 'paths' (list of files/dirs). "
            "Example: archive_create(out_path='/opt/data/bundle.tar.gz', paths=['/opt/data/logs', '/opt/data/report.md'])."
        )
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        return tool_error(
            f"These paths don't exist: {missing}. Pass absolute paths to existing files/dirs "
            f"(check with archive_list or ssh_list / file tools first)."
        )
    low = out.lower()
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for p in paths:
                    pp = Path(p)
                    if pp.is_dir():
                        for f in pp.rglob("*"):
                            if f.is_file():
                                z.write(f, f.relative_to(pp.parent))
                    else:
                        z.write(p, pp.name)
        elif low.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar")):
            mode = ("w:gz" if low.endswith((".tar.gz", ".tgz"))
                    else "w:bz2" if low.endswith(".tar.bz2")
                    else "w:xz" if low.endswith(".tar.xz") else "w")
            with tarfile.open(out, mode) as t:
                for p in paths:
                    t.add(p, arcname=Path(p).name)
        else:
            return tool_error("out_path must end in .zip / .tar.gz / .tgz / .tar.bz2 / .tar.xz / .tar")
    except Exception as e:  # noqa: BLE001
        return tool_error(f"archive create failed: {e}")
    return tool_result({"created": out, "size_bytes": os.path.getsize(out), "entries": len(paths)})
