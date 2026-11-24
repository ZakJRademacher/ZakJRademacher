"""Tests for the portfolio manifest, the README generator and the check.

    python -m unittest discover -s tools -v

No network, browser or third-party packages are needed.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_portfolio  # noqa: E402
import portfolio  # noqa: E402

REAL = portfolio.load()
README_TEXT = portfolio.read_text(portfolio.README)


def project(repo: str) -> dict:
    return next(p for p in REAL["projects"] if p["repo"] == repo)


def mutated(repo: str, **changes) -> dict:
    data = copy.deepcopy(REAL)
    next(p for p in data["projects"] if p["repo"] == repo).update(changes)
    return data


class RealManifest(unittest.TestCase):
    def test_is_valid(self):
        self.assertEqual(portfolio.validate(REAL), [])

    def test_readme_matches_generator_exactly(self):
        self.assertEqual(portfolio.render_readme(README_TEXT, REAL), README_TEXT)

    def test_no_ownership_fields(self):
        self.assertFalse({"team", "owner", "owners"} & {k for p in REAL["projects"] for k in p})


class LineEndings(unittest.TestCase):
    def test_lf_and_crlf_are_stable(self):
        lf = README_TEXT.replace("\r\n", "\n")
        crlf = lf.replace("\n", "\r\n")
        self.assertEqual(portfolio.render_readme(lf, REAL), lf)
        self.assertEqual(portfolio.render_readme(crlf, REAL), crlf)

    def test_handwritten_lines_keep_their_own_endings(self):
        lf = README_TEXT.replace("\r\n", "\n")
        mixed = "first line\r\n" + lf  # one CRLF line in an otherwise LF file
        out = portfolio.render_readme(mixed, REAL)
        self.assertEqual(out, mixed)
        self.assertEqual(out.count("\r\n"), 1)

    def test_changed_manifest_updates_only_the_blocks(self):
        data = mutated("perigee-asteroid-tracker", pitch="A new pitch.")
        out = portfolio.render_readme(README_TEXT, data)
        self.assertIn("A new pitch.", out)
        head = README_TEXT.split("<!-- portfolio:begin featured -->")[0]
        self.assertTrue(out.startswith(head))


class Markers(unittest.TestCase):
    def render(self, text):
        return portfolio.render_readme(text, REAL)

    def test_duplicate_begin_is_refused(self):
        bad = README_TEXT.replace("<!-- portfolio:begin analysis -->",
                                  "<!-- portfolio:begin analysis -->\nhand-written\n<!-- portfolio:begin analysis -->", 1)
        with self.assertRaises(ValueError):
            self.render(bad)

    def test_end_before_begin_is_refused(self):
        bad = "<!-- portfolio:end featured -->\n" + README_TEXT
        with self.assertRaises(ValueError):
            self.render(bad)

    def test_missing_block_is_refused(self):
        bad = README_TEXT.replace("<!-- portfolio:begin software -->", "")
        with self.assertRaises(ValueError):
            self.render(bad)

    def test_unknown_marker_is_refused(self):
        with self.assertRaises(ValueError):
            self.render(README_TEXT + "\n<!-- portfolio:begin extra -->\n")

    def test_empty_block_is_filled(self):
        empty = "<!-- portfolio:begin featured -->\n<!-- portfolio:end featured -->\n"
        text = (empty + "<!-- portfolio:begin software -->\nx\n<!-- portfolio:end software -->\n"
                "<!-- portfolio:begin analysis -->\nx\n<!-- portfolio:end analysis -->\n")
        self.assertIn("**Featured:**", self.render(text))


class Validation(unittest.TestCase):
    def problems(self, data):
        return portfolio.validate(data)

    def test_rejections(self):
        cases = {
            "team field": mutated("string-lab", team="web"),
            "checks without release": mutated("string-lab", checks=["links", "site"]),
            "site check without live url": mutated("data-workbench", checks=["release", "links", "site"]),
            "nested checks": mutated("string-lab", checks=[["release"]]),
            "unknown placeholder": mutated("string-lab", pitch="See {nope}."),
            "live placeholder with no live url": mutated("data-workbench", pitch="Go to {live}."),
            "version with leading v": mutated("string-lab", version="v1.0.1"),
            "numeric label": mutated("string-lab", links=[[{"label": 5, "url": "{live}"}]]),
            "non-boolean bold": mutated("string-lab", links=[[{"label": "Live", "url": "{live}", "bold": {"team": "web"}}]]),
            "absolute screenshot name": mutated("string-lab", card={"shot": "/etc/hosts", "shot_version": "1.0.1", "host": "h"}),
            "parent-folder screenshot name": mutated("string-lab", card={"shot": "../x", "shot_version": "1.0.1", "host": "h"}),
            "repeated screenshot name": mutated("string-lab", card={"shot": "perigee", "shot_version": "1.5.0", "host": "h"}),
            "featured order gap": mutated("string-lab", featured={"order": 5, "label": "x", "url": "{live}"}),
            "http live url": mutated("string-lab", live="http://example.com/"),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertTrue(self.problems(data), f"{name} was accepted")

    def test_top_level_problems(self):
        about = copy.deepcopy(REAL)
        about["about"] = {"team": "web"}
        extra = copy.deepcopy(REAL)
        extra["teams"] = {}
        for data in (about, extra, {"owner": "x"}, [], {"owner": "x", "projects": []}):
            with self.subTest(data=str(data)[:40]):
                self.assertTrue(self.problems(data))

    def test_duplicate_repo(self):
        data = copy.deepcopy(REAL)
        data["projects"].append(copy.deepcopy(data["projects"][0]))
        self.assertTrue(any("listed twice" in p for p in self.problems(data)))


class Escaping(unittest.TestCase):
    def test_quotes_and_markup_in_card_text(self):
        data = mutated("string-lab", title='Lab "one" <b>', pitch="Fish & chips <script>")
        html = portfolio.card_html(next(p for p in data["projects"] if p["repo"] == "string-lab"), data["owner"])
        self.assertIn("alt=\"Lab &quot;one&quot; &lt;b&gt; screenshot\"", html)
        self.assertIn("Fish &amp; chips &lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_featured_link_with_brackets_and_parentheses(self):
        feat = {"order": 1, "label": "A [b] c", "url": "https://example.com/a (b)"}
        data = mutated("data-workbench", featured=feat)
        for p in data["projects"]:
            if p["repo"] != "data-workbench":
                p["featured"] = None
        line = portfolio.featured_line(data)
        self.assertIn(r"[A \[b\] c](https://example.com/a%20%28b%29)", line)


class AttributionAndBlocklist(unittest.TestCase):
    OWNER = "ZakJRademacher"

    def log(self, message, name="Zak Rademacher", email="214904468+ZakJRademacher@users.noreply.github.com"):
        return f"abc1234\x1f{name}\x1f{email}\x1f{name}\x1f{email}\x1f{message}\x1e"

    def test_clean_history(self):
        self.assertEqual(check_portfolio.history_problems(self.log("Fix the thing"), self.OWNER), [])

    def test_ai_author_is_flagged(self):
        bad = self.log("x", name="Cl" + "aude", email="noreply@anth" + "ropic.com")
        self.assertTrue(check_portfolio.history_problems(bad, self.OWNER))

    def test_other_co_author_is_flagged(self):
        bad = self.log("Fix\n\nCo-authored-by: Some Assistant <noreply@example.com>")
        self.assertTrue(check_portfolio.history_problems(bad, self.OWNER))

    def test_bot_and_owner_co_authors_are_allowed(self):
        ok = self.log("Fix\n\nCo-authored-by: dependabot[bot] <1+dependabot[bot]@users.noreply.github.com>\n"
                      "Co-authored-by: Zak <214904468+ZakJRademacher@users.noreply.github.com>")
        self.assertEqual(check_portfolio.history_problems(ok, self.OWNER), [])

    def test_blocklist_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blocked.json"
            path.write_text(json.dumps(["Alpha", "beta"]), encoding="utf-8")
            self.assertEqual(check_portfolio.load_blocklist(path), (["alpha", "beta"], None))
            for bad in ({"alpha": 1}, ["ok", 3], ["  "], "alpha"):
                path.write_text(json.dumps(bad), encoding="utf-8")
                self.assertIsNotNone(check_portfolio.load_blocklist(path)[1], bad)
            path.write_text("not json", encoding="utf-8")
            self.assertIsNotNone(check_portfolio.load_blocklist(path)[1])


if __name__ == "__main__":
    unittest.main()
