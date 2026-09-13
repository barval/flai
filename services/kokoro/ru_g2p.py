"""Russian G2P for Kokoro-82M fine-tuning.

Pipeline: text -> RUAccent (stress + yo restoration + homograph resolution)
       -> combining-acute stress marks -> orthoepic respelling
       -> espeak-ng via misaki (acute-aware ru_dict, see scripts/build_espeak_data.sh)
       -> IPA normalization into Kokoro's 114-symbol vocab.

espeak-ng's stock Homebrew/loader ru_dict ignores U+0301; the project-local
espeak-data/ dir is recompiled from master dictsource where vowel+acute rules
exist. With every word acute-marked, dictionary lookup is bypassed and the
rules path derives stress AND vowel reduction from our marks.
"""

import json
import re
from pathlib import Path

# Standalone-release path resolution: assets sit NEXT TO this file, not one level
# up as in the source tree. Getting this wrong is silent and severe: espeak-ng's
# stock Russian data IGNORES combining-acute stress marks, so a wrong path does
# not error, it just produces worse stress than doing nothing -- which is the
# single most important thing this frontend exists to get right.
_PROJECT = Path(__file__).resolve().parent

ACUTE = "́"

# espeak IPA output -> Kokoro vocab. Discovered empirically; ru espeak never
# emits the doc-claimed ʐ/ʑ for ж/щ (it uses ʒ/ɕ), but does emit these:
NORMALIZE = {
    'u"': "u",  # espeak-internal centralized u leaks as literal u+quote (чу́вство)
    "ɭ": "l",  # espeak's hard л
    "ɵ": "o",  # stressed ё nucleus (jɵ -> jo)
    "ʑ": "ʒ",  # rare, cross-morpheme assimilation (шестьдесят)
    "ʐ": "ʒ",  # defensive; not observed for ru
    "ʧʲ": "ʧ",  # ч is inherently soft; drop redundant palatalization mark
}

# Word-final -ого/-его is pronounced -ово/-ево. espeak's rules handle the
# unstressed ending, but stressed -о́го (его́, большо́го, того́...) is an open
# class the rules miss, so we respell all of them uniformly, minus the
# adverbs/loans where г is really pronounced.
_OGO_BLACKLIST = {
    "много",
    "немного",
    "намного",
    "ненамного",
    "строго",
    "нестрого",
    "настрого",
    "дорого",
    "недорого",
    "полого",
    "убого",
    "лего",
    "диего",
    "ого",
    "огого",
}

# Orthoepic respellings espeak's rules don't cover. Keys/values are plain
# lowercase Russian applied to the acute-marked text (acutes preserved by
# substring position, so patterns avoid vowels where possible).
_RESPELL_SUBSTR = [
    ("солнц", "сонц"),  # со́лнце; солнечный has no ц and keeps л
    ("чувств", "чуств"),
    ("здравств", "здраств"),
    ("счастлив", "счаслив"),
    ("завистлив", "завислив"),
    ("участлив", "учаслив"),
    ("совестлив", "совеслив"),
]

# чн -> шн exceptions (conservative core set; modern Russian keeps чн elsewhere),
# plus three adverbs espeak mangles on its own.
#
# _OGO_BLACKLIST stops OUR -ого rule from firing on adverbs, but espeak's own
# ru_rules also apply the change and ignore our blacklist, so дорого came out as
# "dorovo". Respelling the ending to -га blocks espeak's rule and lands the
# correct [g]; post-tonic а and о both reduce to [ə], so the vowel is unaffected.
_RESPELL_WORD = {
    "дорого": "дорога",
    "недорого": "недорога",
    "настрого": "настрога",
    "конечно": "конешно",
    "скучно": "скушно",
    "скучный": "скушный",
    "нарочно": "нарошно",
    "яичница": "яишница",
    "скворечник": "скворешник",
    "девичник": "девишник",
    "двоечник": "двоешник",
    "троечник": "троешник",
    "прачечная": "прашечная",
    "горчичник": "горчишник",
    "пустячный": "пустяшный",
}

_WORD_RE = re.compile(r"[а-яё́]+", re.IGNORECASE)
_OGO_RE = re.compile(r"([а-яё́]*?[ое]́?)го(́?)$")

# --- Vowel reduction (v2) -------------------------------------------------
#
# espeak's ru_rules do not implement akanye: standard Russian merges unstressed
# а and о in pretonic position to a single [ɐ], but espeak keeps them apart and
# splits them inconsistently across `a` and `ʌ`. Measured over the 26,506 v1
# training lines, the SAME first-pretonic slot got `ʌ` 16,682 times and `a`
# 9,128 times, and post-tonic split `ʌ` 25,840 / `a` 7,840 / `o` 509. Because a
# native speaker produces one vowel there, that split is label noise the model
# cannot resolve, and under-reduced post-tonic vowels are a large part of why
# the output reads as non-native. See kokoro-ru-v2/G2P-FINDINGS.md.
#
# The mapping is positional, so it is derivable from the IPA string alone:
#   first pretonic, or absolute word-initial  -> ɐ  (moderate reduction)
#   anything else unstressed                  -> ə  (full reduction)
#   after a palatalized consonant             -> ɪ  (unstressed я/е)
# ɐ, ə, ɪ and ː are all already in Kokoro's 114-symbol vocab; ɐ and ː were
# entirely unused by v1.
_VOWELS = set("aɑoeiuyʌəɪɐɛɨ")
# Only the а/о-derived vowels reduce this way. `ɑ` and `o` are effectively the
# stressed realizations (29,310 of 29,430 `ɑ` and 24,195 of 24,824 `o` in the v1
# lists carried stress), but the unstressed stragglers are included so they are
# normalized rather than left as a third tier.
_REDUCIBLE = set("aɑoʌə")
_STRESS_MARKS = "ˈˌ"
_SCH_RE = re.compile("ɕ(?!ː)")


def _reduce_token(tok: str) -> str:
    chars = list(tok)
    vowels = [i for i, c in enumerate(chars) if c in _VOWELS]
    if not vowels:
        return tok

    # A stress mark sits before the syllable onset, so the vowel it marks is the
    # next vowel after it (`ˈjasnʌ` marks the `a`, not the `j`). Secondary stress
    # counts too: those vowels are not reduced either.
    #
    # Except that espeak puts a spurious secondary stress on the reflexive ending
    # -ться/-тся, emitting `ʦˌʌ` where Russian has an unstressed [ʦə]; that
    # accounted for every residual third-tier vowel in the v1 lists (1,577 of
    # them, all reflexive verbs). Russian secondary stress falls on an EARLIER
    # element of a compound, so a `ˌ` sitting after the primary `ˈ` is an
    # artifact and is ignored.
    primary = tok.find("ˈ")
    stressed = set()
    for i, c in enumerate(chars):
        if c not in _STRESS_MARKS:
            continue
        if c == "ˌ" and primary != -1 and i > primary:
            continue
        nxt = next((j for j in vowels if j > i), None)
        if nxt is not None:
            stressed.add(nxt)
    stressed_ordinals = sorted(vowels.index(i) for i in stressed)

    for ordinal, i in enumerate(vowels):
        if i in stressed or chars[i] not in _REDUCIBLE:
            continue
        if i > 0 and chars[i - 1] in "ʲj":
            chars[i] = "ɪ"
            continue
        # Nearest following stress, so compounds with two marks classify each
        # pretonic slot against the stress it actually leans on.
        next_stress = next((s for s in stressed_ordinals if s > ordinal), None)
        # Absolute-initial means the vowel itself opens the word (окно -> ɐknˈo),
        # not merely that it is the first vowel: голова is [ɡəlɐˈva], because its
        # о sits behind a consonant and is second-pretonic.
        absolute_initial = i == 0
        chars[i] = "ɐ" if next_stress == ordinal + 1 or absolute_initial else "ə"

    # Drop the artifact marks themselves, so no label carries a secondary stress
    # sitting on a vowel this function just reduced.
    if primary != -1:
        chars = [c for k, c in enumerate(chars) if not (c == "ˌ" and k > primary)]
    return "".join(chars)


def reduce_vowels(ps: str) -> str:
    """Apply positional Russian vowel reduction to an espeak IPA string."""
    return " ".join(_reduce_token(t) for t in ps.split(" "))


def lengthen_sch(ps: str) -> str:
    """щ (and сч/зч, which respell to it) is long [ɕː] in Russian; espeak emits
    a short [ɕ] and never emits `ː` at all."""
    return _SCH_RE.sub("ɕː", ps)


def _plus_to_acute(text: str) -> str:
    """RUAccent marks stress as '+' before the vowel; convert to U+0301 after it."""
    return re.sub(r"\+([аеёиоуыэюяАЕЁИОУЫЭЮЯ])", "\\1" + ACUTE, text)


def _strip_acute(word: str) -> str:
    return word.replace(ACUTE, "")


def _respell_word(word: str) -> str:
    bare = _strip_acute(word)
    if bare in _RESPELL_WORD:
        # Re-apply the acute at the same vowel index it had.
        target = _RESPELL_WORD[bare]
        idx = word.find(ACUTE)
        if idx > 0:
            vowel_n = sum(1 for c in word[:idx] if c in "аеёиоуыэюя")
            n = 0
            out = []
            for c in target:
                out.append(c)
                if c in "аеёиоуыэюя":
                    n += 1
                    if n == vowel_n:
                        out.append(ACUTE)
            return "".join(out)
        return target
    if bare not in _OGO_BLACKLIST:
        m = _OGO_RE.search(word)
        if m:
            word = word[: m.start()] + m.group(1) + "во" + m.group(2)
            bare = _strip_acute(word)
    if any(old in bare for old, _ in _RESPELL_SUBSTR):
        # Cluster edits only delete consonants, so the stressed-vowel ordinal
        # survives: edit the bare word, then re-seat the acute by vowel count.
        idx = word.find(ACUTE)
        vowel_n = sum(1 for c in word[:idx] if c in "аеёиоуыэюя") if idx > 0 else 0
        for old, new in _RESPELL_SUBSTR:
            bare = bare.replace(old, new)
        if vowel_n:
            n, out = 0, []
            for c in bare:
                out.append(c)
                if c in "аеёиоуыэюя":
                    n += 1
                    if n == vowel_n:
                        out.append(ACUTE)
            return "".join(out)
        return bare
    return word


def respell(text: str) -> str:
    return _WORD_RE.sub(lambda m: _respell_word(m.group(0)), text)


class _TokenTypeIdsShim:
    """transformers v5 dropped token_type_ids from default tokenizer output,
    but ruaccent's onnx exports still require it. Re-add zeros when missing."""

    def __init__(self, tokenizer):
        self._tokenizer = tokenizer

    def __call__(self, *args, **kwargs):
        import numpy as np

        enc = self._tokenizer(*args, **kwargs)
        if "token_type_ids" not in enc:
            enc["token_type_ids"] = np.zeros_like(enc["input_ids"])
        return enc

    def __getattr__(self, name):
        return getattr(self._tokenizer, name)


def _patch_token_type_ids(accent):
    for attr in ("accent_model", "omograph_model", "stress_usage_predictor"):
        model = getattr(accent, attr, None)
        session = getattr(model, "session", None)
        tokenizer = getattr(model, "tokenizer", None)
        if session is None or tokenizer is None:
            continue
        if "token_type_ids" in {i.name for i in session.get_inputs()}:
            model.tokenizer = _TokenTypeIdsShim(tokenizer)


class RuG2P:
    """Text -> (IPA phoneme string in Kokoro vocab, set of OOV symbols)."""

    def __init__(
        self,
        espeak_data: Path | str | None = None,
        vocab_path: Path | str | None = None,
        omograph_model_size: str = "turbo3.1",
        reduction: bool = True,
    ):
        # reduction=False reproduces the v1 label set, which is what the
        # v1-vs-v2 label-consistency comparison needs.
        from misaki import espeak as misaki_espeak  # sets loader lib+data paths
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        from ruaccent import RUAccent

        _data = Path(espeak_data or _PROJECT / "espeak-data")
        if not (_data / "ru_dict").exists():
            raise FileNotFoundError(
                f"acute-aware espeak data not found at {_data}. It ships with this "
                "repo as espeak-data/. Without it espeak silently ignores stress "
                "marks and Russian stress will be WORSE than baseline."
            )
        EspeakWrapper.set_data_path(str(_data))
        self._espeak = misaki_espeak.EspeakG2P(language="ru")
        self._accent = RUAccent()
        self._accent.load(omograph_model_size=omograph_model_size, use_dictionary=True, tiny_mode=False)
        _patch_token_type_ids(self._accent)
        cfg = json.loads(Path(vocab_path or _PROJECT / "kokoro-config.json").read_text())
        self.vocab = set(cfg["vocab"])
        self.reduction = reduction

    def accentuate(self, text: str) -> str:
        return _plus_to_acute(self._accent.process_all(text))

    def _from_marked(self, marked: str) -> tuple[str, set]:
        """Phonemize already-acute-marked, respell-ready text."""
        marked = respell(marked.lower())
        ps, _ = self._espeak(marked)
        for old, new in NORMALIZE.items():
            ps = ps.replace(old, new)
        if self.reduction:
            # After NORMALIZE, so the ɵ/ɭ/u" leaks are already folded into the
            # plain vowels the positional rules expect.
            ps = lengthen_sch(reduce_vowels(ps))
        oov = {c for c in ps if c not in self.vocab and c != " "}
        return ps, oov

    @staticmethod
    def _brackets(text: str) -> str:
        # espeak treats () as pause boundaries; fold other bracket types onto them
        # so bracketed asides phonemize instead of leaking literal OOV symbols.
        for ch in "[{":
            text = text.replace(ch, "(")
        for ch in "]}":
            text = text.replace(ch, ")")
        return text

    def phonemize(self, text: str) -> tuple[str, set]:
        return self._from_marked(self.accentuate(self._brackets(text)))

    def phonemize_accented(self, accent_text: str) -> tuple[str, set]:
        """Phonemize a corpus '+'-stress transcript (e.g. Dialogs accent_text),
        bypassing RUAccent. '+' precedes the stressed vowel."""
        return self._from_marked(_plus_to_acute(self._brackets(accent_text)))

    __call__ = phonemize
