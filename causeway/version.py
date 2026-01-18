"""Version management for causeway."""
import json
import subprocess
import urllib.request
import urllib.error
from functools import lru_cache
from pathlib import Path
from typing import Optional

GITHUB_API_URL = "https://api.github.com/repos/codimusmaximus/causeway/releases/latest"
CAUSEWAY_ROOT = Path(__file__).parent.parent.resolve()


@lru_cache(maxsize=1)
def get_local_version() -> str:
    """Get local version from git tags.

    Returns tag (e.g., "v0.2.0"), tag with commits (e.g., "v0.2.0-5-gabcdef"),
    commit hash, or "unknown" if not in a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=CAUSEWAY_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def clear_version_cache():
    """Clear the version cache after an update."""
    get_local_version.cache_clear()


def get_version_tuple(version_str: str) -> tuple:
    """Parse version string to comparable tuple.

    Handles:
    - "v0.2.0" -> (0, 2, 0)
    - "v0.2.0-5-gabcdef" -> (0, 2, 0)
    - "0.2.0" -> (0, 2, 0)
    - "abcdef" (commit hash) -> (0, 0, 0)
    """
    if version_str == "unknown":
        return (0, 0, 0)

    # Remove 'v' prefix if present
    version = version_str.lstrip("v")

    # Handle "v0.2.0-5-gabcdef" format - take only the version part
    if "-" in version:
        version = version.split("-")[0]

    # Try to parse as semver
    try:
        parts = version.split(".")
        return tuple(int(p) for p in parts[:3])
    except (ValueError, IndexError):
        # Probably a commit hash
        return (0, 0, 0)


def is_newer_version(latest: str, current: str) -> bool:
    """Check if latest version is newer than current.

    Compares major.minor.patch only.
    """
    latest_tuple = get_version_tuple(latest)
    current_tuple = get_version_tuple(current)
    return latest_tuple > current_tuple


def is_on_edge() -> bool:
    """Check if we're ahead of the latest tag (on edge/development).

    Returns True if version is like "v0.2.0-5-gabcdef" (commits ahead of tag).
    """
    version = get_local_version()
    if version == "unknown":
        return False
    # Check if there are commits after the tag (e.g., "v0.2.0-5-gabcdef")
    return "-" in version and "g" in version.split("-")[-1]


def fetch_latest_release() -> Optional[dict]:
    """Fetch latest release info from GitHub API.

    Returns dict with tag_name, name, html_url or None on failure.
    """
    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "causeway",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def check_for_updates() -> dict:
    """Check for available updates.

    Returns dict with:
    - current_version: str
    - latest_version: str or None
    - update_available: bool
    - on_edge: bool
    - release_url: str or None
    """
    current = get_local_version()
    result = {
        "current_version": current,
        "latest_version": None,
        "update_available": False,
        "on_edge": is_on_edge(),
        "release_url": None,
    }

    release = fetch_latest_release()
    if release:
        latest = release.get("tag_name", "")
        result["latest_version"] = latest
        result["release_url"] = release.get("html_url")
        result["update_available"] = is_newer_version(latest, current)

    return result
