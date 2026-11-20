# Profile assets

`build_assets.py` renders `assets/banner-{light,dark}.png` and the project cards
from the screenshots in `shots/`.

Shots and cards are named for the project version they show:
`shots/<slug>-<version>.webp` becomes `assets/card-<slug>-<version>.webp`. Each
shot is the project's portfolio cover (`deliverables/portfolio-cover.png`,
built by that repo's `presentation/build_cover.py`); the Magnificent Seven shot
is its dashboard image, scaled to 1600 px wide.

To update a project image:

1. Rebuild the cover in the project's repo and note its version.
2. Save it here as `shots/<slug>-<version>.webp` and delete the old shot.
3. Set the version in `PROJECTS` in `build_assets.py`.
4. Run the build, point `README.md` at the new card, and delete the old card.

```sh
pip install playwright pillow && playwright install chromium
python tools/build_assets.py --cards    # add nothing to rebuild the banner too
```

Set `CHROMIUM_PATH` to use an installed Chrome or Edge instead of Playwright's
Chromium. Fonts load from Google Fonts.
