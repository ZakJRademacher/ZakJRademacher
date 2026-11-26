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

    def test_list_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blocked.json"
            path.write_text(json.dumps(["Alpha", " beta "]), encoding="utf-8")
            self.assertEqual(check_portfolio.load_list(path), (["Alpha", "beta"], None))
            for bad in ({"alpha": 1}, ["ok", 3], ["  "], "alpha"):
                path.write_text(json.dumps(bad), encoding="utf-8")
                self.assertIsNotNone(check_portfolio.load_list(path)[1], bad)
            path.write_text("not json", encoding="utf-8")
            self.assertIsNotNone(check_portfolio.load_list(path)[1])


def terms(*entries):
    return check_portfolio.compile_terms(list(entries))


class MatchingRules(unittest.TestCase):
    def hit(self, entries, text):
        return [n for n, _, _ in check_portfolio.matches(terms(*entries), text)]

    def test_single_word_is_a_case_insensitive_substring(self):
        self.assertEqual(self.hit(["alpha"], "see ALPHAbet soup"), [1])
        self.assertEqual(self.hit(["alpha"], "nothing here"), [])

    def test_hyphenated_term_is_a_plain_substring(self):
        self.assertEqual(self.hit(["night-shift"], "the Night-Shifts list"), [1])

    def test_multi_word_term_needs_whole_words(self):
        self.assertEqual(self.hit(["blue lamp"], "The Blue Lamp opens"), [1])
        self.assertEqual(self.hit(["blue lamp"], "ultrablue lampshade"), [])
        self.assertEqual(self.hit(["blue lamp"], "my blue lamps"), [])
        self.assertEqual(self.hit(["red kite"], "no redkite here, no red kites either"), [])

    def test_spaces_match_any_run_of_white_space(self):
        self.assertEqual(self.hit(["blue lamp"], "blue \n  lamp"), [1])
        self.assertEqual(self.hit(["blue lamp"], "blue-lamp"), [])

    def test_entry_numbers_and_lines_are_reported(self):
        found = check_portfolio.matches(terms("zzz", "beta", "gamma"), "one\ntwo beta\nthree gamma")
        self.assertEqual([(n, line) for n, line, _ in found], [(2, 2), (3, 3)])

    def test_html_escaped_text_is_decoded_before_matching(self):
        self.assertEqual(self.hit(["fish & chips"], "Fish &amp; Chips"), [1])
        self.assertEqual(self.hit(["fish & chips"], "Fish &#38; Chips"), [1])
        self.assertEqual(self.hit(["fish & chips"], "Fish &amp;chips"), [])

    def test_regex_characters_are_literal(self):
        self.assertEqual(self.hit(["a.c"], "abc"), [])
        self.assertEqual(self.hit(["a.c"], "a.c"), [1])

    def test_snippet_masks_the_match(self):
        (_, _, snippet), = check_portfolio.matches(terms("consolid"), "we consolidated it", context=10)
        self.assertIn("<#1>", snippet)
        self.assertNotIn("consolid", snippet)


class PrivateLists(unittest.TestCase):
    def test_exact_hits_fail_and_stem_hits_warn_without_naming_the_term(self):
        texts = {"a.md": "uses Secretname here", "b.md": "a consolidated view", "c.md": "clean"}
        errs, warns = check_portfolio.scan_files(texts, terms("secretname"), terms("consolidat"))
        self.assertEqual(len(errs), 1)
        self.assertEqual(len(warns), 1)
        self.assertIn("a.md:1", errs[0])
        self.assertIn("entry #1", errs[0])
        self.assertIn("b.md:1", warns[0])
        self.assertIn("stem #1", warns[0])
        self.assertNotIn("secretname", (errs[0] + warns[0]).lower())
        self.assertNotIn("consolidat", errs[0].lower())

    def test_stem_alone_is_never_an_error(self):
        errs, warns = check_portfolio.scan_files({"b.md": "consolidating"}, [], terms("consolidat"))
        self.assertEqual((errs, len(warns)), ([], 1))

    def test_history_scan_covers_messages_old_versions_and_file_names(self):
        import subprocess

        def run(*args):
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                           check=True, capture_output=True)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run("init", "-q")
            (repo / "README.md").write_text("old text mentions Secretname\n", encoding="utf-8")
            run("add", "-A")
            run("commit", "-q", "-m", "Add readme about consolidation")
            (repo / "README.md").write_text("clean text\n", encoding="utf-8")
            (repo / "secretname-notes.txt").write_text("hello\n", encoding="utf-8")
            run("add", "-A")
            run("commit", "-q", "-m", "Mention secretname in the message")
            errs, warns = check_portfolio.scan_history(repo, terms("secretname"), terms("consolidat"))
        joined = " | ".join(errs)
        self.assertIn("1 file version(s) of README.md match entry #1", joined)
        self.assertIn("1 commit message(s) match entry #1", joined)
        self.assertIn("1 file name(s) of secretname-notes.txt match entry #1", joined)
        self.assertTrue(any("commit message" in w and "stem #1" in w for w in warns))
        self.assertNotIn("Secretname", joined)


if __name__ == "__main__":
    unittest.main()
