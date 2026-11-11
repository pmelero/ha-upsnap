"""Keep the translation files consistent with each other and with the code."""

from __future__ import annotations

import json
from pathlib import Path
import re

COMPONENT = Path(__file__).parent.parent / "custom_components" / "upsnap"


def _load(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def _keys(data: dict, prefix: str = "") -> set[str]:
    keys = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        keys.add(path)
        if isinstance(value, dict):
            keys |= _keys(value, path)
    return keys


def test_strings_match_english() -> None:
    """strings.json and translations/en.json are the same file."""
    assert _load("strings.json") == _load("translations/en.json")


def test_translations_have_the_same_keys() -> None:
    """Every translation has exactly the keys of the English one."""
    english = _keys(_load("translations/en.json"))
    for path in (COMPONENT / "translations").glob("*.json"):
        assert _keys(json.loads(path.read_text(encoding="utf-8"))) == english, path.name


def test_exception_keys_are_translated() -> None:
    """Every translation_key raised in the code has an exception message."""
    code = "\n".join(path.read_text() for path in COMPONENT.glob("*.py"))
    used = set(
        re.findall(r'translation_domain=DOMAIN,\s*translation_key="([a-z_]+)"', code)
    )
    used |= set(re.findall(r'\bkey = "([a-z_]+)"', code))
    exceptions = set(_load("translations/en.json")["exceptions"])
    assert used, "no translation keys found"
    assert used <= exceptions
