#!/usr/bin/env python3
"""Bundle only Runtime code/dependencies, never a real config, state or CLI home."""

import json
import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
ROOT = DESKTOP.parents[1]


def main():
    import PyInstaller  # Fail before creating output if the build dependency is absent.
    import PIL

    with tempfile.TemporaryDirectory(prefix="xiaoyu-runtime-build-") as folder:
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--name", "xiaoyu-runtime", "--paths", str(ROOT / "runtime"),
            "--collect-submodules", "xiaoyu_runtime", "--collect-submodules", "PIL",
            "--distpath", str(DESKTOP / "runtime-dist"), "--workpath", folder + "/work",
            "--specpath", folder, str(DESKTOP / "tools/runtime_entry.py"),
        ], check=True)
    sys.path.insert(0, str(ROOT / "runtime"))
    from xiaoyu_runtime import __version__
    shutil.copytree(ROOT / "runtime/third_party",
                    DESKTOP / "runtime-dist/xiaoyu-runtime/third_party", dirs_exist_ok=True)
    shutil.copyfile(ROOT / "runtime/persona.txt", DESKTOP / "runtime-dist/persona.txt")
    manifest = {"platform": sys.platform, "architecture": platform.machine(),
                "runtime_version": __version__, "python": platform.python_version(),
                "pyinstaller": PyInstaller.__version__, "pillow": PIL.__version__}
    digest = hashlib.sha256()
    package = ROOT / "runtime/xiaoyu_runtime"
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    manifest["source_sha256"] = digest.hexdigest()
    manifest["persona_sha256"] = hashlib.sha256((ROOT / "runtime/persona.txt").read_bytes()).hexdigest()
    (DESKTOP / "runtime-dist/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
