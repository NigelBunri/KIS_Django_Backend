"""Script to populate the Bible reader models from the local JSON dataset."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load Bible translations from the JSON data into the Django models."
    )
    parser.add_argument(
        "--root",
        default="bible",
        help="Path relative to the project root that contains the language folders (default: %(default)s).",
    )
    parser.add_argument(
        "--language",
        "-l",
        action="append",
        dest="languages",
        help="Only import translations from the given language folder (pass multiple times).",
    )
    parser.add_argument(
        "--translation",
        "-t",
        action="append",
        dest="translations",
        help="Only import the named translation JSON file (omit the .json extension).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[1]
    data_root = project_root / args.root

    if not data_root.exists():
        raise SystemExit(f"Bible data root not found at {data_root}")

    from apps.bible.importers import import_bible_translations

    imported = import_bible_translations(root_dir=data_root, languages=args.languages, translations=args.translations)

    print(f"Imported {len(imported)} translation(s): {', '.join(imported)}")


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings.local"))
    import django  # pragma: no cover - script-time import

    django.setup()
    main()
