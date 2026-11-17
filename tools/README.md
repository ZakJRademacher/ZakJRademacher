# Profile assets

`build_assets.py` renders `assets/banner-{light,dark}.png` and `assets/card-*.webp`
from the screenshots in `shots/`. To update a project image, replace its file in
`shots/` and run:

```sh
pip install playwright pillow && playwright install chromium
python tools/build_assets.py
```
