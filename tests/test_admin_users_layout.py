# tests/test_admin_users_layout.py
"""The Users tab keeps its columns narrow with two-line headers and cells."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_narrow_headers_are_split_into_two_lines():
    template = (ROOT / "app/templates/admin.html").read_text(encoding="utf-8")
    for msgid in ("Outgoing tokens", "Incoming tokens", "Documents (shared)", "Internet requests"):
        assert f"_('{msgid}').split(' ', 1)" in template, msgid
    for variable in ("outgoing_header", "incoming_header", "documents_header", "internet_header"):
        assert f"{{{{ {variable}[0] }}}}<br>{{{{ {variable}[1] }}}}" in template, variable


def test_storage_cell_is_two_lines():
    source = (ROOT / "app/static/js/admin.js").read_text(encoding="utf-8")
    assert "storage-limit" in source
    assert "`${storageUsedMb.toFixed(1)} ${t('of')}`" in source
    styles = (ROOT / "app/static/css/admin.css").read_text(encoding="utf-8")
    assert "#users-table .storage-used {\n    display: block;" in styles


def test_documents_cell_shows_the_shared_count():
    source = (ROOT / "app/static/js/admin.js").read_text(encoding="utf-8")
    assert "user.shared_documents_count" in source
    assert "`${user.documents_count || 0} (${sharedDocuments})`" in source


def test_admin_query_counts_shared_documents_not_readers():
    source = (ROOT / "app/routes/admin.py").read_text(encoding="utf-8")
    assert "COUNT(DISTINCT document_id) FROM document_shares" in source
    assert 'u_dict["shared_documents_count"]' in source


def test_mobile_storage_is_one_line():
    """The card layout has room, so the storage value must not break in two."""
    styles = (ROOT / "app/static/css/admin.css").read_text(encoding="utf-8")
    mobile = styles[styles.index("@media (max-width: 768px)"):]
    assert "#users-table .storage-cell .storage-used {\n        display: inline;" in mobile
    assert "#users-table .storage-limit {\n        display: inline-flex;" in mobile
