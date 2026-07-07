import json
import os
import re
import subprocess
import urllib.request
import webbrowser
from pathlib import Path

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


def github_release(tag: str, timeout: int = 8) -> dict | None:
    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tags/{tag}"
    request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "PDFTOOL"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def installer_asset(release: dict | None) -> dict | None:
    if not release:
        return None
    assets = release.get("assets") or []
    preferred = [
        asset
        for asset in assets
        if "installer" in asset.get("name", "").lower() and asset.get("browser_download_url")
    ]
    if preferred:
        return preferred[0]
    for asset in assets:
        name = asset.get("name", "").lower()
        if name.endswith((".zip", ".exe", ".msi")) and asset.get("browser_download_url"):
            return asset
    return None


def download_asset(asset: dict, destination_dir: str | None = None) -> Path:
    destination = Path(destination_dir or str(Path.home() / "Downloads"))
    destination.mkdir(parents=True, exist_ok=True)
    file_path = destination / asset["name"]
    request = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "PDFTOOL"})
    with urllib.request.urlopen(request, timeout=60) as response:
        file_path.write_bytes(response.read())
    return file_path


def open_downloaded_installer(file_path: Path):
    if file_path.suffix.lower() == ".zip":
        subprocess.Popen(["explorer", "/select,", str(file_path)])
        return
    os.startfile(file_path)


def latest_release_url(tag: str | None = None) -> str:
    if tag:
        return f"{GITHUB_REPO_URL}/releases/tag/{tag}"
    return f"{GITHUB_REPO_URL}/releases"


def open_latest_release(tag: str | None = None):
    webbrowser.open(latest_release_url(tag))


def download_latest_installer(tag: str) -> Path | None:
    release = github_release(tag)
    asset = installer_asset(release)
    if not asset:
        return None
    return download_asset(asset)
