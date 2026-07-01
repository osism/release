"""Grouped, narrated text rendering of drift findings.

Pure presentation, no I/O: groups actionable (non-allowlisted) DriftEntry items
and renders each group as a lead sentence (plugin.SUMMARY), the sorted item list,
a Fix line (plugin.REMEDIATION), and source path(s) (Refs).

Grouping key depends on the plugin's SHOW_VALUES attribute (default False):

  SHOW_VALUES=False (default, kolla plugins):
    key = (plugin, expected_src, found_src, summary, remediation)
    Renders a comma-joined image-name list; Refs shows both source paths.

  SHOW_VALUES=True (role_shadows, role_unpinned):
    key = (plugin, expected_src, summary, remediation)
    Renders one line per entry:
      - when expected is non-empty: ``<alias>_tag (found → expected)   <path>``
      - when expected is empty:     ``<alias>_tag (found, no release pin)   <path>``
    where <path> is found_src with its leading repo-name component stripped.
    Refs shows only expected_src (one line).

Block order follows the given `plugins` list. The orientation header precedes
the blocks. Returns [] when there are no actionable drifts. The caller owns
the stale-allowlist block and the summary.
"""

import textwrap

HEADER = "Checks follow a service's path: enabled → built → version-pinned → deployed."

_WIDTH = 76
_NAME_INDENT = "    "


def _short_path(found_src: str) -> str:
    """Strip the leading repo-name component from a found_src path."""
    return found_src.split("/", 1)[1] if "/" in found_src else found_src


def format_text(drifts, plugins, header=HEADER):
    """Render actionable (non-allowlisted) drifts as grouped, narrated lines."""
    actionable = [d for d in drifts if not d.allowlisted]
    if not actionable:
        return []

    by_name = {p.NAME: p for p in plugins}
    order = {p.NAME: i for i, p in enumerate(plugins)}

    groups = {}
    for d in actionable:
        show_values = getattr(by_name[d.plugin], "SHOW_VALUES", False)
        if show_values:
            key = (d.plugin, d.expected_src, d.summary, d.remediation)
        else:
            key = (d.plugin, d.expected_src, d.found_src, d.summary, d.remediation)
        groups.setdefault(key, []).append(d)

    def _sort_key(k):
        plugin_name, expected_src = k[0], k[1]
        show_values = getattr(by_name[plugin_name], "SHOW_VALUES", False)
        plug_ord = order.get(plugin_name, len(order))
        if show_values:
            # key = (plugin, expected_src, summary, remediation)
            return (plug_ord, expected_src, k[2] or "", k[3] or "")
        else:
            # key = (plugin, expected_src, found_src, summary, remediation)
            return (plug_ord, expected_src, k[2], k[3] or "", k[4] or "")

    out = [header, ""]
    for key in sorted(groups, key=_sort_key):
        plugin_name = key[0]
        expected_src = key[1]
        plugin = by_name[plugin_name]
        show_values = getattr(plugin, "SHOW_VALUES", False)
        found_src = None if show_values else key[2]
        out.extend(_format_group(plugin, expected_src, found_src, groups[key]))
    return out


def _format_group(plugin, expected_src, found_src, entries):
    """Render one group as text lines.

    found_src=None signals a SHOW_VALUES plugin: each entry renders as
    ``<alias>_tag (found → expected)   <role-relative-path>`` sorted by
    (path, alias). Refs shows only expected_src. Otherwise the current
    comma-joined names list and two-line Refs block are used.
    """
    # Within a group summary/remediation are uniform by key construction;
    # fall back to plugin-level constants when the entry carries None.
    first = entries[0]
    summary = first.summary if first.summary is not None else plugin.SUMMARY
    remediation = (
        first.remediation if first.remediation is not None else plugin.REMEDIATION
    )

    lead = f"{plugin.NAME} — {summary.format(n=len(entries))}"
    lines = list(
        textwrap.wrap(
            lead, width=_WIDTH, break_long_words=False, break_on_hyphens=False
        )
    )
    lines.append("")

    if found_src is None:  # SHOW_VALUES mode
        for d in sorted(entries, key=lambda d: (_short_path(d.found_src), d.alias)):
            short = _short_path(d.found_src)
            if d.expected:
                lines.append(
                    f"{_NAME_INDENT}{d.alias}_tag ({d.found} → {d.expected})   {short}"
                )
            else:
                lines.append(
                    f"{_NAME_INDENT}{d.alias}_tag ({d.found}, no release pin)   {short}"
                )
    else:
        names = sorted(d.image for d in entries)
        lines.extend(
            textwrap.wrap(
                ", ".join(names),
                width=_WIDTH,
                initial_indent=_NAME_INDENT,
                subsequent_indent=_NAME_INDENT,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )

    lines.append("")
    lines.extend(
        textwrap.wrap(
            f"Fix: {remediation}",
            width=_WIDTH,
            initial_indent="  ",
            subsequent_indent="       ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    )
    lines.append(f"  Refs: {expected_src}")
    if found_src is not None:
        lines.append(f"        {found_src}")
    lines.append("")
    return lines
