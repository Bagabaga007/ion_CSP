"""Render a Final POSCAR with VESTA on a private X display.

The images are presentation artifacts.  The POSCAR remains the authoritative
scientific result, and each image is linked to its exact input hash.
"""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from ase.io import read


AXIS_BUTTONS = (("a", 31), ("b", 69), ("c", 107))
SCREEN_SIZE = (1500, 1100)
GRAPHICS_BOX = (327, 117, 1488, 884)


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=".vesta-", suffix=".json",
        dir=path.parent, delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_snapshot_status(
    path: Path,
    poscar: Path | None,
    *,
    status: str,
    manifest: Path | None = None,
    error: str | None = None,
) -> None:
    """Record success or a visible visualization failure atomically."""
    source = Path(poscar) if poscar is not None else None
    _atomic_json(path, {
        "status": status,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stage": "final",
        "source_poscar": str(source.resolve()) if source is not None else None,
        "source_sha256": _digest(source) if source is not None and source.is_file() else None,
        "manifest": str(manifest) if manifest is not None else None,
        "error": error,
    })


def _resolve_executable(value: str | None) -> Path:
    choice = value or os.environ.get("ION_CSP_VESTA_BIN")
    if choice:
        executable = Path(choice).expanduser().resolve()
    else:
        found = shutil.which("VESTA-gui") or shutil.which("VESTA")
        workspace_install = (
            Path(__file__).resolve().parents[3]
            / ".local" / "opt" / "vesta-3.5.8" / "VESTA-gui"
        )
        if found:
            executable = Path(found).resolve()
        elif workspace_install.is_file():
            executable = workspace_install.resolve()
        else:
            raise FileNotFoundError(
                "VESTA was not found; set vasp_processing.vesta_executable "
                "or ION_CSP_VESTA_BIN to the VESTA GUI executable"
            )
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise FileNotFoundError(f"VESTA executable is unavailable: {executable}")
    return executable


def _ensure_vesta_user_config(vesta: Path) -> None:
    """Initialize VESTA's required per-user INI only when it is absent."""
    target = Path.home() / ".VESTA" / "VESTA.ini"
    if target.is_file():
        return
    default = vesta.parent / "VESTA_def.ini"
    if not default.is_file():
        default = vesta.parent / "VESTA.ini"
    if not default.is_file():
        raise FileNotFoundError(
            f"VESTA user configuration is missing and no bundled default exists: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(default.read_bytes())
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _cached_manifest(output_dir: Path, source_hash: str) -> Path | None:
    for manifest in sorted(output_dir.glob(f"{source_hash[:12]}-*/manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("source_sha256") != source_hash:
                continue
            views = data["views"]
            if set(views) != {"a", "b", "c"}:
                continue
            if all(
                (manifest.parent / entry["file"]).is_file()
                and _digest(manifest.parent / entry["file"]) == entry["sha256"]
                for entry in views.values()
            ):
                return manifest
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def render_vesta_views(
    poscar: Path,
    output_dir: Path,
    *,
    executable: str | None = None,
    timeout_seconds: int = 180,
) -> Path:
    """Return a manifest for three VESTA views of one exact Final POSCAR."""
    source = Path(poscar).resolve(strict=True)
    atoms = read(source, format="vasp")
    if len(atoms) == 0 or atoms.cell.volume <= 0:
        raise ValueError(f"Invalid periodic structure in {source}")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_hash = _digest(source)
    cached = _cached_manifest(output_dir, source_hash)
    if cached is not None:
        return cached
    vesta = _resolve_executable(executable)
    _ensure_vesta_user_config(vesta)
    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        raise FileNotFoundError("xvfb-run is required for headless VESTA snapshots")

    with tempfile.TemporaryDirectory(prefix=".vesta-stage-", dir=output_dir) as stage_name:
        stage = Path(stage_name)
        staged_poscar = stage / "POSCAR"
        shutil.copyfile(source, staged_poscar)
        if _digest(staged_poscar) != source_hash:
            raise RuntimeError("Final POSCAR changed while preparing VESTA input")
        command = [
            xvfb, "-a", "-s", f"-screen 0 {SCREEN_SIZE[0]}x{SCREEN_SIZE[1]}x24",
            sys.executable, "-m", "ion_CSP.vesta_snapshots", "--worker",
            str(staged_poscar), str(stage), str(vesta),
        ]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise TimeoutError(f"VESTA snapshot rendering exceeded {timeout_seconds}s") from exc
        if process.returncode != 0:
            vesta_log = stage / "vesta.log"
            detail = vesta_log.read_text(encoding="utf-8", errors="replace")[-2000:] if vesta_log.is_file() else ""
            raise RuntimeError(
                f"VESTA rendering failed: {output[-2000:].strip()}\n"
                f"VESTA log: {detail.strip()}"
            )
        worker = json.loads((stage / "views.json").read_text(encoding="utf-8"))
        if set(worker) != {"a", "b", "c"}:
            raise RuntimeError("VESTA did not produce all three crystal views")
        for entry in worker.values():
            image = stage / entry["file"]
            if not image.is_file() or image.stat().st_size == 0:
                raise RuntimeError(f"VESTA image is missing or empty: {image}")
            entry["sha256"] = _digest(image)
        if _digest(source) != source_hash:
            raise RuntimeError("Final POSCAR changed during VESTA rendering")
        destination = output_dir / (
            f"{source_hash[:12]}-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
        )
        os.replace(stage, destination)

    manifest = destination / "manifest.json"
    _atomic_json(manifest, {
        "stage": "final",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_poscar": str(source),
        "source_sha256": source_hash,
        "atom_count": len(atoms),
        "formula": atoms.get_chemical_formula(),
        "vesta_executable": str(vesta),
        "views": worker,
    })
    return manifest


class _XDisplay:
    def __init__(self) -> None:
        self.x = ctypes.CDLL("libX11.so.6")
        self.xt = ctypes.CDLL("libXtst.so.6")
        self.x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("VESTA virtual display could not be opened")
        self.x.XFlush.argtypes = [ctypes.c_void_p]
        self.x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.x.XStringToKeysym.restype = ctypes.c_ulong
        self.x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x.XKeysymToKeycode.restype = ctypes.c_uint
        self.x.XMoveResizeWindow.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint, ctypes.c_uint,
        ]
        self.xt.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong,
        ]
        self.xt.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_ulong,
        ]

    def click(self, x: int, y: int) -> None:
        self.xt.XTestFakeMotionEvent(self.display, -1, x, y, 0)
        self.xt.XTestFakeButtonEvent(self.display, 1, 1, 0)
        self.xt.XTestFakeButtonEvent(self.display, 1, 0, 0)
        self.x.XFlush(self.display)

    def resize(self, window: str) -> None:
        self.x.XMoveResizeWindow(self.display, int(window, 0), 10, 10, 1480, 1050)
        self.x.XFlush(self.display)


def _render_worker(poscar: Path, output_dir: Path, vesta: Path) -> None:
    from PIL import Image, ImageChops

    display_name = os.environ["DISPLAY"]
    display = _XDisplay()
    env = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1")
    library_path = os.environ.get("ION_CSP_VESTA_LIBRARY_PATH")
    if library_path:
        env["LD_LIBRARY_PATH"] = library_path
    else:
        env["LD_LIBRARY_PATH"] = (
            f"{vesta.parent}:{vesta.parent / 'compat'}:"
            f"{env.get('LD_LIBRARY_PATH', '')}"
        )
    with (output_dir / "vesta.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(vesta), str(poscar)], cwd=vesta.parent, env=env,
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            window = None
            for _ in range(80):
                if process.poll() is not None:
                    raise RuntimeError("VESTA exited before opening the POSCAR")
                tree = subprocess.check_output(
                    ["xwininfo", "-display", display_name, "-root", "-tree"], text=True,
                )
                match = re.search(r'(0x[0-9a-f]+) "POSCAR - VESTA"', tree)
                if match:
                    window = match.group(1)
                    break
                time.sleep(0.25)
            if window is None:
                raise RuntimeError("VESTA did not open a POSCAR structure window")
            display.resize(window)
            time.sleep(0.8)
            views = {}
            for axis, button_x in AXIS_BUTTONS:
                display.click(button_x, 60)
                time.sleep(0.4)
                for zoom in range(12):
                    raw = output_dir / f"{axis}_screen.png"
                    subprocess.run(
                        ["import", "-display", display_name, "-window", "root", str(raw)],
                        check=True, timeout=15,
                    )
                    screen = Image.open(raw).convert("RGB")
                    graphics = screen.crop(GRAPHICS_BOX)
                    if graphics.getpixel((0, 0)) != (255, 255, 255):
                        raise RuntimeError("VESTA graphics area is not ready")
                    difference = ImageChops.difference(
                        graphics, Image.new("RGB", graphics.size, "white")
                    ).convert("L")
                    box = difference.point(lambda value: 255 if value > 12 else 0).getbbox()
                    if box and box[0] > 20 and box[1] > 20 and box[2] < graphics.width - 20 and box[3] < graphics.height - 20:
                        break
                    display.click(893, 58)
                    time.sleep(0.25)
                else:
                    raise RuntimeError(f"VESTA {axis}-axis view could not fit the viewport")
                crop_box = (
                    max(0, box[0] - 18), max(0, box[1] - 18),
                    min(graphics.width, box[2] + 18),
                    min(graphics.height, box[3] + 18),
                )
                image = graphics.crop(crop_box)
                if image.width < 200 or image.height < 200:
                    raise RuntimeError(f"VESTA {axis}-axis view is unexpectedly small")
                target = output_dir / f"{axis}.png"
                image.save(target, dpi=(180, 180))
                views[axis] = {
                    "file": target.name,
                    "raw_screen": raw.name,
                    "crop_in_graphics": list(crop_box),
                    "pixels": [image.width, image.height],
                    "zoom_out_steps": zoom,
                }
            _atomic_json(output_dir / "views.json", views)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("poscar", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("vesta", nargs="?", type=Path)
    arguments = parser.parse_args()
    if arguments.worker:
        if arguments.vesta is None:
            parser.error("--worker requires a VESTA executable")
        _render_worker(arguments.poscar, arguments.output_dir, arguments.vesta)
    else:
        print(render_vesta_views(arguments.poscar, arguments.output_dir, executable=str(arguments.vesta) if arguments.vesta else None))


if __name__ == "__main__":
    main()
