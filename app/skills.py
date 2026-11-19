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

import difflib
import logging
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"
# Bundled skills ship with the project and are tracked; installed ones are written
# by the admin panel into the git-ignored data tree, so a save never touches git.
BUNDLED_ROOT = SKILLS_ROOT
INSTALLED_ROOT = SKILLS_ROOT.parent / "data" / "skills"
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
    source: str = "bundled"
    shadowed: bool = False
    installed_path: str | None = None

    def for_lang(self, lang: str) -> tuple[str, str]:
        """(description, body) in the requested language, English otherwise."""
        return self.variants.get(lang, (self.description, self.body))


def reset_cache() -> None:
    """Drop the scan cache (tests, and after an edit to the skills folder)."""
    _cache.clear()


# Tools that exist in the catalogue but only when their service is running.
# A skill naming one of these is correct and stays correct when the service
# comes back, so it is a warning rather than a mistake.
OPTIONAL_TOOLS = frozenset(
    {
        "read_page",
        "list_rooms",
        "camera_snapshot",
        "net_dns",
        "net_whois",
        "net_ping",
        "net_trace",
        "net_ports",
        "net_http",
        "net_speed",
    }
)


@dataclass(frozen=True)
class ToolMark:
    """One name from ``allowed-tools``, and what this installation makes of it."""

    name: str
    status: str  # "offered" | "unavailable" | "typo" | "foreign"
    suggestion: str | None = None


def _near_miss(name: str, offered: set[str]) -> str | None:
    """The offered tool this name is a keystroke away from, if any."""
    normalized = name.strip().lower().replace("-", "_")
    if normalized in offered:
        return normalized
    matches = difflib.get_close_matches(normalized, sorted(offered), n=1, cutoff=0.8)
    return matches[0] if matches else None


def classify_allowed_tools(allowed: tuple[str, ...], offered: set[str]) -> list[ToolMark]:
    """Sort every name into offered / unavailable / typo / foreign.

    The fourth class is why uploading a Claude Code skill works: ``Read`` and
    ``Bash(git:*)`` are correct names from another catalogue, and rejecting the
    file over them would refuse exactly the files the panel exists to accept.
    """
    marks: list[ToolMark] = []
    for name in allowed:
        if name in offered:
            marks.append(ToolMark(name, "offered"))
        elif name in OPTIONAL_TOOLS:
            marks.append(ToolMark(name, "unavailable"))
        else:
            suggestion = _near_miss(name, offered)
            marks.append(ToolMark(name, "typo", suggestion) if suggestion else ToolMark(name, "foreign"))
    return marks


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


def parse_skill_text(text: str, path: str = "") -> Skill:
    """Validate a skill document exactly as a file on disk is validated.

    The panel edits text, so the same rules have to run on a string; keeping one
    implementation is what stops an editor from writing something the scanner
    would then reject in silence.
    """
    fields, body = _parse_frontmatter(text)
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
    return Skill(name=name, description=description, body=body, allowed_tools=allowed, path=path)


def _read(path: Path) -> Skill:
    return parse_skill_text(path.read_text(encoding="utf-8"), path=str(path))


def _load_skill(folder: Path, source: str = "bundled") -> Skill:
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
    return Skill(
        name=skill.name,
        description=skill.description,
        body=skill.body,
        allowed_tools=skill.allowed_tools,
        path=skill.path,
        variants=variants,
        source=source,
        installed_path=str(folder) if source == "installed" else None,
    )


def _source_of(base: Path) -> str:
    """Which of the two zones a root belongs to.

    Derived from the path rather than passed in, because ``discover`` caches by
    root: a caller that asked for the install root without naming the zone would
    otherwise poison the cache with skills labelled ``bundled``.
    """
    try:
        return "installed" if base.resolve() == INSTALLED_ROOT.resolve() else "bundled"
    except OSError:
        return "bundled"


def discover(root: Path | str | None = None, ttl_s: int = CACHE_TTL_S) -> list[Skill]:
    """Every usable skill under ``root``, cached for ``ttl_s`` seconds.

    Never raises: an unreadable folder or a broken file costs only that file.
    """
    base = Path(root) if root is not None else SKILLS_ROOT
    key = str(base.resolve()) if base.exists() else str(base)
    cached = _cache.get(key)
    if cached and (time.monotonic() - cached[0]) < ttl_s:
        return list(cached[1])
    source = _source_of(base)
    skills: list[Skill] = []
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            if not folder.is_dir() or not (folder / SKILL_FILE).is_file():
                continue
            try:
                skills.append(_load_skill(folder, source=source))
            except SkillError as exc:
                logger.warning(f"Skill {folder} is ignored: {exc}")
            except OSError as exc:
                logger.warning(f"Skill {folder} could not be read: {exc}")
    _cache[key] = (time.monotonic(), skills)
    return list(skills)


def discover_all(ttl_s: int = CACHE_TTL_S) -> list[Skill]:
    """Both zones, with a bundled skill winning a name an install also holds.

    An upgrade can create that collision after the fact: a release ships a skill
    whose name an administrator already installed. The installed copy is returned
    with ``shadowed=True`` instead of being dropped or becoming a second catalog
    line, so the panel can offer a rename and nothing disappears silently.
    """
    merged: dict[str, Skill] = {}
    shadowed: list[Skill] = []
    for root in (BUNDLED_ROOT, INSTALLED_ROOT):
        for skill in discover(root, ttl_s=ttl_s):
            if skill.name in merged:
                shadowed.append(replace(skill, shadowed=True))
            else:
                merged[skill.name] = skill
    return [merged[name] for name in sorted(merged)] + [s for s in sorted(shadowed, key=lambda s: s.name)]


def active_skills(ttl_s: int = CACHE_TTL_S) -> list[Skill]:
    """The skills the model may see: installed or bundled, enabled, not shadowed.

    A database that cannot be read means "nothing disabled" rather than an empty
    catalogue: a broken flag table should not silently remove every skill, and the
    rest of the application needs PostgreSQL to run at all.
    """
    try:
        from app.skilldb import disabled_names

        disabled = disabled_names()
    except Exception:  # noqa: BLE001 - see the docstring
        disabled = set()
    return [s for s in discover_all(ttl_s=ttl_s) if not s.shadowed and s.name not in disabled]


def catalog(
    lang: str = "en",
    root: Path | str | None = None,
    available: set[str] | None = None,
) -> str:
    """The lines that go into every system prompt: name and description only.

    ``available`` is the live tool catalogue. A skill whose every ``allowed-tools`` name is
    missing from it cannot do its work, so it is not offered: network diagnostics without its
    sidecar, a site-reading skill without the crawler. A skill that names no tools is never
    hidden — what it needs is decided by its instructions.
    """
    skills = active_skills() if root is None else discover(root)
    if available is not None:
        skills = [
            skill
            for skill in skills
            if not skill.allowed_tools or any(name in available for name in skill.allowed_tools)
        ]
    if not skills:
        return ""
    lines = [f"- `{skill.name}`: {skill.for_lang(lang)[0]}" for skill in skills]
    return "\n".join(lines)


def activate(name: str, lang: str = "en", root: Path | str | None = None) -> str | None:
    """The body of one skill, or None when the name is not in the catalog."""
    for skill in active_skills() if root is None else discover(root):
        if skill.name == name:
            return skill.for_lang(lang)[1]
    return None


def skill_tool_definition(root: Path | str | None = None) -> dict | None:
    """The one tool a skill catalog needs, or None when there is no catalog.

    The names go into the schema as an enum: a small model picks from a list far
    more reliably than it recalls a name, and a name outside the list is refused
    by the server before the tool even runs.
    """
    found = active_skills() if root is None else discover(root)
    names = [skill.name for skill in found]
    if not names:
        return None
    return {
        "type": "function",
        "function": {
            "name": "activate_skill",
            "description": (
                "Load the instructions of a skill listed in the system prompt. Call this BEFORE answering "
                "or using any other tool when the request matches one of those skills; the instructions "
                "arrive as the tool result and take precedence over your usual approach."
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
