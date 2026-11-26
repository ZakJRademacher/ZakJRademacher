# Profile assets and the portfolio manifest

`portfolio.json` at the repo root lists the projects shown on the profile. The
Featured line and the project cards in `README.md` are generated from it, so a
version, link or description is edited in one place. Everything in `README.md`
outside the `<!-- portfolio:begin ... -->` / `<!-- portfolio:end ... -->` comments
is hand-written and is left alone.

| File | What it does |
| --- | --- |
| `portfolio.json` | The project list: the single source for cards and the Featured line. |
| `tools/build_assets.py` | Renders `assets/banner-*.png` and the cards from `tools/shots/`, then rewrites the README blocks. `--readme` rewrites only the blocks and needs no browser. |
| `tools/portfolio.py` | Loads and validates the manifest and renders the README blocks. |
| `tools/check_portfolio.py` | Read-only check against GitHub and the README. Exit code 1 on any error. |
| `tools/test_portfolio.py` | Unit tests for the above. Run `python -m unittest discover -s tools`; no network needed. |

## Manifest fields

The top level has `about`, `owner` (the GitHub user) and `projects`. Each project:

| Field | Meaning |
| --- | --- |
| `repo` | Repository name. It must be a public repo of `owner`. |
| `title` | Card heading and image alt text. |
| `category` | `software` (installed app) or `web` (hosted site or report). |
| `section` | README grouping: `software` or `analysis`. Cards appear in list order. |
| `live` | Public https URL of the site, or `null` when there is none. |
| `version` | Current version, such as `1.0.1`. It must equal the repo's latest GitHub Release. |
| `featured` | `null`, or `{order, label, url}` for the Featured line (orders run 1, 2, 3, ...). |
| `pitch` | First card paragraph. `**bold**` is allowed. |
| `proof` | Second card paragraph with the proof point, or `null` when the pitch already carries it. |
| `tags` | Technology tags under the text. |
| `card` | `{shot, shot_version, host}`: the screenshot in `tools/shots/` and the address shown in the card's window frame. |
| `links` | Rows of `{label, url, bold?}` under the tags. |
| `checks` | Checks that apply: always `release` and `links`, plus `site` exactly when there is a live URL. |

Placeholders work in `label`, `url`, `pitch` and `proof`: `{repo_url}`, `{live}`,
`{version}` and `{version_short}` (the version without its patch number).

Rules for this file, which is public:

- List only public portfolio repos. Do not list products built to be sold, private
  apps or retired projects.
- Do not add who owns or maintains a repo. Unknown fields, including `team` and
  `owner` on a project, fail validation.
- Keep the version in step with the latest Release; the check fails when it differs.

## Updating a project

1. Publish the project's Release, then set `version` in `portfolio.json`.
2. If its look changed, rebuild its cover in its own repo (`deliverables/portfolio-cover.png`,
   built by that repo's `presentation/build_cover.py`; the Magnificent Seven shot is
   its dashboard image, scaled to 1600 px wide). Save it here as
   `tools/shots/<slug>-<version>.webp`, delete the old shot and set `card.shot_version`.
3. Rebuild the cards, then delete the old card image.
4. Run the check.

```sh
pip install playwright pillow && playwright install chromium
python tools/build_assets.py --cards    # cards and README blocks; add nothing to rebuild the banner too
python tools/build_assets.py --readme   # README blocks only
python tools/check_portfolio.py         # add --offline to skip the GitHub checks
python -m unittest discover -s tools    # unit tests
```

Shots and cards are named for the project version they show:
`tools/shots/<slug>-<version>.webp` becomes `assets/card-<slug>-<version>.webp`, so a
new version gets a new file and GitHub's image cache never serves an old picture.
Set `CHROMIUM_PATH` to use an installed Chrome or Edge instead of Playwright's
Chromium. Fonts load from Google Fonts.

## What the check covers

- The manifest is well formed (`portfolio.validate`).
- Each repo is public and not archived, and its latest Release equals `version`.
- The README blocks match what the manifest generates, and each block's begin and end
  comments appear exactly once, in order.
- Each card image and screenshot exists.
- No file in the repo (working tree or staged, any text extension), and no commit on
  `HEAD` or a remote branch, carries AI-assistant attribution or a co-author
  trailer for anyone but the owner or a bot. Remote branches are read as last fetched.
- No name from the private block list (below) appears in a file or in published history.
- Warnings: a stem from the private stem list, an About homepage that differs from
  `live`, a card screenshot older than the current release, and unused card images.

## Private lists

If a `.portfolio` folder sits next to this repository (or `PORTFOLIO_PRIVATE_DIR`
points at one), the check reads two flat JSON lists of text from it. Neither list is
kept in this repository.

| File | A hit is | Meaning |
| --- | --- | --- |
| `blocked.json` | an error | Names that must not appear in this repo. This list is also what keeps for-sale or retired projects off the manifest, since GitHub cannot say which public repos belong on a portfolio. |
| `blocked-stems.json` | a warning | Loose stems. A hit is for a person to review, never a failure. |

Both lists are matched the same way, against every text file in the repo (working tree
and staged) and against published history (commit messages, author fields, file names
and every version of every file):

- Case-insensitive substring.
- A term that contains a space matches whole words only, and the space matches any run
  of white space, because some terms are ordinary English phrases.

Output names a list entry by its number (`entry #3` is the third item), never by its
text, so a report can be shared without repeating the names. To scan another clone's
history, such as a fresh clone of GitHub, run
`python tools/check_portfolio.py --history-of <path>`.
