"""Check portfolio.json against GitHub and the README. Read-only: it never writes or publishes.

    python tools/check_portfolio.py                     # all checks (uses the GitHub API)
    python tools/check_portfolio.py --offline           # skip the GitHub checks
    python tools/check_portfolio.py --history-of PATH   # only scan the history of another clone

Errors (exit code 1):
  - the manifest is malformed (see portfolio.validate)
  - a manifest version differs from the repo's latest published GitHub Release
  - a listed repo is not public, is archived, or has no release
  - the generated README blocks are out of date (run: python tools/build_assets.py --readme)
  - a card image or its screenshot is missing
  - a file in this repo (working tree or staged), or a commit on HEAD or a remote
    branch, carries Claude or Anthropic attribution, or a co-author trailer that is
    neither the owner nor a bot
  - a name from the private block list (blocked.json) appears in a text file, or in
    published history: commit messages, author fields, file names or any file version
  - a private list is unreadable or is not a JSON list of non-empty text
  - GitHub could not be asked (rate limit, network): the check cannot pass blind

Warnings (exit code 0):
  - a stem from the private stem list (blocked-stems.json) appears anywhere the block
    list is scanned. A stem hit is for a person to review, never a failure.
  - the repo's About homepage differs from the live URL
  - a card screenshot is older than the current release
  - a card image in assets/ is not used by any project
  - a private list is missing, so that part of the eligibility check was skipped

Matching rules for both private lists: case-insensitive substring, except that a
term containing a space matches whole words only (spaces match any run of white
space), because some of them are ordinary English phrases. Output names a list entry
by its number (entry #3 is the third item), never by its text, so a report can be
shared without repeating the names.

Private lists: if a folder named .portfolio sits next to this repository (or
PORTFOLIO_PRIVATE_DIR points at one), blocked.json and blocked-stems.json in it are
flat JSON lists of text. They are kept out of this public repository on purpose.

Scope notes:
  - Whether a public repo is fit for the portfolio (for sale, retired) cannot be read
    from GitHub; that is the private block list's job. Without it, only public and
    not-archived are checked.
  - Remote branches are read as last fetched; run `git fetch` first for the latest.

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
from collections import Counter
from pathlib import Path

import portfolio

ROOT = portfolio.ROOT
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()
# Built from pieces so this file does not match its own scan.
ATTRIBUTION = re.compile("|".join(["cl" + "aude", "anth" + "ropic"]), re.I)
CO_AUTHOR = re.compile(r"^\s*co-authored-by:\s*(.+)$", re.I | re.M)
LOG_FORMAT = "%h%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e"

errors: list[str] = []
warnings: list[str] = []


class GitHubError(Exception):
    pass


# --- GitHub ------------------------------------------------------------------------

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


# --- git ---------------------------------------------------------------------------

def git(*args: str, binary: bool = False, repo: Path = ROOT, stdin: bytes | None = None):
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True, input=stdin)
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


def history_units(repo: Path):
    """Yield (kind, label, text) for everything published: commit messages, author and
    committer fields, file names, and every distinct text version of every file."""
    for rec in git("log", "HEAD", "--remotes", f"--format={LOG_FORMAT}", repo=repo).split("\x1e"):
        rec = rec.strip("\n")
        if rec:
            sha, an, ae, cn, ce, body = (rec.split("\x1f") + [""] * 6)[:6]
            yield "commit message", sha, body
            yield "author or committer field", sha, " ".join((an, ae, cn, ce))
    names: dict[str, str] = {}
    for line in git("rev-list", "--objects", "HEAD", "--remotes", repo=repo).splitlines():
        sha, _, path = line.partition(" ")
        if sha and path:
            names.setdefault(sha, path)
    for path in sorted(set(names.values())):
        yield "file name", path, path
    if not names:
        return
    out = git("cat-file", "--batch", repo=repo, binary=True, stdin=("\n".join(names) + "\n").encode())
    pos = 0
    while pos < len(out):
        eol = out.index(b"\n", pos)
        header = out[pos:eol].decode("utf-8", errors="replace").split()
        if len(header) < 3 or header[1] == "missing":
            pos = eol + 1
            continue
        sha, kind, size = header[0], header[1], int(header[2])
        data = out[eol + 1:eol + 1 + size]
        pos = eol + 1 + size + 1
        if kind == "blob" and b"\0" not in data[:8000]:
            yield "file version", names.get(sha, sha[:7]), data.decode("utf-8", errors="ignore")


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


# --- private lists -----------------------------------------------------------------

def load_list(path: Path) -> tuple[list[str], str | None]:
    """Return (entries, problem). The file must be a JSON list of non-empty text."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], f"{path.name} is unreadable: {e}"
    if not (isinstance(raw, list) and all(isinstance(n, str) and n.strip() for n in raw)):
        return [], f"{path.name} must be a JSON list of non-empty text"
    return [n.strip() for n in raw], None


def compile_terms(entries: list[str]) -> list[re.Pattern]:
    """Case-insensitive substring; a term with a space matches whole words only."""
    patterns = []
    for entry in entries:
        words = entry.split()
        if len(words) > 1:
            body = r"\s+".join(re.escape(w) for w in words)
            patterns.append(re.compile(r"(?<!\w)" + body + r"(?!\w)", re.I))
        else:
            patterns.append(re.compile(re.escape(entry), re.I))
    return patterns


def matches(patterns: list[re.Pattern], text: str, context: int = 0) -> list[tuple[int, int, str]]:
    """First match of each entry in the text: (entry number, line number, masked snippet)."""
    found = []
    for number, pattern in enumerate(patterns, 1):
        m = pattern.search(text)
        if m:
            snippet = ""
            if context:
                a, b = max(0, m.start() - context), min(len(text), m.end() + context)
                snippet = " ".join((text[a:m.start()] + f"<#{number}>" + text[m.end():b]).split())
            found.append((number, text.count("\n", 0, m.start()) + 1, snippet))
    return found


def scan_files(texts: dict[str, str], exact: list[re.Pattern], stems: list[re.Pattern]) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    for rel, text in texts.items():
        errs += [f"{rel}:{line}: matches entry #{n} of blocked.json" for n, line, _ in matches(exact, text)]
        warns += [f"{rel}:{line}: matches stem #{n} of blocked-stems.json, review: ...{snip}..."
                  for n, line, snip in matches(stems, text, 25)]
    return errs, warns


def scan_history(repo: Path, exact: list[re.Pattern], stems: list[re.Pattern]) -> tuple[list[str], list[str]]:
    """Scan published history with both lists; hits are counted per kind, file and entry."""
    hit_exact: Counter = Counter()
    hit_stem: Counter = Counter()
    sample: dict[tuple, str] = {}
    for kind, label, text in history_units(repo):
        for n, _, _ in matches(exact, text):
            hit_exact[(kind, label if kind in ("file version", "file name") else "", n)] += 1
        for n, _, snip in matches(stems, text, 25):
            key = (kind, label if kind in ("file version", "file name") else "", n)
            hit_stem[key] += 1
            sample.setdefault(key, snip)

    def line(prefix: str, kind: str, label: str, n: int, count: int, which: str) -> str:
        where = f" of {label}" if label else ""
        return f"history: {count} {kind}(s){where} match {prefix} #{n} of {which}"

    errs = [line("entry", k, lab, n, c, "blocked.json") for (k, lab, n), c in sorted(hit_exact.items())]
    warns = [line("stem", k, lab, n, c, "blocked-stems.json") + f", review: ...{sample[(k, lab, n)]}..."
             for (k, lab, n), c in sorted(hit_stem.items())]
    return errs, warns


def private_dir() -> Path:
    return Path(os.environ.get("PORTFOLIO_PRIVATE_DIR") or ROOT.parent / ".portfolio")


def load_private_lists() -> tuple[list[re.Pattern], list[re.Pattern], int, int]:
    """Read blocked.json and blocked-stems.json; problems go to errors, absences to warnings."""
    patterns: dict[str, list[re.Pattern]] = {}
    counts: dict[str, int] = {}
    for name in ("blocked.json", "blocked-stems.json"):
        path = private_dir() / name
        if not path.is_file():
            warnings.append(f"private list {name} not found; skipped that part of the eligibility check")
            patterns[name], counts[name] = [], 0
            continue
        entries, problem = load_list(path)
        if problem:
            errors.append(problem)
        patterns[name], counts[name] = compile_terms(entries), len(entries)
    return patterns["blocked.json"], patterns["blocked-stems.json"], counts["blocked.json"], counts["blocked-stems.json"]


# --- the check ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--offline", action="store_true", help="skip the checks that need the GitHub API")
    parser.add_argument("--history-of", metavar="PATH", help="only scan the published history of the git clone at PATH")
    args = parser.parse_args()

    try:
        manifest = portfolio.load()
    except (OSError, ValueError) as e:
        errors.append(f"manifest: cannot read portfolio.json ({e})")
        return report(0, 0)
    problems = portfolio.validate(manifest)
    errors.extend(f"manifest: {p}" for p in problems)
    if problems:
        return report(0, 0)
    owner = manifest["owner"]
    projects = manifest["projects"]
    exact, stems, n_exact, n_stems = load_private_lists()

    if args.history_of:
        repo = Path(args.history_of)
        errors.extend(history_problems(git("log", "HEAD", "--remotes", f"--format={LOG_FORMAT}", repo=repo), owner))
        e, w = scan_history(repo, exact, stems)
        errors.extend(e)
        warnings.extend(w)
        return report(n_exact, n_stems)

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

    # Attribution and the private lists, over repo files and published history.
    texts = repo_texts()
    for rel, text in texts.items():
        if rel.split(" (staged)")[0] != SELF and ATTRIBUTION.search(text):
            errors.append(f"{rel}: contains AI-assistant attribution")
    errors.extend(history_problems(git("log", "HEAD", "--remotes", f"--format={LOG_FORMAT}"), owner))
    e, w = scan_files(texts, exact, stems)
    errors.extend(e)
    warnings.extend(w)
    e, w = scan_history(ROOT, exact, stems)
    errors.extend(e)
    warnings.extend(w)

    # GitHub: public, not archived, latest release equals the manifest version.
    if not args.offline:
        auth = token()
        for p in projects:
            try:
                check_repo(p, owner, auth)
            except GitHubError as e:
                errors.append(f"{p['repo']}: {e}")
    return report(n_exact, n_stems)


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


def report(n_exact: int, n_stems: int) -> int:
    for w in warnings:
        print(f"warn   {w}")
    for e in errors:
        print(f"ERROR  {e}")
    status = "FAILED" if errors else "ok"
    lists = f"; private lists: {n_exact} blocked name(s), {n_stems} stem(s) checked" if (n_exact or n_stems) else ""
    print(f"{status}: {len(errors)} error(s), {len(warnings)} warning(s){lists}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
