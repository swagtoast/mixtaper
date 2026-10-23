"""Builds the stand-alone app for the current platform (run on each OS, e.g. in CI).

  macOS    dist/Mixtaper-<ver>-macOS-<arch>.zip   Mixtaper.app + Mixtaper.command
  Windows  dist/Mixtaper-<ver>-Windows.zip        Mixtaper.exe
  Linux    dist/Mixtaper-<ver>-Linux.tar.gz       mixtaper

ffmpeg is bundled (taken from imageio-ffmpeg unless MIXTAPER_FFMPEG points elsewhere).
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

from icon import make_iconset, render, write_png  # noqa: E402

from mixtaper import __version__  # noqa: E402

SYSTEM = platform.system()
EXE = ".exe" if SYSTEM == "Windows" else ""

LAUNCHER = """#!/bin/bash
# Opens mixtaper in a Terminal window.
HERE="$(cd "$(dirname "$0")/../Resources/mixtaper" && pwd)"
exec open -a Terminal "$HERE/mixtaper"
"""

COMMAND = """#!/bin/bash
cd "$(dirname "$0")" || exit 1
exec "./Mixtaper.app/Contents/Resources/mixtaper/mixtaper" "$@"
"""

README_MAC = """mixtaper {ver}

Doppio clic su Mixtaper.app (oppure su Mixtaper.command).
Se macOS blocca l'app perché scaricata da internet: tasto destro > Apri,
oppure nel Terminale:  xattr -dr com.apple.quarantine Mixtaper.app

Double-click Mixtaper.app (or Mixtaper.command).
If macOS blocks it because it was downloaded: right-click > Open,
or in Terminal:  xattr -dr com.apple.quarantine Mixtaper.app
"""


def ffmpeg_binary() -> Path:
    env = os.environ.get("MIXTAPER_FFMPEG")
    if env:
        return Path(env)
    import imageio_ffmpeg

    return Path(imageio_ffmpeg.get_ffmpeg_exe())


def run(cmd: list[str]) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def make_icon() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    png = BUILD / "icon.png"
    write_png(png, render(512))
    if SYSTEM == "Darwin":
        iconset = BUILD / "AppIcon.iconset"
        make_iconset(iconset)
        icns = BUILD / "AppIcon.icns"
        run(["iconutil", "-c", "icns", iconset, "-o", icns])
        return icns
    return png   # PyInstaller converts it to .ico when Pillow is installed


def pyinstaller(icon: Path, onefile: bool, name: str = "mixtaper") -> None:
    staged = BUILD / "bin" / f"ffmpeg{EXE}"
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ffmpeg_binary(), staged)
    staged.chmod(staged.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    sep = os.pathsep
    run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--console",
        "--onefile" if onefile else "--onedir",
        "--name", name,
        "--distpath", DIST, "--workpath", BUILD / "pyi", "--specpath", BUILD,
        "--icon", icon,
        "--collect-submodules", "textual", "--collect-data", "textual",
        "--collect-submodules", "rich",
        "--add-data", f"{ROOT / 'mixtaper' / 'tui' / 'app.tcss'}{sep}mixtaper/tui",
        "--add-binary", f"{staged}{sep}bin",
        "--exclude-module", "imageio_ffmpeg",
        "--exclude-module", "tkinter",
        ROOT / "packaging" / "entry.py",
    ])


def build_macos(icon: Path) -> Path:
    pyinstaller(icon, onefile=False)
    app = DIST / "Mixtaper.app"
    shutil.rmtree(app, ignore_errors=True)
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    shutil.move(str(DIST / "mixtaper"), contents / "Resources" / "mixtaper")
    shutil.copy2(icon, contents / "Resources" / "AppIcon.icns")
    launcher = contents / "MacOS" / "Mixtaper"
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleName": "Mixtaper",
            "CFBundleDisplayName": "Mixtaper",
            "CFBundleIdentifier": "com.swagtoast.mixtaper",
            "CFBundleExecutable": "Mixtaper",
            "CFBundleIconFile": "AppIcon",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        }, f)
    run(["codesign", "--force", "--deep", "--sign", "-", app])
    command = DIST / "Mixtaper.command"
    command.write_text(COMMAND)
    command.chmod(0o755)
    (DIST / "LEGGIMI.txt").write_text(README_MAC.format(ver=__version__))
    out = DIST / f"Mixtaper-{__version__}-macOS-{platform.machine()}.zip"
    # ditto keeps the bundle's symlinks, permissions and signature intact
    staging = BUILD / "zipdir" / "Mixtaper"
    shutil.rmtree(staging.parent, ignore_errors=True)
    staging.mkdir(parents=True)
    run(["ditto", app, staging / "Mixtaper.app"])
    shutil.copy2(command, staging / "Mixtaper.command")
    shutil.copy2(DIST / "LEGGIMI.txt", staging / "LEGGIMI.txt")
    out.unlink(missing_ok=True)
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", staging, out])
    return out


def build_windows(icon: Path) -> Path:
    pyinstaller(icon, onefile=True, name="Mixtaper")   # case-insensitive FS: name it right away
    exe = DIST / "Mixtaper.exe"
    out = DIST / f"Mixtaper-{__version__}-Windows.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, "Mixtaper/Mixtaper.exe")
    return out


def build_linux(icon: Path) -> Path:
    pyinstaller(icon, onefile=True)
    out = DIST / f"Mixtaper-{__version__}-Linux.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(DIST / "mixtaper", "mixtaper/mixtaper")
    return out


def main() -> None:
    DIST.mkdir(exist_ok=True)
    icon = make_icon()
    out = {"Darwin": build_macos, "Windows": build_windows}.get(SYSTEM, build_linux)(icon)
    print(f"\nbuilt {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
