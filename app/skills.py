"""Skills: a folder that holds instructions the model loads only when it asks.

The format is the one Claude Code, opencode and pi use — ``SKILL.md`` with a
frontmatter block — so a skill written for FLAI works in those harnesses and one
written there works here. Three keys are understood: ``name``, ``description``
and ``allowed-tools``. Nothing else is a new dependency: the frontmatter is three
lines of ``key: value``, not a reason to ship a YAML parser.

Two rules shape the module:

* only the catalog (name + one-line description) is cheap enough to sit in every
  system prompt; a body is loaded as a tool result when the model activates it;
* a broken skill is skipped with a warning and never takes its siblings with it —
  a typo in one folder must not disable the others.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"
SKILL_FILE = "SKILL.md"
MAX_DESCRIPTION_CHARS = 1024
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CACHE_TTL_S = 60

# root -> (taken_at, skills)
_cache: dict[str, tuple[float, list[Skill]]] = {}


class SkillError(ValueError):
    """A file that is not a usable skill, with the reason for the log."""


@dataclass(frozen=True)
class Skill:
    """One skill, plus the localized variant when the folder ships one."""

    name: str
    description: str
    body: str
    allowed_tools: tuple[str, ...]
    path: str
    variants: dict[str, tuple[str, str]] = field(default_factory=dict)

    def for_lang(self, lang: str) -> tuple[str, str]:
        """(description, body) in the requested language, English otherwise."""
        return self.variants.get(lang, (self.description, self.body))


def reset_cache() -> None:
    """Drop the scan cache (tests, and after an edit to the skills folder)."""
    _cache.clear()


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        raise SkillError("no frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise SkillError("frontmatter is not closed")
    header = text[3:end]
    body = text[end + 4 :].lstrip("\n")
    fields: dict[str, str] = {}
    for line in header.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise SkillError(f"frontmatter line is not key: value: {line!r}")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields, body


def _read(path: Path) -> Skill:
    fields, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if not name:
        raise SkillError("no name")
    if not _NAME_RE.match(name):
        raise SkillError(f"name is not portable as a tool argument: {name!r}")
    if not description:
        raise SkillError("no description")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillError(f"description is longer than {MAX_DESCRIPTION_CHARS} characters")
    if not body.strip():
        raise SkillError("empty body")
    allowed = tuple(item.strip() for item in fields.get("allowed-tools", "").split(",") if item.strip())
    return Skill(name=name, description=description, body=body, allowed_tools=allowed, path=str(path))


def _load_skill(folder: Path) -> Skill:
    skill = _read(folder / SKILL_FILE)
    if folder.name != skill.name:
        raise SkillError(f"folder {folder.name!r} does not match the skill name {skill.name!r}")
    variants: dict[str, tuple[str, str]] = {}
    for variant in folder.glob("SKILL.*.md"):
        lang = variant.stem.split(".", 1)[1]
        try:
            localized = _read(variant)
        except SkillError as exc:
            logger.warning(f"Skill variant {variant} is ignored: {exc}")
            continue
        variants[lang] = (localized.description, localized.body)
    if variants:
        skill = Skill(
            name=skill.name,
            description=skill.description,
            body=skill.body,
            allowed_tools=skill.allowed_tools,
            path=skill.path,
            variants=variants,
        )
    return skill


def discover(root: Path | str | None = None, ttl_s: int = CACHE_TTL_S) -> list[Skill]:
    """Every usable skill under ``root``, cached for ``ttl_s`` seconds.

    Never raises: an unreadable folder or a broken file costs only that file.
    """
    base = Path(root) if root is not None else SKILLS_ROOT
    key = str(base.resolve()) if base.exists() else str(base)
    cached = _cache.get(key)
    if cached and (time.monotonic() - cached[0]) < ttl_s:
        return list(cached[1])
    skills: list[Skill] = []
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            if not folder.is_dir() or not (folder / SKILL_FILE).is_file():
                continue
            try:
                skills.append(_load_skill(folder))
            except SkillError as exc:
                logger.warning(f"Skill {folder} is ignored: {exc}")
            except OSError as exc:
                logger.warning(f"Skill {folder} could not be read: {exc}")
    _cache[key] = (time.monotonic(), skills)
    return list(skills)


def catalog(lang: str = "en", root: Path | str | None = None) -> str:
    """The lines that go into every system prompt: name and description only."""
    skills = discover(root)
    if not skills:
        return ""
    lines = [f"- `{skill.name}`: {skill.for_lang(lang)[0]}" for skill in skills]
    return "\n".join(lines)


def activate(name: str, lang: str = "en", root: Path | str | None = None) -> str | None:
    """The body of one skill, or None when the name is not in the catalog."""
    for skill in discover(root):
        if skill.name == name:
            return skill.for_lang(lang)[1]
    return None


def skill_tool_definition(root: Path | str | None = None) -> dict | None:
    """The one tool a skill catalog needs, or None when there is no catalog.

    The names go into the schema as an enum: a small model picks from a list far
    more reliably than it recalls a name, and a name outside the list is refused
    by the server before the tool even runs.
    """
    names = [skill.name for skill in discover(root)]
    if not names:
        return None
    return {
        "type": "function",
        "function": {
            "name": "activate_skill",
            "description": (
                "Load the full instructions of a skill listed in the system prompt. "
                "Call this before doing work the skill covers; the instructions arrive as the tool result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": names,
                        "description": "Skill name exactly as the catalog lists it",
                    }
                },
                "required": ["name"],
            },
        },
    }
