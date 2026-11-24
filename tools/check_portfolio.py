"""Check portfolio.json against GitHub and the README. Read-only: it never writes or publishes.

    python tools/check_portfolio.py             # all checks (uses the GitHub API)
    python tools/check_portfolio.py --offline   # skip the GitHub checks

Errors (exit code 1):
  - the manifest is malformed (see portfolio.validate)
  - a manifest version differs from the repo's latest published GitHub Release
  - a listed repo is not public, is archived, or has no release
  - the generated README blocks are out of date (run: python tools/build_assets.py --readme)
  - a card image or its screenshot is missing
  - a tracked file, or a commit on HEAD or a remote branch, carries AI-assistant attribution
  - a name from the private block list appears in a tracked file (see below)

Warnings (exit code 0):
  - the repo's About homepage differs from the live URL
  - a card screenshot is older than the current release
  - a card image in assets/ is not used by any project

Private block list: if a folder named .portfolio sits next to this repository
(or PORTFOLIO_PRIVATE_DIR points at one) and holds blocked.json, a list of names,
none of those names may appear in any tracked file. That list is kept out of this
public repository on purpose.

GitHub requests use GITHUB_TOKEN / GH_TOKEN if set, else the gh CLI's login, else
no authentication (60 requests an hour is enough for this check).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import portfolio

ROOT = portfolio.ROOT
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()
TEXT_SUFFIXES = {".md", ".json", ".py", ".yml", ".yaml", ".txt", ".html", ".css", ".js", ".toml", ".svg", ".cfg", ".ini"}
# Built from pieces so this file does not match its own scan.
ATTRIBUTION = re.compile("|".join(["cl" + "aude", "anth" + "ropic"]), re.I)

errors: list[str] = []
warnings: list[str] = []


def token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def api(path: str, auth: str | None) -> dict | None:
    """GET https://api.github.com/<path>; returns None on 404."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "portfolio-check"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/{path}", headers=headers), timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def tracked_text() -> dict[str, str]:
    files: dict[str, str] = {}
    for rel in git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0"):
        if rel and Path(rel).suffix.lower() in TEXT_SUFFIXES:
            try:
                files[rel] = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    return files


def private_dir() -> Path:
    return Path(os.environ.get("PORTFOLIO_PRIVATE_DIR") or ROOT.parent / ".portfolio")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--offline", action="store_true", help="skip the checks that need the GitHub API")
    args = parser.parse_args()

    manifest = portfolio.load()
    problems = portfolio.validate(manifest)
    errors.extend(f"manifest: {p}" for p in problems)
    if problems:
        return report(0)
    owner = manifest["owner"]
    projects = manifest["projects"]

    # README blocks match the manifest.
    current = portfolio.read_text(portfolio.README)
    try:
        if portfolio.render_readme(current, manifest) != current:
            errors.append("README.md: generated blocks are out of date (run: python tools/build_assets.py --readme)")
    except ValueError as e:
        errors.append(f"README.md: {e}")

    # Card images and screenshots exist; flag unused cards and old screenshots.
    used = set()
    for p in projects:
        card = p["card"]
        name = f"card-{card['shot']}-{card['shot_version']}.webp"
        used.add(name)
        if not (ROOT / "assets" / name).is_file():
            errors.append(f"{p['repo']}: assets/{name} is missing")
        if not (ROOT / "tools" / "shots" / f"{card['shot']}-{card['shot_version']}.webp").is_file():
            errors.append(f"{p['repo']}: tools/shots/{card['shot']}-{card['shot_version']}.webp is missing")
        if card["shot_version"] != p["version"]:
            warnings.append(f"{p['repo']}: the card shows {card['shot_version']} but the version is {p['version']}")
    for f in sorted((ROOT / "assets").glob("card-*.webp")):
        if f.name not in used:
            warnings.append(f"assets/{f.name} is not used by any project")

    # Attribution and the private block list, over tracked files and local history.
    files = tracked_text()
    for rel, text in files.items():
        if rel != SELF and ATTRIBUTION.search(text):
            errors.append(f"{rel}: contains AI-assistant attribution")
    history = git("log", "HEAD", "--remotes", "--format=%an %ae %cn %ce%n%B")  # what is, or would be, published
    if ATTRIBUTION.search(history):
        errors.append("git history: a commit has AI-assistant attribution in its author, committer or message")
    blocked_file = private_dir() / "blocked.json"
    blocked: list[str] = []
    if blocked_file.is_file():
        blocked = [n.lower() for n in json.loads(blocked_file.read_text(encoding="utf-8")) if isinstance(n, str) and n.strip()]
        for rel, text in files.items():
            lower = text.lower()
            for name in blocked:
                if name in lower:
                    errors.append(f"{rel}: mentions a name on the private block list ({name})")
    else:
        warnings.append("private block list not found; skipped that check")

    # GitHub: public, not archived, latest release equals the manifest version.
    if not args.offline:
        auth = token()
        for p in projects:
            repo = p["repo"]
            info = api(f"repos/{owner}/{repo}", auth)
            if info is None:
                errors.append(f"{repo}: not found on GitHub (or not visible)")
                continue
            if info.get("private"):
                errors.append(f"{repo}: is private; the portfolio lists public repos only")
            if info.get("archived"):
                errors.append(f"{repo}: is archived")
            home = (info.get("homepage") or "").rstrip("/")
            if p["live"] and home != p["live"].rstrip("/"):
                warnings.append(f"{repo}: About homepage is {home or 'empty'}, live URL is {p['live']}")
            if "release" in p["checks"]:
                rel = api(f"repos/{owner}/{repo}/releases/latest", auth)
                if rel is None:
                    errors.append(f"{repo}: no published release")
                else:
                    tag = str(rel["tag_name"]).lstrip("vV")
                    if tag != p["version"]:
                        errors.append(f"{repo}: manifest says {p['version']} but the latest release is {rel['tag_name']}")
    return report(len(blocked))


def report(blocked_count: int) -> int:
    for w in warnings:
        print(f"warn   {w}")
    for e in errors:
        print(f"ERROR  {e}")
    status = "FAILED" if errors else "ok"
    print(f"{status}: {len(errors)} error(s), {len(warnings)} warning(s)"
          + (f"; private block list: {blocked_count} name(s) checked" if blocked_count else ""))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
