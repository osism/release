import importlib.util
import pathlib
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

# release-notes.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "release-notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", _SRC)
rn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rn)

ACS = "osism/ansible-collection-services"

CHANGELOG = f"""### Fixed
- Netbox: build netbox_init_users as a mapping ({ACS}#2178, contributed by Timon Schnell (@kingspeedy95))
- Something internal ({ACS}#2186)
- Paired work ({ACS}#2190, {ACS}@abc1234, contributed by @a, Jane Doe)
- Corporate author ({ACS}#2191, contributed by "Doe, Jane" (@jane), Bob (@bob))
"""


def test_external_contributions_reads_credited_entries_only():
    assert rn.external_contributions(CHANGELOG) == [
        ([f"{ACS}#2178"], [("Timon Schnell", "kingspeedy95")]),
        ([f"{ACS}#2190", f"{ACS}@abc1234"], [(None, "a"), ("Jane Doe", None)]),
        ([f"{ACS}#2191"], [("Doe, Jane", "jane"), ("Bob", "bob")]),
    ]


@pytest.mark.parametrize(
    "text, credits",
    [
        ("Timon Schnell (@kingspeedy95)", [("Timon Schnell", "kingspeedy95")]),
        ("@a, Jane Doe", [(None, "a"), ("Jane Doe", None)]),
        # A comma inside a quoted name does not split it
        ('"Doe, Jane" (@jane), @b', [("Doe, Jane", "jane"), (None, "b")]),
        ('"Doe, Jane", X', [("Doe, Jane", None), ("X", None)]),
        # A bare name next to a login credit stays two people
        ("Jane Doe, Bob (@bob)", [("Jane Doe", None), ("Bob", "bob")]),
        (r'"A \"B\" C" (@abc)', [('A "B" C', "abc")]),
    ],
)
def test_parse_credits(text, credits):
    assert rn.parse_credits(text) == credits


def test_parse_credits_keeps_an_unparseable_list_whole(capsys):
    assert rn.parse_credits('"unterminated, (@x)') == [('"unterminated, (@x)', None)]
    assert "Cannot parse" in capsys.readouterr().err


def test_credit_sentence_links_the_handles():
    assert rn.credit_sentence([("Timon Schnell", "kingspeedy95")]) == (
        "Contributed by Timon Schnell "
        "([@kingspeedy95](https://github.com/kingspeedy95))."
    )
    assert rn.credit_sentence([(None, "a"), ("Jane Doe", None), ("B", "b-2")]) == (
        "Contributed by [@a](https://github.com/a), Jane Doe and B "
        "([@b-2](https://github.com/b-2))."
    )


def test_credit_sentence_keeps_a_comma_name_quoted():
    assert rn.credit_sentence([("Doe, Jane", "jane"), ("Bob", None)]) == (
        'Contributed by "Doe, Jane" ([@jane](https://github.com/jane)) and Bob.'
    )


def _sanitize(tmp_path, monkeypatch, capsys, body, input_text):
    input_file = tmp_path / "input.md"
    input_file.write_text(input_text)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(read=lambda: body))
    rn.cmd_sanitize(SimpleNamespace(input=str(input_file)))
    return capsys.readouterr().out


INPUT = f"""# Input

{rn.CONTRIBUTORS_HEADING}

Intro.

- {ACS}#2178: Contributed by Timon Schnell ([@kingspeedy95](https://github.com/kingspeedy95)).

## OSISM kolla defaults (reference)

- not-a-credit: Something.
"""


def test_sanitize_keeps_a_body_that_carries_the_credit(tmp_path, monkeypatch, capsys):
    body = (
        "### Notable changes\n\n* NetBox works again. Contributed by Timon "
        "Schnell ([@kingspeedy95](https://github.com/kingspeedy95)).\n"
    )
    assert _sanitize(tmp_path, monkeypatch, capsys, body, INPUT) == body


def test_sanitize_appends_a_dropped_credit(tmp_path, monkeypatch, capsys):
    body = "### Notable changes\n\n* NetBox works again.\n"
    out = _sanitize(tmp_path, monkeypatch, capsys, body, INPUT)
    assert out == (
        body
        + "\n### Contributors\n\n"
        + "* Contributed by Timon Schnell "
        + "([@kingspeedy95](https://github.com/kingspeedy95)): "
        + f"[{ACS}#2178](https://github.com/{ACS}/pull/2178)\n"
    )


def test_reference_link_of_a_commit():
    assert rn.reference_link(f"{ACS}@abc1234") == (
        f"[{ACS}@abc1234](https://github.com/{ACS}/commit/abc1234)"
    )


TIMON = (
    "Contributed by Timon Schnell "
    "([@kingspeedy95](https://github.com/kingspeedy95))."
)
TWO_CHANGES = f"""# Input

{rn.CONTRIBUTORS_HEADING}

- {ACS}#2178: {TIMON}
- {ACS}#2200: {TIMON}
"""
FALLBACK = (
    "\n### Contributors\n\n"
    + f"* {TIMON[:-1]}: [{ACS}#2178](https://github.com/{ACS}/pull/2178)\n"
    + f"* {TIMON[:-1]}: [{ACS}#2200](https://github.com/{ACS}/pull/2200)\n"
)


@pytest.mark.parametrize(
    "credited, fallback",
    [(0, FALLBACK), (1, FALLBACK), (2, "")],
)
def test_sanitize_counts_a_shared_credit_per_change(
    tmp_path, monkeypatch, capsys, credited, fallback
):
    # Two changes by the same person share one sentence. Unless it appears
    # once per change, which one lacks it is unknown: both get a line.
    items = [
        "* NetBox works again." + (f" {TIMON}" if credited >= 1 else ""),
        "* Another fix." + (f" {TIMON}" if credited >= 2 else ""),
    ]
    body = "### Notable changes\n\n" + "\n".join(items) + "\n"
    out = _sanitize(tmp_path, monkeypatch, capsys, body, TWO_CHANGES)
    assert out == body + fallback


def _quote_credit_name():
    script = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scripts"
        / "generate-changelog-input.sh"
    ).read_text()
    return re.search(r"^quote_credit_name\(\) \{\n.*?^\}\n", script, re.M | re.S)[0]


@pytest.mark.parametrize(
    "name, published",
    [
        ("Jane Doe", "Jane Doe"),
        ("Doe, Jane", '"Doe, Jane"'),
        ('A "B" C', r'"A \"B\" C"'),
    ],
)
def test_shell_quoting_round_trips_through_the_parser(name, published):
    out = subprocess.run(
        ["bash", "-c", _quote_credit_name() + 'quote_credit_name "$1"', "-", name],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")
    assert out == published
    assert rn.parse_credits(f"{out} (@x), @y") == [(name, "x"), (None, "y")]


def _script_lines(pattern):
    script = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scripts"
        / "generate-changelog-input.sh"
    ).read_text()
    return re.search(pattern, script, re.M | re.S)[0]


@pytest.mark.parametrize(
    "email, external",
    [
        ("someone@osism.tech", False),
        ("Someone@OSISM.DE", False),
        ("someone@example.com", True),
        ("someone@notosism.de", True),
        ("renovate[bot]@users.noreply.github.com", False),
    ],
)
def test_internal_email_domains_default(email, external):
    code = (
        _script_lines(r"^INTERNAL_EMAIL_DOMAINS=.*?\n")
        + _script_lines(r"^is_external_email\(\) \{\n.*?^\}\n")
        + 'is_external_email "$1"'
    )
    r = subprocess.run(["bash", "-c", code, "-", email], env={"PATH": "/usr/bin:/bin"})
    assert (r.returncode == 0) is external
