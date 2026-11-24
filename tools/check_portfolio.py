"""Check portfolio.json against GitHub and the README. Read-only: it never writes or publishes.

    python tools/check_portfolio.py             # all checks (uses the GitHub API)
    python tools/check_portfolio.py --offline   # skip the GitHub checks

Errors (exit code 1):
  - the manifest is malformed (see portfolio.validate)
  - a manifest version differs from the repo's latest published GitHub Release
  - a listed repo is not public, is archived, or has no release
  - the generated README blocks are out of date (run: python tools/build_assets.py --readme)
  - a card image or its screenshot is missing
  - a file in this repo (working tree or staged), or a commit on HEAD or a remote
    branch, carries Claude or Anthropic attribution, or a co-author trailer that is
    neither the owner nor a bot
  - a name from the private block list appears in a text file of this repo
  - GitHub could not be asked (rate limit, network): the check cannot pass blind

Warnings (exit code 0):
  - the repo's About homepage differs from the live URL
  - a card screenshot is older than the current release
  - a card image in assets/ is not used by any project
  - there is no private block list, so the eligibility check was skipped

Scope notes:
  - Whether a public repo is fit for the portfolio (for sale, retired) cannot be read
    from GitHub; that is the private block list's job. Without it, only public and
    not-archived are checked.
  - Remote branches are read as last fetched; run `git fetch` first for the latest.

Private block list: if a folder named .portfolio sits next to this repository
(or PORTFOLIO_PRIVATE_DIR points at one) and holds blocked.json, a JSON list of
names, none of those names may appear in any text file. That list is kept out of
this public repository on purpose.

GitHub requests use GITHUB_TOKEN / GH_TOKEN if set, else the gh CLI's login, else
no authentication (60 requests an hour is enough for this check).
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # a read-only check should not leave __pycache__ behind

import argparse
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import portfolio

ROOT = portfolio.ROOT
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()
# Built from pieces so this file does not match its own scan.
ATTRIBUTION = re.compile("|".join(["cl" + "aude", "anth" + "ropic"]), re.I)
CO_AUTHOR = re.compile(r"^\s*co-authored-by:\s*(.+)$", re.I | re.M)

errors: list[str] = []
warnings: list[str] = []


class GitHubError(Exception):
    pass


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
    """GET https://api.github.com/<path>. Returns None on 404; raises GitHubError otherwise."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "portfolio-check"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/{path}", headers=headers), timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        hint = " (rate limit: set GITHUB_TOKEN or sign in with gh)" if e.code in (403, 429) else ""
        raise GitHubError(f"GitHub answered {e.code} for {path}{hint}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GitHubError(f"could not reach GitHub for {path}: {e}") from e
    except ValueError as e:
        raise GitHubError(f"GitHub sent something that is not JSON for {path}") from e
    if not isinstance(data, dict):
        raise GitHubError(f"unexpected answer shape for {path}")
    return data


def git(*args: str, binary: bool = False):
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode("utf-8", errors="replace")


def repo_texts() -> dict[str, str]:
    """Text of every file in the repo: the working tree, plus the staged version when it differs.

    Any extension is read; files with a NUL byte near the start are treated as binary and skipped.
    """
    texts: dict[str, str] = {}
    listing = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    cached = set(git("ls-files", "-z", "--cached").split("\0"))
    for rel in filter(None, listing):
        try:
            data = (ROOT / rel).read_bytes()
        except OSError:
            data = b""
        if b"\0" in data[:8000]:
            continue
        texts[rel] = data.decode("utf-8", errors="ignore")
        if rel in cached:
            try:
                staged = git("show", f":{rel}", binary=True)
            except subprocess.CalledProcessError:
                continue
            if staged != data and b"\0" not in staged[:8000]:
                texts[f"{rel} (staged)"] = staged.decode("utf-8", errors="ignore")
    return texts


def history_problems(log: str, owner: str) -> list[str]:
    """Problems in `git log` output made with --format=%h%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e."""
    problems = []
    for record in log.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, rest = record.partition("\x1f")
        if ATTRIBUTION.search(rest):
            problems.append(f"commit {sha}: AI-assistant attribution in its author, committer or message")
        for line in CO_AUTHOR.findall(rest):
            if "[bot]" not in line.lower() and owner.lower() not in line.lower():
                problems.append(f"commit {sha}: co-author trailer for someone other than {owner} or a bot")
    return problems


def load_blocklist(path: Path) -> tuple[list[str], str | None]:
    """Return (names, problem). The file must be a JSON list of non-empty strings."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], f"private block list is unreadable: {e}"
    if not (isinstance(raw, list) and all(isinstance(n, str) and n.strip() for n in raw)):
        return [], "private block list must be a JSON list of non-empty names"
    return [n.lower() for n in raw], None


def private_dir() -> Path:
    return Path(os.environ.get("PORTFOLIO_PRIVATE_DIR") or ROOT.parent / ".portfolio")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--offline", action="store_true", help="skip the checks that need the GitHub API")
    args = parser.parse_args()

    try:
        manifest = portfolio.load()
    except (OSError, ValueError) as e:
        errors.append(f"manifest: cannot read portfolio.json ({e})")
        return report(0)
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
        errors.append(str(e))

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

    # Attribution and the private block list, over repo files and published history.
    texts = repo_texts()
    for rel, text in texts.items():
        if rel.split(" (staged)")[0] != SELF and ATTRIBUTION.search(text):
            errors.append(f"{rel}: contains AI-assistant attribution")
    fmt = "%h%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e"
    errors.extend(history_problems(git("log", "HEAD", "--remotes", f"--format={fmt}"), owner))
    blocked_file = private_dir() / "blocked.json"
    blocked: list[str] = []
    if blocked_file.is_file():
        blocked, problem = load_blocklist(blocked_file)
        if problem:
            errors.append(problem)
        for rel, text in texts.items():
            lower = text.lower()
            errors.extend(f"{rel}: mentions a name on the private block list ({n})" for n in blocked if n in lower)
    else:
        warnings.append("private block list not found; skipped the eligibility check")

    # GitHub: public, not archived, latest release equals the manifest version.
    if not args.offline:
        auth = token()
        for p in projects:
            try:
                check_repo(p, owner, auth)
            except GitHubError as e:
                errors.append(f"{p['repo']}: {e}")
    return report(len(blocked))


def check_repo(p: dict, owner: str, auth: str | None) -> None:
    repo = p["repo"]
    info = api(f"repos/{owner}/{repo}", auth)
    if info is None:
        errors.append(f"{repo}: not found on GitHub (or not visible)")
        return
    if info.get("private") is not False:
        errors.append(f"{repo}: is not confirmed public; the portfolio lists public repos only")
    if info.get("archived") is not False:
        errors.append(f"{repo}: is archived (or its state could not be read)")
    home = str(info.get("homepage") or "").rstrip("/")
    if p["live"] and home != p["live"].rstrip("/"):
        warnings.append(f"{repo}: About homepage is {home or 'empty'}, live URL is {p['live']}")
    rel = api(f"repos/{owner}/{repo}/releases/latest", auth)
    if rel is None:
        errors.append(f"{repo}: no published release")
    elif not isinstance(rel.get("tag_name"), str):
        errors.append(f"{repo}: the latest release has no tag name")
    elif rel["tag_name"].lstrip("vV") != p["version"]:
        errors.append(f"{repo}: manifest says {p['version']} but the latest release is {rel['tag_name']}")


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
