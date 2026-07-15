"""Build-time helper: upload installer zip for APP_VERSION to GitHub Releases."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pdftool.version import APP_VERSION, GITHUB_OWNER, GITHUB_REPO  # noqa: E402

REPO = f"{GITHUB_OWNER}/{GITHUB_REPO}"
TAG = f"v{APP_VERSION}"
ASSET = ROOT / "release" / f"PDFTOOL-v{APP_VERSION}-installer.zip"


def git_token() -> str:
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        check=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("No password from git credential")


def api(method: str, url: str, token: str, data: bytes | dict | None = None, content_type: str = "application/json"):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "PDFTOOL-release",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body: bytes | None = None
    if isinstance(data, dict):
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = content_type
    elif isinstance(data, (bytes, bytearray)):
        body = bytes(data)
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw_body = resp.read()
            if not raw_body:
                return {}
            if raw_body[:1] in (b"{", b"["):
                return json.loads(raw_body.decode("utf-8"))
            return {}
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {method} {url}: {err[:800]}") from exc


def main() -> None:
    if not ASSET.is_file():
        raise SystemExit(f"Missing asset: {ASSET}")

    token = git_token()
    try:
        release = api("GET", f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", token)
        print("Release exists:", release.get("html_url"))
    except RuntimeError as exc:
        if "404" not in str(exc):
            raise
        release = api(
            "POST",
            f"https://api.github.com/repos/{REPO}/releases",
            token,
            {
                "tag_name": TAG,
                "name": f"PDFTOOL v{APP_VERSION}",
                "body": (
                    f"## PDFTOOL v{APP_VERSION}\n\n"
                    "- Undo/Redo toàn tài liệu (Ctrl+Z / Ctrl+Y)\n"
                    "- Tìm & thay thế (Ctrl+H)\n"
                    "- Cài đặt lưu local (Ctrl+,)\n"
                    "- Icon ứng dụng PDFTOOL + branding taskbar (AppUserModelID)\n"
                    "- Các cải tiến Phase A/B trước đó\n\n"
                    "### Cài đặt\n"
                    f"1. Tải `PDFTOOL-v{APP_VERSION}-installer.zip`\n"
                    "2. Giải nén\n"
                    "3. Chạy `install.ps1`\n"
                ),
                "draft": False,
                "prerelease": False,
            },
        )
        print("Release created:", release.get("html_url"))

    release_id = release["id"]
    for asset in release.get("assets") or []:
        if asset.get("name") == ASSET.name:
            asset_id = asset["id"]
            print("Replacing existing asset", asset_id)
            api("DELETE", f"https://api.github.com/repos/{REPO}/releases/assets/{asset_id}", token)

    data = ASSET.read_bytes()
    upload_url = f"https://uploads.github.com/repos/{REPO}/releases/{release_id}/assets?name={ASSET.name}"
    result = api("POST", upload_url, token, data, content_type="application/zip")
    print("Uploaded:", result.get("browser_download_url") or result.get("name"))
    print("Size:", result.get("size"))


if __name__ == "__main__":
    main()
