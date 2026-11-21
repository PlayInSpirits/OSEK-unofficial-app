from importlib.util import find_spec
from pathlib import Path
from shutil import copy2, copytree
import subprocess
import sys
from zipfile import ZipFile

from py2exe import freeze


def main():
    sys.path.insert(0, str(Path.cwd()))
    bundle = Path(sys.argv[1]).resolve()
    bundle.mkdir(parents=True, exist_ok=True)
    internal = bundle / "_internal"
    freeze(
        windows=[{"script": "tools/py2exe_entry.py", "dest_base": "OSEK-App"}],
        options={"py2exe": {
            "dist_dir": str(internal),
            "packages": ["webview", "pythonnet", "clr_loader", "pypdfium2", "pypdfium2_raw", "keyring", "win32ctypes", "reportlab"],
            "includes": ["pystray._win32", "keyring.backends.Windows", "win32cred", "pywintypes"],
            "excludes": ["tkinter", "PyInstaller"],
        }},
    )
    packages = internal / "packages"
    with ZipFile(internal / "library.zip") as archive:
        names = ("webview/", "pythonnet/", "pypdfium2/", "pypdfium2_raw/", "clr_loader/")
        for member in archive.namelist():
            if member.startswith(names):
                archive.extract(member, packages)
    for package, folder in (("webview", "lib"), ("pythonnet", "runtime"), ("clr_loader", "ffi/dlls")):
        source = Path(find_spec(package).origin).parent / folder
        copytree(source, packages / package / folder, dirs_exist_ok=True)
    pdfium = Path(find_spec("pypdfium2_raw").origin).parent
    for filename in ("pdfium.dll", "version.json"):
        copy2(pdfium / filename, packages / "pypdfium2_raw" / filename)
    copy2(Path(find_spec("pypdfium2").origin).with_name("version.json"), packages / "pypdfium2" / "version.json")
    copy2(find_spec("clr").origin, packages / "clr.py")
    copytree("web", internal / "web", dirs_exist_ok=True)
    for relative in ("assets/icon.png", "sdo_reborn/install_update.ps1"):
        target = internal / relative
        target.parent.mkdir(exist_ok=True)
        copy2(relative, target)

    import os
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(csc), "/nologo", "/target:winexe", "/platform:x64", "/codepage:65001",
                    f"/out:{bundle / 'ОСЭК-Remastered.exe'}", f"/win32icon:{Path('assets/icon.ico').resolve()}",
                    str(Path("tools/windows_launcher.cs").resolve())], check=True)


if __name__ == "__main__":
    main()
