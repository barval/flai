# tests/test_morph.py
"""Tests for Russian morphological analysis of room names."""

from app.morph import generate_room_name_forms


class TestGenerateRoomNameForms:
    """Test generate_room_name_forms() with all project room names."""

    def test_tamбур(self):
        """Masculine inanimate — nomn == accs, only 2 forms."""
        forms = generate_room_name_forms("тамбур")
        assert forms[0] == "тамбур"  # nomn
        assert "тамбуре" in forms     # loct
        assert len(forms) == 2        # accs deduplicated

    def test_prihozhaya(self):
        """Feminine noun on -ая — 3 distinct forms."""
        forms = generate_room_name_forms("прихожая")
        assert forms == ["прихожая", "прихожую", "прихожей"]

    def test_koridor(self):
        """Masculine inanimate — nomn == accs, only 2 forms."""
        forms = generate_room_name_forms("коридор")
        assert forms[0] == "коридор"
        assert "коридоре" in forms
        assert len(forms) == 2

    def test_spalnya(self):
        """Feminine noun on -ня — 3 distinct forms."""
        forms = generate_room_name_forms("спальня")
        assert forms == ["спальня", "спальню", "спальне"]

    def test_kabinet(self):
        """Masculine inanimate — nomn == accs, only 2 forms."""
        forms = generate_room_name_forms("кабинет")
        assert forms[0] == "кабинет"
        assert "кабинете" in forms
        assert len(forms) == 2

    def test_detskaya(self):
        """Adjective used as noun — must return feminine forms only."""
        forms = generate_room_name_forms("детская")
        assert forms[0] == "детская"   # nomn femn
        assert forms[1] == "детскую"   # accs femn
        assert forms[2] == "детской"   # loct femn
        # Must NOT contain masculine forms
        assert "детский" not in forms
        assert "детского" not in forms

    def test_gostinaya(self):
        """Feminine noun on -ая — 3 distinct forms."""
        forms = generate_room_name_forms("гостиная")
        assert forms == ["гостиная", "гостиную", "гостиной"]

    def test_kuhnya(self):
        """Feminine noun on -я — 3 distinct forms."""
        forms = generate_room_name_forms("кухня")
        assert forms == ["кухня", "кухню", "кухне"]

    def test_balkon(self):
        """Masculine inanimate — nomn == accs, only 2 forms."""
        forms = generate_room_name_forms("балкон")
        assert forms[0] == "балкон"
        assert "балконе" in forms
        assert len(forms) == 2


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_string(self):
        assert generate_room_name_forms("") == []

    def test_whitespace_only(self):
        assert generate_room_name_forms("   ") == []

    def test_unknown_word(self):
        """Unknown word returns the word itself."""
        forms = generate_room_name_forms("гараж")
        assert forms[0] == "гараж"

    def test_lowercase_normalization(self):
        """Input is normalized to lowercase."""
        forms = generate_room_name_forms("КУХНЯ")
        assert forms[0] == "кухня"

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace is stripped."""
        forms = generate_room_name_forms("  кухня  ")
        assert forms[0] == "кухня"


class TestFormOrder:
    """Verify canonical form order: nomn → accs → loct."""

    def test_order_for_feminine(self):
        """Feminine noun — 3 forms in correct order."""
        forms = generate_room_name_forms("спальня")
        # First form is always nominative
        assert forms[0] == "спальня"

    def test_order_for_masculine(self):
        """Masculine noun — 2 forms (nomn + loct, accs deduped)."""
        forms = generate_room_name_forms("балкон")
        assert forms[0] == "балкон"
        assert forms[1] == "балконе"
