#!/usr/bin/env python3
"""Report the per-release re-sync of osism/defaults' kolla mirror layer."""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from osism_drift import config as cfgmod  # noqa: E402
from osism_drift import enablement, mirror_sync, source  # noqa: E402
from osism_drift.mirror_sync import layers, effective, render, worktree  # noqa: E402
from osism_drift.mirror_sync.model import SyncOpts  # noqa: E402

here = Path(__file__).resolve().parent
_DEFAULT_BASE_REF = "origin/main"


def _parser():
    p = argparse.ArgumentParser(
        description="Report the re-sync of osism/defaults' kolla mirror for a release."
    )
    p.add_argument("--config", default=str(here / "drift-config.yml"))
    p.add_argument("--target", help="release to mirror; default newest supported")
    p.add_argument(
        "--defaults-dir", help="osism/defaults checkout (required in both modes)"
    )
    p.add_argument("--base-dir", action="append", default=[], dest="base_dirs")
    p.add_argument("--base-ref", default=_DEFAULT_BASE_REF)
    p.add_argument("--pin", help="upstream commit for --target")
    p.add_argument("--pins-from", help="report.json to replay every pin from")
    # --accept-upstream only. --retain / --retain-unverified arrive with --apply,
    # which owns the canonical-gate parser that gives them meaning; accepting
    # them here would let a run report "all dispositioned" without the checks.
    p.add_argument("--accept-upstream", action="append", default=[])
    p.add_argument("--worktree-path")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--format", choices=("text", "json"), default="text")
    return p


def main(argv=None):
    args = _parser().parse_args(argv if argv is not None else sys.argv[1:])

    if not args.defaults_dir:
        print("--defaults-dir is required", file=sys.stderr)
        return 3
    if args.apply:
        print(
            "--apply is not implemented yet; see the --apply plan. Report works.",
            file=sys.stderr,
        )
        return 3

    try:
        cfg = replace(cfgmod.load_config(args.config), base_dirs=tuple(args.base_dirs))
        target = args.target or sorted(enablement.release_range(cfg))[-1]

        pins, base_ref = {}, args.base_ref
        if args.pins_from:
            if args.base_ref != _DEFAULT_BASE_REF:
                print(
                    "--pins-from carries the defaults base commit; "
                    "--base-ref conflicts with it",
                    file=sys.stderr,
                )
                return 3
            payload = json.loads(Path(args.pins_from).read_text(encoding="utf-8"))
            pins, base_ref = effective.validate_pins_payload(
                payload, target, effective.effective_range(cfg, target)
            )

        opts = SyncOpts(
            target=target,
            base_ref=base_ref,
            pin=args.pin,
            pins=pins,
            accept_upstream=frozenset(args.accept_upstream),
            worktree_path=args.worktree_path,
            apply=False,
        )

        wt_path = args.worktree_path or worktree.default_path(args.defaults_dir, target)
        # Report mode always removes the worktree: keep=False.
        with worktree.session(args.defaults_dir, wt_path, base_ref) as (tree, base):
            plan = mirror_sync.build_plan(cfg, opts, tree, base)
            if args.format == "json":
                print(
                    json.dumps(
                        render.json_payload(plan, opts), indent=1, sort_keys=True
                    )
                )
            else:
                print(render.text(plan, opts), end="")
            return render.exit_code(plan, opts)
    except (
        worktree.WorktreeError,  # base: exists / create / cleanup all map to 3
        layers.LayerError,  # base: monolithic / empty-layer / unowned-010
        source.SourceError,
        cfgmod.ConfigError,
        ValueError,
        KeyError,
        OSError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
