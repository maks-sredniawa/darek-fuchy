#!/usr/bin/env python3
"""Checks every fucha in fuchy/ the way Darek checks one at install, plus the words against Vosk's model.

Mirrors app/src/main/java/app/darek/fuchy/ in maks-sredniawa/darek (InstallCheck.kt, Clash.kt,
FuchaFile.kt, Fucha.kt); a rule changed there changes here.

Usage: python3 scripts/check.py --model <vosk-model-small-pl-0.22 dir> [--own <darek.json>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
FUCHY = ROOT / "fuchy"
OWN_URL = "https://raw.githubusercontent.com/maks-sredniawa/darek/main/app/src/main/fuchy/darek.json"

FORMAT = 1
LANGUAGE = "pl"
OWN_ID = "darek"
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
CANCEL_WORDS = ("stop", "anuluj")
PERSONAS = ("darek", "daria")
NEAR = 2
INT_MIN, INT_MAX = -(2**31), 2**31 - 1
HOST = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")


@dataclass(frozen=True)
class Action:
    needs: str | None
    own_only: bool = False
    reply: bool = True


ACTIONS = {
    "speak": Action(needs=None),
    "call": Action(needs="call", own_only=True),
    "weather": Action(needs="internet", reply=False),
    "update_fuchy": Action(needs=None, own_only=True, reply=False),
}
PERMISSIONS = ("call", "internet")
SENT_VALUES = ("chosen_city",)


@dataclass(frozen=True)
class Problem:
    kind: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class Command:
    fucha: str
    id: str
    phrase: str
    decoys: tuple[str, ...] = ()


@dataclass
class Fucha:
    source: dict
    commands: list[Command] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.source["id"]

    @property
    def decoys(self) -> list[str]:
        return list(dict.fromkeys(d for c in self.commands for d in c.decoys))


class Unreadable(Exception):
    pass


def _str(value) -> bool:
    return isinstance(value, str)


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and INT_MIN <= value <= INT_MAX


def _strings(value) -> bool:
    return isinstance(value, list) and all(_str(v) for v in value)


FUCHA_FIELDS = {
    "format": (True, _int),
    "id": (True, _str),
    "version": (True, _int),
    "language": (True, _str),
    "name": (True, _str),
    "description": (True, _str),
    "author": (True, _str),
    "profanity": (True, lambda v: isinstance(v, bool)),
    "permissions": (False, _strings),
    "addresses": (False, _strings),
    "sends": (False, _strings),
    "commands": (True, lambda v: isinstance(v, list)),
}
COMMAND_FIELDS = {
    "id": (True, _str),
    "phrase": (True, _str),
    "action": (True, _str),
    "reply": (False, lambda v: v is None or _str(v)),
    "reply_feminine": (False, lambda v: v is None or _str(v)),
    "decoys": (False, _strings),
}


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    duplicated = sorted({k for k in keys if keys.count(k) > 1})
    if duplicated:
        raise Unreadable(f"duplicate keys {duplicated}")
    return dict(pairs)


def _shape(obj, fields: dict, where: str) -> None:
    if not isinstance(obj, dict):
        raise Unreadable(f"{where} is not a JSON object")
    unknown = sorted(set(obj) - set(fields))
    if unknown:
        raise Unreadable(f"{where} has unknown keys {unknown}")
    for name, (required, valid) in fields.items():
        if name not in obj:
            if required:
                raise Unreadable(f"{where} lacks {name!r}")
        elif not valid(obj[name]):
            raise Unreadable(f"{where} has a {name!r} of the wrong type")


def parse(data: bytes) -> tuple[dict | None, list[Problem]]:
    """The fucha's own JSON, as FuchaFile.kt reads it: the format before the rest, strictly typed, no unknown keys."""
    try:
        tree = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
        if not isinstance(tree, dict):
            raise Unreadable("not a JSON object")
        fmt = tree.get("format")
        if not _int(fmt) or fmt < FORMAT:
            raise Unreadable("no readable 'format'")
        if fmt > FORMAT:
            return None, [Problem("newer_format", f"format {fmt} needs a newer Darek (this check reads {FORMAT})")]
        _shape(tree, FUCHA_FIELDS, "the fucha")
        for i, command in enumerate(tree["commands"]):
            _shape(command, COMMAND_FIELDS, f"command {i}")
        return tree, []
    except UnicodeDecodeError:
        return None, [Problem("unreadable", "unreadable: not UTF-8")]
    except json.JSONDecodeError as e:
        return None, [Problem("unreadable", f"unreadable: {e}")]
    except Unreadable as e:
        return None, [Problem("unreadable", f"unreadable: {e}")]


_JAVA_WHITESPACE = " \t\n\x0b\x0c\r"


def words_of(phrase: str) -> list[str]:
    """Clash.kt's wordsOf: lower case, anything but letters, digits and ASCII whitespace dropped."""
    kept = "".join(
        c for c in phrase.lower() if c in _JAVA_WHITESPACE or unicodedata.category(c)[0] in ("L", "N")
    )
    return [w for w in re.split(f"[{re.escape(_JAVA_WHITESPACE)}]+", kept) if w]


def normalised(phrase: str) -> str:
    return " ".join(words_of(phrase))


def is_allowed_address(address: str) -> bool:
    """HTTPS to one exact host, a path prefix at most."""
    try:
        parts = urlsplit(address)
        port = parts.port
    except ValueError:
        return False
    if not address.isascii() or any(c.isspace() for c in address) or "\\" in address:
        return False
    return (
        address.split(":", 1)[0] == "https"
        and bool(parts.hostname)
        and HOST.fullmatch(parts.hostname) is not None
        and "@" not in parts.netloc
        and port is None
        and not parts.netloc.endswith(":")
        and "?" not in address
        and "#" not in address
    )


def refusals(source: dict, knows: Callable[[str], bool], bundled: bool = False) -> list[Problem]:
    """InstallCheck.kt's refusals, one Problem each."""
    out: list[Problem] = []
    fid = source["id"]
    if source["format"] != FORMAT:
        out.append(Problem("newer_format", f"format {source['format']} needs a newer Darek"))
    if source["language"] != LANGUAGE:
        out.append(Problem("wrong_language", f"language {source['language']!r}: only {LANGUAGE!r} is accepted"))
    if not ID.fullmatch(fid):
        out.append(Problem("bad_id", f"id {fid!r} is not [a-z0-9][a-z0-9-]{{0,63}}"))
    if fid == OWN_ID and not bundled:
        out.append(Problem("reserved_id", f"id {OWN_ID!r} is Darek's own"))
    permissions = source.get("permissions", [])
    declared = [p for p in permissions if p in PERMISSIONS]
    for p in permissions:
        if p not in PERMISSIONS:
            out.append(Problem("unknown_permission", f"permission {p!r} needs a newer Darek"))
    for s in source.get("sends", []):
        if s not in SENT_VALUES:
            out.append(Problem("unknown_sent_value", f"sent value {s!r} needs a newer Darek"))
    for a in source.get("addresses", []):
        if not is_allowed_address(a):
            out.append(Problem("bad_address", f"address {a!r}: HTTPS, one exact host, no user, port, query or fragment"))
    commands = source["commands"]
    for c in commands:
        action = ACTIONS.get(c["action"])
        if action is None:
            out.append(Problem("unknown_action", f"command {c['id']!r}: action {c['action']!r} needs a newer Darek"))
        else:
            if action.needs is not None and action.needs not in declared:
                out.append(Problem("undeclared", f"command {c['id']!r}: action {c['action']!r} needs the {action.needs!r} permission declared"))
            if action.own_only and not bundled:
                out.append(Problem("own_only", f"command {c['id']!r}: action {c['action']!r} is Darek's own only"))
            reply, feminine = c.get("reply"), c.get("reply_feminine")
            if action.reply and (reply is None or not reply.strip() or (feminine is not None and not feminine.strip())):
                out.append(Problem("no_reply", f"command {c['id']!r}: action {c['action']!r} needs a non-blank 'reply'"))
        words = words_of(c["phrase"])
        if not words:
            out.append(Problem("empty_phrase", f"command {c['id']!r}: the phrase has no words"))
        cancel = next((w for w in words if w in CANCEL_WORDS), None)
        if cancel is not None:
            out.append(Problem("cancel_word", f"command {c['id']!r}: the phrase holds the cancel word {cancel!r}"))
    every = [w for c in commands for w in words_of(c["phrase"]) + [x for d in c.get("decoys", []) for x in words_of(d)]]
    unknown = [w for w in dict.fromkeys(every) if not knows(w)]
    if unknown:
        out.append(Problem("unknown_words", f"words the model does not know: {', '.join(unknown)}"))
    return out


def to_fucha(source: dict) -> Fucha:
    return Fucha(
        source,
        [
            Command(source["id"], c["id"], normalised(c["phrase"]), tuple(normalised(d) for d in c.get("decoys", [])))
            for c in source["commands"]
        ],
    )


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a[i - 1] != b[j - 1]))
        previous = current
    return previous[len(b)]


def _near(a: str, b: str) -> bool:
    return 1 <= edit_distance(a, b) <= NEAR


def _holds(words: list[str], other: list[str]) -> bool:
    return bool(other) and any(words[s : s + len(other)] == other for s in range(len(words) - len(other) + 1))


def _name(c: Command) -> str:
    return f"{c.fucha}/{c.id} „{c.phrase}”"


def clashes(candidate: Fucha, enabled: Iterable[Command]) -> list[Problem]:
    """Clash.kt's clashes; in this repo every one fails, since a fucha published here must install switched on."""
    others = [c for c in enabled if c.fucha != candidate.id]
    out: list[Problem] = []
    for mine in candidate.commands:
        for other in others:
            a, b = words_of(mine.phrase), words_of(other.phrase)
            if _holds(a, b) or _holds(b, a):
                out.append(Problem("holds", f"{_name(mine)} and {_name(other)}: one holds the other, never both on"))
            elif edit_distance(" ".join(a), " ".join(b)) <= NEAR:
                out.append(Problem("sounds_like", f"{_name(mine)} is within {NEAR} letters of {_name(other)}"))
    words = list(dict.fromkeys([w for c in candidate.commands for w in words_of(c.phrase)] + [w for d in candidate.decoys for w in words_of(d)]))
    for word in words:
        for persona in PERSONAS:
            if _near(word, persona):
                out.append(Problem("near_name", f"{word!r} is within {NEAR} letters of the Persona name {persona!r}"))
    for word in words:
        for other in others:
            for theirs in words_of(other.phrase):
                if _near(word, theirs):
                    out.append(Problem("near_word", f"{word!r} is within {NEAR} letters of {theirs!r} in {_name(other)}"))
    return list(dict.fromkeys(out))


def own_fucha(path: str | None) -> Fucha:
    if path:
        data = Path(path).read_bytes()
    else:
        with urllib.request.urlopen(OWN_URL, timeout=30) as response:
            data = response.read()
    source, problems = parse(data)
    if problems:
        raise SystemExit(f"Darek's own fucha is unreadable: {problems[0]}")
    problems = refusals(source, knows=lambda _: True, bundled=True)
    if problems:
        raise SystemExit(f"Darek's own fucha is refused: {'; '.join(map(str, problems))}")
    return to_fucha(source)


def fucha_files(root: Path = FUCHY) -> list[Path]:
    return sorted(root.glob("*/fucha.json"))


def check_repo(knows: Callable[[str], bool], own: Fucha, root: Path = FUCHY) -> dict[str, list[Problem]]:
    """Every fucha's problems, keyed by its folder name; an empty list is a pass."""
    results: dict[str, list[Problem]] = {}
    read: dict[str, Fucha] = {}
    strays = sorted(p.name for p in root.iterdir() if not (p / "fucha.json").is_file()) if root.is_dir() else []
    for stray in strays:
        results[stray] = [Problem("stray", f"fuchy/{stray} is not a folder holding fucha.json")]
    for path in fucha_files(root):
        folder = path.parent.name
        source, problems = parse(path.read_bytes())
        if source is not None:
            problems = refusals(source, knows)
            if source["id"] != folder:
                problems.append(Problem("folder", f"id {source['id']!r} is not its folder's name {folder!r}"))
            read[folder] = to_fucha(source)
        results[folder] = problems
    for folder, fucha in read.items():
        enabled = own.commands + [c for other, f in read.items() if other != folder for c in f.commands]
        results[folder] += clashes(fucha, enabled)
    return results


def vosk_knows(model_dir: str) -> Callable[[str], bool]:
    from vosk import Model, SetLogLevel

    SetLogLevel(-1)
    model = Model(model_dir)
    return lambda word: model.vosk_model_find_word(word) >= 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, help="the unzipped vosk-model-small-pl-0.22 directory")
    parser.add_argument("--own", help=f"Darek's own darek.json (default: {OWN_URL})")
    args = parser.parse_args(argv)
    if not Path(args.model).is_dir():
        parser.error(f"no model at {args.model}")
    results = check_repo(vosk_knows(args.model), own_fucha(args.own))
    if not results:
        print("no fuchy found under fuchy/", file=sys.stderr)
        return 1
    failed = False
    for folder, problems in results.items():
        if problems:
            failed = True
            print(f"FAIL {folder}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"ok   {folder}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
