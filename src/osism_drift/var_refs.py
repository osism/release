"""Which variable names a file reads, for the defaults_orphan check.

Pure text functions, no I/O. A name is read when it appears as an identifier
token anywhere in a file, with two exceptions in YAML: the key of a mapping
line (a definition, or a role default declaring it) and a full-line comment.
Every other file type counts every token: a stray mention can hide a finding,
never invent one.

Names can also be read by pattern, through community.general.merge_variables.
Those calls are parsed here and matched the way the lookup matches them
(community.general plugins/lookup/merge_variables.py, _var_matches: `regex`,
the default, uses re.search; `prefix`/`suffix` use startswith/endswith).
"""

import re
from dataclasses import dataclass

YAML_SUFFIXES = (".yml", ".yaml")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_YAML_KEY = re.compile(
    r"""^\s*(?:-\s+)?["']?([A-Za-z_][A-Za-z0-9_]*)["']?\s*:(?:\s|$)"""
)
_YAML_COMMENT = re.compile(r"^\s*#")
_TOP_KEY = re.compile(r"""^(["']?)([A-Za-z_][A-Za-z0-9_]*)\1\s*:(?:\s|$)""", re.M)
_MERGE_NAME = re.compile(r"""(\\?['"])(?:community\.general\.)?merge_variables\1""")
_STRING = re.compile(r"""^(?:'([^']*)'|"([^"]*)")$""")
_KWARG = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", re.S)
_STANDARD_SHAPE = re.compile(r"\^([A-Za-z0-9_]+?)__\.\+\$")
_KINDS = ("regex", "prefix", "suffix")


def is_yaml(path: str) -> bool:
    """True for the file types whose mapping keys are definitions."""
    return path.endswith(YAML_SUFFIXES)


def top_level_keys(text: str) -> list[str]:
    """Top-level mapping keys of a YAML document, in order, without duplicates."""
    return list(dict.fromkeys(key for _quote, key in _TOP_KEY.findall(text)))


def code_text(path: str, text: str) -> str:
    """`text` without its full-line comments if `path` is YAML, else unchanged.

    The comment rule of reads(), for callers that scan text another way (the
    merge_variables calls): a commented-out lookup reads nothing.
    """
    if not is_yaml(path):
        return text
    return "\n".join(
        line for line in text.splitlines() if not _YAML_COMMENT.match(line)
    )


def reads(path: str, text: str) -> set[str]:
    """Every name `text` reads, by the rules in the module docstring."""
    yaml_file = is_yaml(path)
    out = set()
    for line in text.splitlines():
        if yaml_file and _YAML_COMMENT.match(line):
            continue
        tokens = _IDENT.findall(line)
        if yaml_file and tokens:
            key = _YAML_KEY.match(line)
            if key and tokens[0] == key.group(1):
                tokens = tokens[1:]
        out.update(tokens)
    return out


@dataclass(frozen=True)
class MergeCall:
    """One merge_variables call: its (kind, pattern) pairs, and whether every
    pattern could be read. An unresolved call may read any variable."""

    patterns: tuple[tuple[str, str], ...]
    resolved: bool


def _string(arg):
    m = _STRING.match(arg)
    if m is None:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


def _call_args(text, start):
    """Arguments after a lookup plugin name, up to the call's `)`.

    `start` is just past the quoted plugin name. Returns the stripped
    arguments that follow it, or None when the call never closes (truncated,
    or not a call at all).
    """
    args, cur, depth, quote, i = [], [], 0, None, start
    while i < len(text):
        c = text[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            if depth == 0:
                if c != ")":
                    return None
                args.append("".join(cur))
                # args[0] is the text between the name and the first comma.
                return [a.strip() for a in args[1:]]
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            args.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    return None


def merge_calls(text: str) -> list[MergeCall]:
    """Every merge_variables call in `text`, as MergeCall."""
    calls = []
    for m in _MERGE_NAME.finditer(text):
        args = _call_args(text, m.end())
        if args is None:
            calls.append(MergeCall((), False))
            continue
        kind, patterns, resolved = "regex", [], True
        for arg in args:
            if not arg:
                continue
            literal = _string(arg)
            if literal is not None:
                if "\\" in literal:
                    # Jinja escape decoding is not implemented: unresolved is
                    # the safe direction.
                    resolved = False
                else:
                    patterns.append(literal)
                continue
            kw = _KWARG.match(arg)
            if kw is not None:
                if kw.group(1) == "pattern_type":
                    value = _string(kw.group(2).strip())
                    if value in _KINDS:
                        kind = value
                    else:
                        resolved = False
                continue
            resolved = False
        if not patterns:
            resolved = False
        if kind == "regex":
            # An invalid regex fails the lookup in Ansible too. Keep the call
            # unresolved (advisory), and drop the pattern so the matcher never
            # sees it: re.search would raise re.error.
            valid = []
            for p in patterns:
                try:
                    re.compile(p)
                except re.error:
                    resolved = False
                else:
                    valid.append(p)
            patterns = valid
        calls.append(MergeCall(tuple((kind, p) for p in patterns), resolved))
    return calls


def pattern_matches(kind: str, pattern: str, name: str) -> bool:
    """Whether merge_variables with this (kind, pattern) collects `name`."""
    if kind == "prefix":
        return name.startswith(pattern)
    if kind == "suffix":
        return name.endswith(pattern)
    return re.search(pattern, name) is not None


def near_miss(name: str, pattern: str) -> str | None:
    """The name `name` was meant to be, if it misses `^<prefix>__.+$` by one `_`.

    Only that shape is recognised (it is the only one OSISM uses); any other
    pattern returns None.
    """
    m = _STANDARD_SHAPE.fullmatch(pattern)
    if m is None:
        return None
    prefix = m.group(1)
    if not name.startswith(prefix + "_") or name.startswith(prefix + "__"):
        return None
    rest = name[len(prefix) + 1 :]
    return f"{prefix}__{rest}" if rest else None
