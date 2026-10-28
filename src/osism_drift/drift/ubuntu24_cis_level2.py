"""ubuntu24_cis_level2 plugin: Level 2 toggles in osism/defaults vs the pinned role."""

import re

import yaml

from osism_drift import source
from osism_drift.model import DriftEntry

NAME = "ubuntu24_cis_level2"
SHOW_VALUES = True
ROLE_REPO = "UBUNTU24-CIS"
DEFAULTS_FILE = "all/099-ubuntu24-cis.yml"
DESCRIPTION = (
    "Detect UBUNTU24-CIS rules tagged level2-server and not level1-server at the "
    "release pin whose toggle is missing from the Level 2 block of defaults "
    f"{DEFAULTS_FILE}, and toggles in that block that are no longer Level 2. The "
    "role ignores ubtu24cis_level_2, so the block is what keeps Level 2 off."
)
INPUT_FILES = [
    ("release", "<release_version>/base.yml"),
    (ROLE_REPO, "tasks/section_*/cis_*.yml"),
    ("defaults", DEFAULTS_FILE),
]
SUMMARY = "{n} UBUNTU24-CIS Level 2 toggles out of step with the pinned role:"
REMEDIATION = (
    f"in defaults {DEFAULTS_FILE}, between '# BEGIN level2' and '# END level2', add "
    "'<toggle>: false' for each entry with expected=false and remove each entry "
    "with found=false."
)

_RULE = re.compile(r"([0-9]+(?:\.[0-9]+)+) \|")
_TOGGLE = re.compile(r"^(ubtu24cis_rule_[0-9_]+):\s*false\s*$")


def level2_toggles(task_docs) -> set:
    """Toggles of rules whose server tags are level2-server only."""
    seen = {}

    def walk(tasks):
        for t in tasks or []:
            if not isinstance(t, dict):
                continue
            m = _RULE.match(t.get("name", ""))
            tags = t.get("tags") or []
            if m and any(x.startswith("level") for x in tags):
                seen.setdefault(m.group(1), set()).update(
                    x for x in tags if x.startswith("level") and x.endswith("-server")
                )
            walk(t.get("block"))

    for doc in task_docs:
        walk(doc)
    return {
        "ubtu24cis_rule_" + k.replace(".", "_")
        for k, v in seen.items()
        if "level1-server" not in v
    }


def block_toggles(text: str) -> set:
    """Toggles set to false between the '# BEGIN level2' and '# END level2' markers."""
    found, inside = set(), False
    for line in text.splitlines():
        if line.startswith("# BEGIN level2"):
            inside = True
        elif line.startswith("# END level2"):
            inside = False
        elif inside and (m := _TOGGLE.match(line)):
            found.add(m.group(1))
    return found


def _task_docs(ref, config):
    docs = []
    for section in source.list_dir_at_ref(
        ROLE_REPO, "tasks", ref, config, dirs_only=True
    ):
        if not section.startswith("section_"):
            continue
        for name in source.list_dir_at_ref(ROLE_REPO, f"tasks/{section}", ref, config):
            if name.startswith("cis_") and name.endswith(".yml"):
                body = source.read_at_ref(
                    ROLE_REPO, f"tasks/{section}/{name}", ref, config
                )
                docs.append(yaml.safe_load(body))
    return docs


def run(config, allowlist, verbose: bool = False) -> list:
    base = yaml.safe_load(
        source.read("release", f"{config.release_version}/base.yml", config)
    )
    ref = (base.get("ansible_roles") or {}).get("ubuntu24_cis")
    if ref is None:
        return []
    expected = level2_toggles(_task_docs(str(ref), config))
    body = source.read_optional("defaults", DEFAULTS_FILE, config)
    found = block_toggles(body.decode()) if body is not None else set()

    expected_src = f"ansible-lockdown/UBUNTU24-CIS@{ref} (level2-server only)"
    found_src = f"defaults/{DEFAULTS_FILE}"
    rows = [(k, "false", "") for k in sorted(expected - found)]
    rows += [(k, "", "false") for k in sorted(found - expected)]
    drifts = []
    for key, exp, fnd in rows:
        d = DriftEntry(
            plugin=NAME,
            image="ubuntu24_cis",
            alias=key,
            expected=exp,
            found=fnd,
            expected_src=expected_src,
            found_src=found_src,
        )
        drifts.append(allowlist.apply(d))
    return drifts
