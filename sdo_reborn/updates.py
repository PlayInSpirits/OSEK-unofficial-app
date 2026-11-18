"""Find published Windows builds newer than the installed app."""

import re

import requests

from .diagnostic_log import logger


REPO = "PlayInSpirits/OSEK-unofficial-app"
RELEASE_INTERVAL = 15 * 60
RELEASE_TAG = re.compile(r"OSEK-v0\.([0-9]{1,9})")


def check_release(installed):
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
        logger.warning("release_check_failed type=%s", type(error).__name__)
        return {"ok": False}
    if not isinstance(releases, list):
        return {"ok": False}

    available = installed
    tag = ""
    for release in releases:
        if release.get("draft"):
            continue
        match = RELEASE_TAG.fullmatch(release.get("tag_name", ""))
        if match and int(match[1]) > available and any(
            asset.get("name") in {"OSEK-Remastered-Windows.zip", f"OSEK-Remastered-v0.{int(match[1])}.zip"}
            and asset.get("state") == "uploaded"
            for asset in release.get("assets", [])
        ):
            available = int(match[1])
            tag = match[0]
    return {
        "ok": True,
        "version": available if tag else None,
        "url": f"https://github.com/{REPO}/releases/tag/{tag}" if tag else "",
    }
