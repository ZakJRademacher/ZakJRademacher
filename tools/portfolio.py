"""Portfolio manifest: load it, validate it, and render the README blocks from it.

portfolio.json at the repo root is the single source for the project cards and
the Featured line. This module has no third-party dependencies, so the README can
be regenerated and checked without a browser.

README.md carries three generated blocks, each wrapped in a pair of comments:

    <!-- portfolio:begin featured --> ... <!-- portfolio:end featured -->
    <!-- portfolio:begin software -->  ... <!-- portfolio:end software -->
    <!-- portfolio:begin analysis -->  ... <!-- portfolio:end analysis -->

Everything outside those blocks is hand-written and is never touched, line
endings included.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "portfolio.json"
README = ROOT / "README.md"

CATEGORIES = ("software", "web")
SECTIONS = ("software", "analysis")
CHECKS = ("release", "links", "site")
ALWAYS_CHECKS = ("release", "links")  # apply to every project
BLOCKS = ("featured", "software", "analysis")
PLACEHOLDERS = ("repo_url", "live", "version", "version_short")

TOP_KEYS = {"about", "owner", "projects"}
REQUIRED = {"repo", "title", "category", "section", "live", "version", "featured", "pitch",
            "proof", "tags", "card", "links", "checks"}
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MARKER = re.compile(r"<!-- portfolio:(begin|end) (\S+) -->")


def load(path: Path = MANIFEST) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def context(project: dict, owner: str) -> dict[str, str]:
    version = project["version"]
    return {
        "repo_url": f"https://github.com/{owner}/{project['repo']}",
        "live": project["live"] or "",
        "version": version,
        "version_short": ".".join(version.split(".")[:2]),
    }


def expand(text: str, ctx: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: ctx[m.group(1)], text)


def _is_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(manifest: object) -> list[str]:
    """Return a list of problems; an empty list means the manifest is well formed."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["the manifest must be a JSON object"]
    for key in sorted(set(manifest) - TOP_KEYS):
        errors.append(f"unknown top-level field {key!r}")
    if "about" in manifest and not isinstance(manifest["about"], str):
        errors.append("about must be text")
    owner = manifest.get("owner")
    if not isinstance(owner, str) or not REPO_NAME.match(owner):
        errors.append("owner must be a GitHub user name")
        owner = "owner"
    projects = manifest.get("projects")
    if not isinstance(projects, list) or not projects:
        return errors + ["projects must be a non-empty list"]

    seen_repos: set[str] = set()
    seen_shots: set[str] = set()
    orders: list[int] = []
    for i, p in enumerate(projects):
        if not isinstance(p, dict):
            errors.append(f"projects[{i}] must be an object")
            continue
        name = p.get("repo") if isinstance(p.get("repo"), str) else f"projects[{i}]"
        try:
            errors.extend(_validate_project(p, name, seen_repos, seen_shots, orders))
        except (TypeError, AttributeError, KeyError, ValueError) as e:
            errors.append(f"{name}: malformed value ({type(e).__name__}: {e})")

    if not any(isinstance(p, dict) and p.get("featured") for p in projects):
        errors.append("at least one project must be featured")
    if sorted(orders) != list(range(1, len(orders) + 1)):
        errors.append("featured orders must run 1, 2, 3 ... with no gaps or repeats")
    return errors


def _validate_project(p: dict, where: str, seen_repos: set[str], seen_shots: set[str],
                      orders: list[int]) -> list[str]:
    errors: list[str] = []
    for key in sorted(set(p) - REQUIRED):
        hint = " (ownership belongs in the private map, not here)" if key in ("team", "owner", "owners") else ""
        errors.append(f"{where}: unknown field {key!r}{hint}")
    missing = sorted(REQUIRED - set(p))
    for key in missing:
        errors.append(f"{where}: missing field {key!r}")
    if missing:
        return errors

    if not isinstance(p["repo"], str) or not REPO_NAME.match(p["repo"]):
        errors.append(f"{where}: repo must be a repository name")
    elif p["repo"] in seen_repos:
        errors.append(f"{where}: listed twice")
    if isinstance(p["repo"], str):
        seen_repos.add(p["repo"])
    for key in ("title", "pitch"):
        if not _is_str(p[key]):
            errors.append(f"{where}: {key} must be a non-empty string")
    if p["proof"] is not None and not _is_str(p["proof"]):
        errors.append(f"{where}: proof must be text or null")
    if p["category"] not in CATEGORIES:
        errors.append(f"{where}: category must be one of {CATEGORIES}")
    if p["section"] not in SECTIONS:
        errors.append(f"{where}: section must be one of {SECTIONS}")
    if not isinstance(p["version"], str) or not SEMVER.match(p["version"]):
        return errors + [f"{where}: version must look like 1.2.3 (no leading v)"]
    if p["live"] is not None and not (isinstance(p["live"], str) and p["live"].startswith("https://")):
        return errors + [f"{where}: live must be an https URL or null"]
    if not (isinstance(p["tags"], list) and p["tags"] and all(_is_str(t) for t in p["tags"])):
        errors.append(f"{where}: tags must be a non-empty list of strings")

    card = p["card"]
    if not (isinstance(card, dict) and set(card) == {"shot", "shot_version", "host"}
            and all(isinstance(v, str) and v for v in card.values())):
        errors.append(f"{where}: card needs shot, shot_version and host (all text)")
    else:
        if not SLUG.match(card["shot"]):
            errors.append(f"{where}: card.shot must be a plain name (letters, digits, hyphens)")
        elif card["shot"] in seen_shots:
            errors.append(f"{where}: card.shot {card['shot']!r} is used by another project")
        seen_shots.add(card["shot"])
        if not SEMVER.match(card["shot_version"]):
            errors.append(f"{where}: card.shot_version must look like 1.2.3")

    checks = p["checks"]
    if not (isinstance(checks, list) and all(isinstance(c, str) for c in checks) and set(checks) <= set(CHECKS)):
        errors.append(f"{where}: checks must be a list drawn from {CHECKS}")
    else:
        for needed in ALWAYS_CHECKS:
            if needed not in checks:
                errors.append(f"{where}: checks must include {needed!r}")
        if ("site" in checks) != (p["live"] is not None):
            errors.append(f"{where}: the 'site' check applies exactly when there is a live URL")

    feat = p["featured"]
    if feat is not None:
        if not (isinstance(feat, dict) and set(feat) == {"order", "label", "url"}
                and isinstance(feat["order"], int) and not isinstance(feat["order"], bool) and feat["order"] >= 1
                and _is_str(feat["label"]) and _is_str(feat["url"])):
            errors.append(f"{where}: featured must be null or {{order, label, url}}")
            feat = None
        else:
            orders.append(feat["order"])

    links = p["links"]
    if not (isinstance(links, list) and links and all(isinstance(r, list) and r for r in links)):
        errors.append(f"{where}: links must be a non-empty list of non-empty rows")
        links = []
    items = [it for row in links for it in row]
    good_items = []
    for it in items:
        if not (isinstance(it, dict) and {"label", "url"} <= set(it) <= {"label", "url", "bold"}
                and _is_str(it["label"]) and _is_str(it["url"])
                and isinstance(it.get("bold", False), bool)):
            errors.append(f"{where}: each link needs text label and url (and optionally bold: true or false)")
        else:
            good_items.append(it)

    # Every placeholder must be known, and {live} needs a live URL.
    texts = [p["title"], p["pitch"], p["proof"] or ""]
    texts += [it["url"] for it in good_items] + [it["label"] for it in good_items]
    if feat is not None:
        texts += [feat["label"], feat["url"]]
    for t in texts:
        for ph in re.findall(r"\{(\w*)\}", t):
            if ph not in PLACEHOLDERS:
                errors.append(f"{where}: unknown placeholder {{{ph}}}")
            elif ph == "live" and p["live"] is None:
                errors.append(f"{where}: {{live}} used but live is null")
    return errors


# --- rendering ---------------------------------------------------------------------

def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def _inline(text: str) -> str:
    """Escape plain text, then turn **bold** into <b>bold</b>."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _esc(text))


def _md_label(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _md_url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def primary_url(project: dict, owner: str) -> str:
    return project["live"] or context(project, owner)["repo_url"]


def card_html(project: dict, owner: str) -> str:
    ctx = context(project, owner)
    primary = _attr(primary_url(project, owner))
    card = project["card"]
    image = _attr(f"assets/card-{card['shot']}-{card['shot_version']}.webp")
    lines = [
        '<td width="50%" valign="top">',
        f'<a href="{primary}"><img src="{image}" width="100%" alt="{_attr(project["title"])} screenshot"></a>',
        f'<h3><a href="{primary}">{_esc(project["title"])}</a></h3>',
        f'<p>{_inline(expand(project["pitch"], ctx))}</p>',
    ]
    if project["proof"]:
        lines.append(f'<p>{_inline(expand(project["proof"], ctx))}</p>')
    tags = " ".join(f"<code>{_esc(t)}</code>" for t in project["tags"])
    lines.append(f"<p><sub>{tags}</sub></p>")
    for row in project["links"]:
        parts = []
        for item in row:
            label = _inline(expand(item["label"], ctx))
            label = f"<b>{label}</b>" if item.get("bold") else label
            parts.append(f'<a href="{_attr(expand(item["url"], ctx))}">{label}</a>')
        lines.append("<p>" + " · ".join(parts) + "</p>")
    lines.append("</td>")
    return "\n".join(lines)


def table_html(manifest: dict, section: str) -> str:
    owner = manifest["owner"]
    cards = [card_html(p, owner) for p in manifest["projects"] if p["section"] == section]
    rows = ["<tr>\n" + "\n".join(cards[i:i + 2]) + "\n</tr>" for i in range(0, len(cards), 2)]
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def featured_line(manifest: dict) -> str:
    owner = manifest["owner"]
    picked = sorted((p for p in manifest["projects"] if p["featured"]), key=lambda p: p["featured"]["order"])
    links = []
    for p in picked:
        ctx = context(p, owner)
        feat = p["featured"]
        links.append(f"[{_md_label(expand(feat['label'], ctx))}]({_md_url(expand(feat['url'], ctx))})")
    return "**Featured:** " + " · ".join(links)


def blocks(manifest: dict) -> dict[str, str]:
    return {
        "featured": featured_line(manifest),
        "software": table_html(manifest, "software"),
        "analysis": table_html(manifest, "analysis"),
    }


def marker_problems(text: str) -> list[str]:
    """Each block needs exactly one begin and one end comment, in order, not nested."""
    found = [(m.group(1), m.group(2), m.start()) for m in MARKER.finditer(text)]
    problems = [f"unknown marker 'portfolio:{k} {n}'" for k, n, _ in found if n not in BLOCKS]
    for name in BLOCKS:
        begins = [pos for k, n, pos in found if k == "begin" and n == name]
        ends = [pos for k, n, pos in found if k == "end" and n == name]
        if len(begins) != 1 or len(ends) != 1:
            problems.append(f"block '{name}' needs exactly one begin and one end marker "
                            f"(found {len(begins)} and {len(ends)})")
        elif begins[0] > ends[0]:
            problems.append(f"block '{name}' has its end marker before its begin marker")
        elif any(begins[0] < pos < ends[0] for k, n, pos in found if n != name):
            problems.append(f"block '{name}' overlaps another block")
    return problems


def render_readme(text: str, manifest: dict) -> str:
    """Return the README with every generated block rewritten from the manifest.

    Text outside the blocks is returned byte for byte; a block takes the line
    ending of its begin marker.
    """
    problems = marker_problems(text)
    if problems:
        raise ValueError("README.md markers: " + "; ".join(problems))
    for name, body in blocks(manifest).items():
        pattern = re.compile(
            rf"(<!-- portfolio:begin {name} -->)(\r?\n)(?:.*?\r?\n)?(<!-- portfolio:end {name} -->)", re.S)
        text = pattern.sub(
            lambda m: m.group(1) + m.group(2) + body.replace("\n", m.group(2)) + m.group(2) + m.group(3),
            text, count=1)
    return text


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def update_readme(manifest: dict, path: Path = README) -> bool:
    """Rewrite the generated blocks in README.md. Returns True when the file changed."""
    old = read_text(path)
    new = render_readme(old, manifest)
    if new != old:
        write_text(path, new)
    return new != old
