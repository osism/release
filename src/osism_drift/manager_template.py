"""Extract alias-to-release-key map from the generics manager Jinja2 template."""

import re

_ALIAS_RE = re.compile(
    r"^(\w+)_tag:\s*\"\{\{\s*versions\[['\"](\w+)['\"]\]\s*\}\}\"",
    re.MULTILINE,
)

_STREAM_RE = re.compile(
    r"^(\w+)_tag:\s*\"\{\{ '\{\{' \}\} \w+_version\|default",
    re.MULTILINE,
)


def extract_stream_resolved(template_bytes: bytes) -> set[str]:
    """Return aliases whose <alias>_tag resolves to a <name>_version release-stream variable.

    These tags resolve to a release-stream variable, not a docker_images pin.
    """
    text = template_bytes.decode("utf-8")
    return {m.group(1) for m in _STREAM_RE.finditer(text)}


def extract_alias_map(template_bytes: bytes) -> dict[str, str]:
    """Return {alias: release_key} from the generics manager template.

    Lines of the form
        <alias>_tag: "{{ versions['<release_key>'] }}"
    contribute one entry. Identity entries (alias == release_key) appear
    naturally. Lines that don't match the pattern (including the
    {{ '{{' }} escape sequences used to emit literal Jinja into the
    rendered output for rolling-release images) are skipped.
    """
    text = template_bytes.decode("utf-8")
    return {m.group(1): m.group(2) for m in _ALIAS_RE.finditer(text)}
