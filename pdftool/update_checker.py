import json
import re
import urllib.request
import webbrowser

from .version import APP_VERSION, GITHUB_OWNER, GITHUB_REPO, GITHUB_REPO_URL


def normalize_version(version: str) -> tuple[int, ...]:
    value = version.strip().lstrip("vV")
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts[:3]) or (0,)


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    candidate_parts = normalize_version(candidate)
    current_parts = normalize_version(current)
    max_len = max(len(candidate_parts), len(current_parts))
    candidate_parts += (0,) * (max_len - len(candidate_parts))
    current_parts += (0,) * (max_len - len(current_parts))
    return candidate_parts > current_parts


def latest_github_tag(timeout: int = 8) -> str | None:
    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/tags"
    request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "PDFTOOL"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        tags = json.loads(response.read().decode("utf-8"))
    if not tags:
        return None
    return tags[0].get("name")


def latest_release_url(tag: str | None = None) -> str:
    if tag:
        return f"{GITHUB_REPO_URL}/releases/tag/{tag}"
    return f"{GITHUB_REPO_URL}/releases"


def open_latest_release(tag: str | None = None):
    webbrowser.open(latest_release_url(tag))

