"""Portfolio manifest: load it, validate it, and render the README blocks from it.

portfolio.json at the repo root is the single source for the project cards and
the Featured line. This module has no third-party dependencies, so the README can
be regenerated and checked without a browser.

README.md carries three generated blocks, each wrapped in a pair of comments:

    <!-- portfolio:begin featured --> ... <!-- portfolio:end featured -->
    <!-- portfolio:begin software -->  ... <!-- portfolio:end software -->
    <!-- portfolio:begin analysis -->  ... <!-- portfolio:end analysis -->

Everything outside those blocks is hand-written and is never touched.
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
PLACEHOLDERS = ("repo_url", "live", "version", "version_short")

TOP_KEYS = {"about", "owner", "projects"}
REQUIRED = {"repo", "title", "category", "section", "live", "version", "featured", "pitch",
            "proof", "tags", "card", "links", "checks"}
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


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


def validate(manifest: dict) -> list[str]:
    """Return a list of problems; an empty list means the manifest is well formed."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["the manifest must be a JSON object"]
    for key in sorted(set(manifest) - TOP_KEYS):
        errors.append(f"unknown top-level field {key!r}")
    owner = manifest.get("owner")
    if not isinstance(owner, str) or not REPO_NAME.match(owner):
        errors.append("owner must be a GitHub user name")
        owner = "owner"
    projects = manifest.get("projects")
    if not isinstance(projects, list) or not projects:
        return errors + ["projects must be a non-empty list"]

    seen: set[str] = set()
    orders: set[int] = set()
    for i, p in enumerate(projects):
        where = f"projects[{i}]"
        if not isinstance(p, dict):
            errors.append(f"{where} must be an object")
            continue
        name = p.get("repo", "?")
        where = f"{name}"
        for key in sorted(set(p) - REQUIRED):
            hint = " (ownership belongs in the private map, not here)" if key in ("team", "owner", "owners") else ""
            errors.append(f"{where}: unknown field {key!r}{hint}")
        for key in sorted(REQUIRED - set(p)):
            errors.append(f"{where}: missing field {key!r}")
        if REQUIRED - set(p):
            continue

        if not isinstance(p["repo"], str) or not REPO_NAME.match(p["repo"]):
            errors.append(f"{where}: repo must be a repository name")
        elif p["repo"] in seen:
            errors.append(f"{where}: listed twice")
        seen.add(str(p["repo"]))
        for key in ("title", "pitch"):
            if not isinstance(p[key], str) or not p[key].strip():
                errors.append(f"{where}: {key} must be a non-empty string")
        if p["proof"] is not None and (not isinstance(p["proof"], str) or not p["proof"].strip()):
            errors.append(f"{where}: proof must be text or null")
        if p["category"] not in CATEGORIES:
            errors.append(f"{where}: category must be one of {CATEGORIES}")
        if p["section"] not in SECTIONS:
            errors.append(f"{where}: section must be one of {SECTIONS}")
        if not isinstance(p["version"], str) or not SEMVER.match(p["version"]):
            errors.append(f"{where}: version must look like 1.2.3 (no leading v)")
            continue
        if p["live"] is not None and not (isinstance(p["live"], str) and p["live"].startswith("https://")):
            errors.append(f"{where}: live must be an https URL or null")
            continue
        if not (isinstance(p["tags"], list) and p["tags"] and all(isinstance(t, str) and t for t in p["tags"])):
            errors.append(f"{where}: tags must be a non-empty list of strings")
        card = p["card"]
        if not (isinstance(card, dict) and set(card) == {"shot", "shot_version", "host"}
                and all(isinstance(v, str) and v for v in card.values())
                and SEMVER.match(card["shot_version"])):
            errors.append(f"{where}: card needs shot, shot_version (1.2.3) and host")
        if not (isinstance(p["checks"], list) and set(p["checks"]) <= set(CHECKS)):
            errors.append(f"{where}: checks must be a list drawn from {CHECKS}")
        else:
            if ("site" in p["checks"]) != (p["live"] is not None):
                errors.append(f"{where}: the 'site' check applies exactly when there is a live URL")

        feat = p["featured"]
        if feat is not None:
            if not (isinstance(feat, dict) and set(feat) == {"order", "label", "url"}
                    and isinstance(feat["order"], int) and feat["order"] >= 1
                    and isinstance(feat["label"], str) and isinstance(feat["url"], str)):
                errors.append(f"{where}: featured must be null or {{order, label, url}}")
            elif feat["order"] in orders:
                errors.append(f"{where}: featured order {feat['order']} is used twice")
            else:
                orders.add(feat["order"])

        links = p["links"]
        if not (isinstance(links, list) and links and all(isinstance(r, list) and r for r in links)):
            errors.append(f"{where}: links must be a non-empty list of non-empty rows")
            links = []
        items = [it for row in links for it in row]
        for it in items:
            if not (isinstance(it, dict) and {"label", "url"} <= set(it) <= {"label", "url", "bold"}
                    and isinstance(it["label"], str) and isinstance(it["url"], str)):
                errors.append(f"{where}: each link needs label and url (and optionally bold)")

        # Every placeholder must be known, and {live} needs a live URL.
        texts = [p["title"], p["pitch"], p["proof"] or ""]
        texts += [it.get("url", "") for it in items if isinstance(it, dict)]
        texts += [it.get("label", "") for it in items if isinstance(it, dict)]
        if isinstance(feat, dict):
            texts += [str(feat.get("label", "")), str(feat.get("url", ""))]
        for t in texts:
            for ph in re.findall(r"\{(\w*)\}", t):
                if ph not in PLACEHOLDERS:
                    errors.append(f"{where}: unknown placeholder {{{ph}}}")
                elif ph == "live" and p["live"] is None:
                    errors.append(f"{where}: {{live}} used but live is null")

    featured = [p for p in projects if isinstance(p, dict) and p.get("featured")]
    if orders and orders != set(range(1, len(orders) + 1)):
        errors.append("featured orders must run 1, 2, 3 ... with no gaps")
    if not featured:
        errors.append("at least one project must be featured")
    return errors


# --- rendering ---------------------------------------------------------------------

def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _inline(text: str) -> str:
    """Escape plain text, then turn **bold** into <b>bold</b>."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _esc(text))


def primary_url(project: dict, owner: str) -> str:
    ctx = context(project, owner)
    return project["live"] or ctx["repo_url"]


def card_html(project: dict, owner: str) -> str:
    ctx = context(project, owner)
    primary = primary_url(project, owner)
    card = project["card"]
    image = f"assets/card-{card['shot']}-{card['shot_version']}.webp"
    lines = [
        '<td width="50%" valign="top">',
        f'<a href="{primary}"><img src="{image}" width="100%" alt="{_esc(project["title"])} screenshot"></a>',
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
            parts.append(f'<a href="{expand(item["url"], ctx)}">{label}</a>')
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
        links.append(f"[{expand(feat['label'], ctx)}]({expand(feat['url'], ctx)})")
    return "**Featured:** " + " · ".join(links)


def blocks(manifest: dict) -> dict[str, str]:
    return {
        "featured": featured_line(manifest),
        "software": table_html(manifest, "software"),
        "analysis": table_html(manifest, "analysis"),
    }


def render_readme(text: str, manifest: dict) -> str:
    """Return the README with every generated block rewritten from the manifest."""
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    for name, body in blocks(manifest).items():
        pattern = re.compile(
            rf"(<!-- portfolio:begin {name} -->\n)(.*?)(\n<!-- portfolio:end {name} -->)", re.S)
        if not pattern.search(text):
            raise ValueError(f"README.md has no 'portfolio:{name}' block")
        text = pattern.sub(lambda m: m.group(1) + body + m.group(3), text, count=1)
    return text.replace("\n", "\r\n") if crlf else text


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
