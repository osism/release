"""Shared CLI driver for drift-detection tools.

Parameterized by tool identity: description, config/allowlist paths,
plugin groups, and report headers.
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from osism_drift.config import (
    Allowlist,
    ConfigError,
    load_allowlist,
    load_config,
)
from osism_drift import demo, report, source
from osism_drift.source import SourceError


def run(
    argv,
    *,
    description,
    default_config,
    default_allowlist,
    plugin_groups,
    report_headers,
) -> int:
    """CLI entry point: run the configured plugins and report drift."""
    parser = _build_parser(
        plugin_groups, description, default_config, default_allowlist
    )
    args = parser.parse_args(argv)
    plugins = _candidate_plugins(args.group, plugin_groups)
    if args.demo:
        drifts = demo.build_demo_drifts(plugins)
        _emit(args, drifts, plugins, drifts, [], [], plugin_groups, report_headers)
        return 0
    try:
        config, allowlist = _load_runtime(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    selected = [
        p
        for p in plugins
        if (args.plugin is None or p.NAME in args.plugin)
        and config.plugins.get(p.NAME) is not None
        and config.plugins[p.NAME].enabled
    ]

    repos = {repo for p in selected for repo, _ in p.INPUT_FILES}
    try:
        resolution = source.describe_resolution(repos, config)
    except SourceError as e:
        print(f"source error: {e}", file=sys.stderr)
        return 2
    if not args.quiet:
        print(
            f"Resolving sources ({len(config.base_dirs)} base dir(s)):", file=sys.stderr
        )
        for line in resolution:
            print(line, file=sys.stderr)
        # Remote reads go through the GitHub API, which is slow: a full run makes
        # dozens of requests over several minutes. Warn up front and stream one
        # line per request, so the wait looks like progress rather than a hang.
        if any(" remote " in line for line in resolution):
            print(
                "GitHub API reads can take several minutes; progress follows.",
                file=sys.stderr,
            )
            source.set_progress(lambda line: print(line, file=sys.stderr, flush=True))

    drifts = []
    try:
        for plugin in selected:
            drifts.extend(plugin.run(config, allowlist, verbose=args.verbose))
    except SourceError as e:
        print(f"source error: {e}", file=sys.stderr)
        return 2

    actionable = [d for d in drifts if not d.allowlisted]
    allowlisted = [d for d in drifts if d.allowlisted]
    ran = {p.NAME for p in selected}
    stale = allowlist.stale(drifts, ran)

    _emit(
        args,
        drifts,
        plugins,
        actionable,
        allowlisted,
        stale,
        plugin_groups,
        report_headers,
    )
    return 1 if (actionable or stale) else 0


def _candidate_plugins(group, plugin_groups):
    """Return selected candidate plugins in group order."""
    groups = plugin_groups if group == "all" else {group: plugin_groups[group]}
    plugins = []
    seen = set()
    for group_plugins in groups.values():
        for plugin in group_plugins:
            if plugin.NAME not in seen:
                plugins.append(plugin)
                seen.add(plugin.NAME)
    return plugins


def _build_parser(plugin_groups, description, default_config, default_allowlist):
    p = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_format_plugin_groups(plugin_groups) + "\n\n" + _exit_codes_help(),
    )
    p.add_argument(
        "--group",
        choices=tuple(plugin_groups) + ("all",),
        required=True,
        help="plugin group to run (required)",
    )
    p.add_argument(
        "--config",
        default=default_config,
        help=f"config file (default: {_display_default(default_config)})",
    )
    p.add_argument(
        "--allowlist",
        default=default_allowlist,
        help=f"allowlist file (default: {_display_default(default_allowlist)})",
    )
    p.add_argument(
        "--plugin",
        action="append",
        help="run only this plugin (repeatable); default: all enabled",
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument(
        "--no-allowlist",
        action="store_true",
        help="ignore allowlist; report everything",
    )
    p.add_argument(
        "--demo",
        action="store_true",
        help=(
            "print an illustrative report with synthetic findings for every "
            "plugin in the group; reads no repos, config, or allowlist"
        ),
    )
    p.add_argument(
        "--base-dir",
        action="append",
        metavar="DIR",
        help=(
            "local checkout root (repeatable); repos found by dir name, "
            "first match wins"
        ),
    )
    p.add_argument(
        "--remote-fallback",
        action="store_true",
        help=(
            "for repos not found under any --base-dir, fetch remotely instead "
            "of erroring"
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    return p


def _display_default(path) -> str:
    """Render a default file path for --help: relative to the current dir when
    it lives under it (e.g. 'src/drift-allowlist.yml'), else the absolute path.
    Beats a vague 'alongside script' — it points at the exact file to copy."""
    path = Path(path)
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _format_plugin_groups(plugin_groups) -> str:
    lines = ["Plugins by group:"]
    for group, plugins in plugin_groups.items():
        lines.append(f"  {group}: {', '.join(p.NAME for p in plugins)}")
    lines.append("")
    lines.append("See docs/check-drift-<group>.md for what each plugin reads.")
    return "\n".join(lines)


def _exit_codes_help() -> str:
    return (
        "Exit codes:\n"
        "  0   no actionable drift and no stale allowlist entries\n"
        "  1   actionable drift or stale allowlist entries found\n"
        "  2   input error (missing file, unparseable, bad config)"
    )


def _format_stale_text(stale) -> list:
    out = []
    if stale:
        out.append("STALE ALLOWLIST (entries that matched no drift):")
        for e in stale:
            extra = []
            if e.alias is not None:
                extra.append(f"alias={e.alias}")
            if e.found_src is not None:
                extra.append(f"found_src={e.found_src}")
            suffix = (" " + " ".join(extra)) if extra else ""
            out.append(f"  {e.plugin}: {e.image}{suffix} -- {e.reason}")
        out.append("")
    return out


def _load_runtime(args):
    """Build the resolved config and allowlist from parsed CLI args."""
    config = load_config(args.config)
    config = dataclasses.replace(
        config,
        base_dirs=tuple(args.base_dir or ()),
        remote_fallback=args.remote_fallback,
    )
    allowlist = (
        Allowlist(entries=()) if args.no_allowlist else load_allowlist(args.allowlist)
    )
    return config, allowlist


def _emit(
    args,
    drifts,
    plugins,
    actionable,
    allowlisted,
    stale,
    plugin_groups,
    report_headers,
):
    """Print the findings and stale-allowlist report in the chosen format."""
    if args.format == "json":
        for d in drifts if args.verbose else actionable:
            print(json.dumps(d.to_dict()))
        for e in stale:
            print(
                json.dumps(
                    {
                        "type": "stale_allowlist",
                        "plugin": e.plugin,
                        "image": e.image,
                        "alias": e.alias,
                        "found_src": e.found_src,
                        "reason": e.reason,
                    }
                )
            )
        return

    plugin_group = {
        plugin.NAME: group
        for group, group_plugins in plugin_groups.items()
        for plugin in group_plugins
    }
    for group, group_plugins in plugin_groups.items():
        selected_names = {p.NAME for p in plugins if plugin_group[p.NAME] == group}
        if not selected_names:
            continue
        group_drifts = [d for d in drifts if plugin_group[d.plugin] == group]
        selected_group_plugins = [p for p in group_plugins if p.NAME in selected_names]
        for line in report.format_text(
            group_drifts, selected_group_plugins, header=report_headers[group]
        ):
            print(line)

    for line in _format_stale_text(stale):
        print(line)
    if not args.quiet:
        plural = "entry" if len(stale) == 1 else "entries"
        print(
            f"Summary: {len(actionable)} to act on, {len(allowlisted)} "
            f"allowlisted, {len(stale)} stale allowlist {plural} "
            f"({len(drifts)} total)"
        )
