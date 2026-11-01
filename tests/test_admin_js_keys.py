"""Every key an admin script asks the translation map for must exist in it.

`t('Token')` reads `window.TRANSLATIONS` and falls back to the key itself, so a
missing entry shows English text in a Russian panel with no error anywhere. The
MCP tab shipped three of those (Token, Allowed tools, Tools) and the Model Hub
badge had the same defect before.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ADMIN_HTML = Path("app/templates/admin.html").read_text(encoding="utf-8")
# A map entry is `'Key': {{ _('Key')|tojson }}`.
KEY_RE = re.compile(r"^\s*'([^']+)':\s*\{\{", re.M)
# `t('Key')` only: createElement('td') ends with "t('td')" and is not a call.
CALL_RE = re.compile(r"(?<![A-Za-z_])t\('([^']+)'\)")


def test_every_admin_script_key_exists_in_the_translation_map():
    keys = set(KEY_RE.findall(ADMIN_HTML))
    missing: dict[str, list[str]] = {}
    for path in sorted(Path("app/static/js").glob("admin*.js")):
        used = set(CALL_RE.findall(path.read_text(encoding="utf-8")))
        gap = sorted(used - keys)
        if gap:
            missing[path.name] = gap
    assert not missing, f"t() calls with no TRANSLATIONS entry: {missing}"
