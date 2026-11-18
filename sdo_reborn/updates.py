"""Discover and stage verified Windows updates from the project's GitHub releases."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from zipfile import ZipFile

import requests

from .diagnostic_log import LOG_DIR, logger


REPO = "PlayInSpirits/OSEK-unofficial-app"
RELEASE_INTERVAL = 15 * 60
RELEASE_TAG = re.compile(r"OSEK-v0\.([0-9]{1,9})")
DIGEST = re.compile(r"sha256:([a-f0-9]{64})")
BUNDLE_NAME = "ОСЭК-Remastered"
MAX_ARCHIVE = 200 * 1024 * 1024
MAX_BUNDLE = 1024 * 1024 * 1024
UPDATE_FAILURE = LOG_DIR / "update-error.txt"


class UpdateError(Exception):
    """A failure suitable for the update interface."""


def check_release(installed):
    try:
        with requests.get(
            f"https://api.github.com/repos/{REPO}/releases",
            params={"per_page": 100},
            headers={"Accept": "application/vnd.github+json"},
            timeout=(4, 8),
        ) as response:
            response.raise_for_status()
            releases = response.json()
    except (requests.RequestException, ValueError) as error:
        logger.warning("release_check_failed type=%s", type(error).__name__)
        return {"ok": False}
    if not isinstance(releases, list):
        return {"ok": False}

    available = installed
    result = {"ok": True, "version": None}
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        match = RELEASE_TAG.fullmatch(release.get("tag_name") or "")
        if not match or int(match[1]) <= available:
            continue
        version = int(match[1])
        archive = next((
            asset for asset in release.get("assets", [])
            if asset.get("name") in {"OSEK-Remastered-Windows.zip", f"OSEK-Remastered-v0.{version}.zip"}
            and asset.get("state") == "uploaded"
        ), None)
        if archive is None:
            continue
        tag = match[0]
        digest = DIGEST.fullmatch(archive.get("digest") or "")
        size = archive.get("size")
        available = version
        result = {
            "ok": True, "version": version,
            "name": release.get("name") or tag,
            "url": f"https://github.com/{REPO}/releases/tag/{tag}",
            "asset": f"https://github.com/{REPO}/releases/download/{tag}/{archive['name']}",
            "sha256": digest[1] if digest else "",
            "size": size if isinstance(size, int) else 0,
        }
    return result


def download_bundle(release, archive: Path, on_progress):
    size = release["size"]
    if not release["sha256"] or not 0 < size <= MAX_ARCHIVE:
        raise UpdateError("GitHub не предоставил корректную контрольную сумму или размер архива.")
    digest = hashlib.sha256()
    received = 0
    with requests.get(release["asset"], stream=True, timeout=(7, 25)) as response:
        response.raise_for_status()
        with archive.open("xb") as output:
            for chunk in response.iter_content(128 * 1024):
                received += len(chunk)
                if received > size:
                    raise UpdateError("Размер архива отличается от указанного GitHub.")
                output.write(chunk)
                digest.update(chunk)
                on_progress(received, size)
    if received != size or digest.hexdigest() != release["sha256"]:
        raise UpdateError("Контрольная сумма скачанного архива не совпала.")


def unpack_bundle(archive: Path, stage: Path, version: int):
    with ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > 10_000 or sum(entry.file_size for entry in members) > MAX_BUNDLE:
            raise UpdateError("Архив обновления слишком большой.")
        seen = set()
        for entry in members:
            name = entry.orig_filename
            parts = name.rstrip("/").split("/")
            if (not parts or parts[0] != BUNDLE_NAME
                or any(not part or part in {".", ".."} or part.endswith((".", " "))
                       or any(char in part for char in '\\:*?"<>|\x00') for part in parts)
                or stat.S_ISLNK(entry.external_attr >> 16)):
                raise UpdateError("Архив содержит недопустимый путь.")
            relative = PurePosixPath(*parts)
            normalized = str(relative).casefold()
            if normalized in seen:
                raise UpdateError("В архиве повторяется имя файла.")
            seen.add(normalized)
            target = stage.joinpath(*relative.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    bundle = stage / BUNDLE_NAME
    executable = bundle / f"{BUNDLE_NAME}.exe"
    version_file = bundle / "_internal" / "web" / "version.txt"
    if (not executable.is_file() or not version_file.is_file()
        or version_file.read_text(encoding="ascii").strip() != str(version)
        or not (version_file.parent / "index.html").is_file()):
        raise UpdateError("Архив не содержит полноценную сборку заявленной версии.")
    with executable.open("rb") as file:
        if file.read(2) != b"MZ":
            raise UpdateError("В архиве отсутствует исполняемый файл Windows.")


def start_installer(executable: Path, script: Path, stage: Path):
    program = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not program.is_file():
        raise UpdateError("Windows PowerShell для установки обновления не найден.")
    UPDATE_FAILURE.unlink(missing_ok=True)
    env = os.environ.copy()
    env.update({
        "OSEK_UPDATE_PID": str(os.getpid()), "OSEK_UPDATE_EXE": str(executable),
        "OSEK_UPDATE_STAGE": str(stage), "OSEK_UPDATE_ERROR": str(UPDATE_FAILURE),
        "PYINSTALLER_RESET_ENVIRONMENT": "1",
    })
    command = base64.b64encode(script.read_text(encoding="utf-8").encode("utf-16-le")).decode("ascii")
    subprocess.Popen(
        [str(program), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
        env=env, cwd=str(executable.parent), creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def stage_update(release, executable: Path, script: Path, progress):
    executable = executable.resolve(strict=True)
    if not (executable.parent / "_internal" / "web" / "version.txt").is_file():
        raise UpdateError("Автообновление доступно в распакованной Windows-сборке ОСЭК.")
    stage = Path(tempfile.mkdtemp(prefix=".osek-update-", dir=executable.parent))
    try:
        archive = stage / "update.zip"
        download_bundle(release, archive, progress)
        unpack_bundle(archive, stage, release["version"])
        archive.unlink()
        start_installer(executable, script, stage)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def acknowledge_restart(executable: Path):
    ready = os.environ.pop("OSEK_UPDATE_READY", "")
    if not ready:
        return
    marker = Path(ready).resolve()
    if (marker.name == "ready" and marker.parent.name.startswith(".osek-update-")
        and marker.parent.parent == executable.resolve().parent and marker.parent.is_dir()):
        try:
            marker.touch()
        except OSError:
            logger.warning("update_restart_ack_failed")
