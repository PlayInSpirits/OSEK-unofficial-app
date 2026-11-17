"""Find downloadable previews in the project's public GitHub releases."""

import re

import requests

from .diagnostic_log import logger


REPO = "PlayInSpirits/SDO-Reborn"


def check_preview(installed):
    try:
        with requests.get(
            f"https://api.github.com/repos/{REPO}/releases",
            params={"per_page": 100},
            headers={"Accept": "application/vnd.github+json"},
            timeout=(4, 6),
        ) as response:
            response.raise_for_status()
            releases = response.json()
    except (requests.RequestException, ValueError) as error:
        logger.warning("preview_check_failed type=%s", type(error).__name__)
        return {"ok": False}
    if not isinstance(releases, list):
        return {"ok": False}

    available = installed
    for release in releases:
        if release.get("draft") or not release.get("prerelease"):
            continue
        match = re.fullmatch(r"preview-([0-9]{1,9})", release.get("tag_name", ""))
        if match and any(
            asset.get("name") == "OSEK-Remastered-Windows.zip" and asset.get("state") == "uploaded"
            for asset in release.get("assets", [])
        ):
            available = max(available, int(match[1]))
    url = f"https://github.com/{REPO}/releases/tag/preview-{available}" if available > installed else ""
    return {"ok": True, "url": url}
