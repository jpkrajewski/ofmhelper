# phones

Static data behind /phones, as package JSON + Pydantic. Here, not in `features/phones/`, because `db/stores` and `schemas/devices.py` check against it (2026-11-11); the drawings stay in `features/phones/catalog/render.py`.

- New model/color = JSON entry in `catalog.json`; new camera shape = `CameraLayout` member + draw function in `features/phones/catalog/render.py`. No photos anywhere (user decision 2026-10-21: no image files in the repo, generated drawings instead).
- `services.json` — login services, in display order (forms, export, and each card's logins: iCloud, Instagram, Gmail first, user decision 2026-10-21): `icon` = Iconify name hotlinked from api.iconify.design (`logos:` full-color where it exists; `skill-icons:instagram` is the colored one, `logos:instagram-icon` is black; Simple Icons + `icon_color` for iCloud/X/Threads (their logos are one-color); Microsoft/Outlook/Yahoo are not in Simple Icons).
- Colors/storage sources: Apple specs; iPhone 17e (Mar 2026), 18 Pro (Sep 2026) per 9to5Mac/MacObserver.
