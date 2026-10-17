"""Robust app directories. Default to %LOCALAPPDATA%\\OpenMontage Studio on Windows (Controlled Folder
Access blocks Documents), fall back there on any permission/creation error."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

WARNINGS: list[str] = []


def local_base() -> Path:
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(la) / "OpenMontage Studio"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "OpenMontage Studio"


def _mk(p: Path) -> Path:
    os.makedirs(p, exist_ok=True)
    probe = p / f".om_write_test_{os.getpid()}"
    probe.write_text("ok"); probe.unlink()
    return p


def ensure_dir(p, name: str | None = None) -> Path:
    """Create p (parents ok). On failure fall back to LOCALAPPDATA/<name>, then temp."""
    p = Path(p)
    try:
        return _mk(p)
    except OSError as e:
        for fb in (local_base() / (name or p.name), Path(tempfile.gettempdir()) / "OpenMontage Studio" / (name or p.name)):
            if fb == p:
                continue
            try:
                _mk(fb)
                msg = (f"Cannot write to {p} ({type(e).__name__}: {e}). Using {fb} instead. "
                       "(Windows 'Controlled folder access' may block Documents; choose another folder in Settings.)")
                if msg not in WARNINGS:
                    WARNINGS.append(msg); print("[paths] " + msg, file=sys.stderr, flush=True)
                return fb
            except OSError:
                continue
        raise


def work_dir() -> Path:
    env = os.environ.get("OM_WORK_DIR")
    return ensure_dir(Path(env), "") if env else ensure_dir(local_base())


def sub(name: str) -> Path:
    try:
        return ensure_dir(work_dir() / name, name)
    except OSError:
        return ensure_dir(local_base() / name, name)


def export_dir() -> Path:
    e = os.environ.get("OM_EXPORT_DIR")
    return ensure_dir(Path(e), "exports") if e else sub("exports")


def ensure_parent(file_path) -> Path:
    """Make sure the parent dir of an output file exists; if not creatable, redirect file into exports."""
    fp = Path(file_path)
    d = ensure_dir(fp.parent, "exports")
    return d / fp.name
