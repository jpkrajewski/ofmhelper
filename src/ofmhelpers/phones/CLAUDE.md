# phones

Static data behind /phones, as package JSON + Pydantic (`models.py`), read through `__init__.py` accessors.

- `catalog.json` — families (newest first) -> models (`size` class, `camera` layout, `lidar`, `palette`) + `palettes` (official colors, hex). New model/color = JSON entry; new camera shape = `CameraLayout` member + draw function in `render.py`.
- `render.py` — `render(model, finish)` -> SVG of the back: body at real mm size (SE/mini/6.1"/Max) on one canvas, finish color, camera layout. No photos anywhere (user decision 2026-10-21: no image files in the repo, generated drawings instead).
- `services.json` — login services, in display order (forms, export, and each card's logins: iCloud, Instagram, Gmail first, user decision 2026-10-21): `kind` (email/social/other), `icon` = Iconify name hotlinked from api.iconify.design (`logos:` full-color where it exists; `skill-icons:instagram` is the colored one, `logos:instagram-icon` is black; Simple Icons + `icon_color` for iCloud/X/Threads (their logos are one-color); Microsoft/Outlook/Yahoo are not in Simple Icons), `custom` (asks for provider name), `tracked_platform` (links to /accounts).
- Colors/storage sources: Apple specs; iPhone 17e (Mar 2026), 18 Pro (Sep 2026) per 9to5Mac/MacObserver.
