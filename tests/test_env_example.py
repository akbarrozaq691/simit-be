"""`.env.example` is the only instruction anyone gets when setting this up, and
nothing else notices when it drifts from Settings: a missing key becomes a
runtime surprise (uploads answering 500), and a stale key becomes a setting
someone believes they configured.
"""

import pathlib
import re

from src.settings import Settings

EXAMPLE = pathlib.Path(__file__).resolve().parent.parent / ".env.example"


def example_keys() -> set[str]:
    text = EXAMPLE.read_text(encoding="utf-8")
    return {m.group(1).lower() for m in re.finditer(r"^([A-Z_][A-Z0-9_]*)=", text, re.M)}


def test_example_exists():
    assert EXAMPLE.is_file()


def test_every_setting_is_documented():
    missing = sorted(set(Settings.model_fields) - example_keys())
    assert not missing, f".env.example is missing: {missing}"


def test_no_key_that_settings_would_ignore():
    unknown = sorted(example_keys() - set(Settings.model_fields))
    assert not unknown, f".env.example sets keys Settings does not read: {unknown}"


def test_no_byte_order_mark():
    """A BOM ahead of the first line rides along into a copied .env, where it
    corrupts the first key if the file is ever reordered."""
    assert not EXAMPLE.read_bytes().startswith(b"\xef\xbb\xbf")


def test_storage_secrets_are_left_blank():
    """The file is committed. A filled-in credential here is a leaked one."""
    text = EXAMPLE.read_text(encoding="utf-8")
    for key in ("STORAGE_ACCESS_KEY", "STORAGE_SECRET_KEY"):
        match = re.search(rf"^{key}=(.*)$", text, re.M)
        assert match, f"{key} missing"
        assert match.group(1).strip() == "", f"{key} has a value committed to git"


def test_storage_region_is_not_auto():
    """"auto" is a Cloudflare R2 convention. On the Ceph-backed endpoint this
    project uses, it makes signature verification fail — so the example must not
    hand out a default that cannot work here."""
    text = EXAMPLE.read_text(encoding="utf-8")
    match = re.search(r"^STORAGE_REGION=(.*)$", text, re.M)
    assert match and match.group(1).strip() != "auto"
