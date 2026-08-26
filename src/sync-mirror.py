#!/usr/bin/env python3
"""Report the per-release re-sync of osism/defaults' kolla mirror layer."""

import argparse
import json
import shlex
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from osism_drift import config as cfgmod  # noqa: E402
from osism_drift import enablement, mirror_sync, source  # noqa: E402
from osism_drift.mirror_sync import (  # noqa: E402
    classify,
    commit as commit_mod,
    detail,
    effective,
    gate,
    layers,
    render,
    worktree,
    write,
)
from osism_drift.mirror_sync.model import SyncOpts  # noqa: E402

here = Path(__file__).resolve().parent
_DEFAULT_BASE_REF = "origin/main"


def _parser():
    p = argparse.ArgumentParser(
        description="Report the re-sync of osism/defaults' kolla mirror for a release.",
        epilog=(
            "exit codes:\n"
            "  0  nothing to do; the mirror already matches\n"
            "  1  changes ready to apply; re-run with --apply\n"
            "  2  operator action needed (a semantic key without a disposition,\n"
            "     or an existing 010 file this tool does not own)\n"
            "  3  refused; nothing was written\n"
            "\ntypical run:\n"
            "  tox -e sync-mirror -- --target 2026.1 \\\n"
            "      --defaults-dir ~/src/osism/defaults \\\n"
            "      --base-dir ~/src/osism --base-dir ~/src/openstack\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--config",
        default=str(here / "drift-config.yml"),
        help="drift config; supplies the release range and upstream branches",
    )
    p.add_argument("--target", help="release to mirror; default newest supported")
    p.add_argument(
        "--defaults-dir", help="osism/defaults checkout (required in both modes)"
    )
    p.add_argument(
        "--base-dir",
        action="append",
        default=[],
        dest="base_dirs",
        metavar="DIR",
        help="repeatable root holding checkouts; first match wins per repo",
    )
    p.add_argument(
        "--base-ref",
        default=_DEFAULT_BASE_REF,
        help="ref in --defaults-dir the throwaway worktree is built from "
        f"(default {_DEFAULT_BASE_REF}); point it at your own branch",
    )
    p.add_argument("--pin", help="upstream commit for --target")
    p.add_argument("--pins-from", help="report.json to replay every pin from")
    p.add_argument(
        "--accept-upstream",
        action="append",
        default=[],
        metavar="KEY",
        help="take the upstream value for KEY; repeatable",
    )
    p.add_argument(
        "--retain",
        action="append",
        default=[],
        metavar="KEY",
        help="keep the OSISM value for KEY; verified against the 099 gate",
    )
    p.add_argument(
        "--retain-unverified",
        action="append",
        default=[],
        metavar="KEY",
        help="keep the OSISM value for KEY when its gate exists but cannot "
        "be parsed; use only after reading the gate yourself",
    )
    p.add_argument(
        "--worktree-path",
        metavar="PATH",
        help="where to build the throwaway worktree (default: "
        "<defaults>.worktrees/sync-<target>/defaults)",
    )
    p.add_argument(
        "--report-file",
        metavar="PATH",
        help="full per-key values (default: ./sync-mirror-<target>.txt)",
    )
    p.add_argument(
        "--apply", action="store_true", help="write the plan into the worktree"
    )
    p.add_argument(
        "--commit",
        action="store_true",
        help="with --apply: branch the worktree and commit what was written",
    )
    p.add_argument(
        "--commit-branch",
        metavar="NAME",
        help="branch --commit creates (default: sync-mirror/<target>)",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="text for people, json for tooling and --pins-from",
    )
    return p


def main(argv=None):
    args = _parser().parse_args(argv if argv is not None else sys.argv[1:])

    if not args.defaults_dir:
        print("--defaults-dir is required", file=sys.stderr)
        return 3
    if (args.commit or args.commit_branch) and not args.apply:
        print(
            "--commit needs --apply: there is nothing to commit until the plan "
            "has been written",
            file=sys.stderr,
        )
        return 3
    disp = [set(args.accept_upstream), set(args.retain), set(args.retain_unverified)]
    for i, a in enumerate(disp):
        for b in disp[i + 1 :]:
            if a & b:
                print(
                    f"keys {sorted(a & b)} given more than one disposition; a key "
                    "takes exactly one disposition",
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
            retain=frozenset(args.retain),
            retain_unverified=frozenset(args.retain_unverified),
            worktree_path=args.worktree_path,
            apply=args.apply,
        )

        wt_path = args.worktree_path or worktree.default_path(args.defaults_dir, target)
        wrote = False
        with worktree.session(
            args.defaults_dir, wt_path, base_ref, keep=lambda: wrote
        ) as (tree, base):
            plan = mirror_sync.build_plan(cfg, opts, tree, base)
            older = effective.effective_range(cfg, target)[:-1]
            supply = classify.supply_info(cfg, tree)

            # Raises on a stale disposition key, a failed retention gate, or
            # --retain-unverified on a canonical gate. Reaches the handler below
            # as exit 3 carrying its own diagnostic.
            notes = gate.validate_dispositions(plan, opts, older, supply)

            # Written before any emit path, including the apply refusal below:
            # the values behind a refusal are exactly what the operator needs to
            # resolve it, so they must not depend on the run succeeding.
            report_path = Path(
                args.report_file or f"sync-mirror-{target}.txt"
            ).resolve()
            report_path.write_text(detail.text(plan, opts), encoding="utf-8")

            code = render.exit_code(plan, opts)
            applied = None
            result = None
            committed = None
            if args.apply:
                if code != 1:
                    # Refused. Nothing was written, so `wrote` stays False and
                    # the session removes the worktree on the way out. applied is
                    # explicitly False, not None: a consumer must be able to tell
                    # "an apply was attempted and did not happen" from "this was a
                    # report run", and null cannot express that.
                    _emit(
                        args.format,
                        plan,
                        opts,
                        notes,
                        applied=False,
                        code=code,
                        report_path=report_path,
                    )
                    return code

                def _started():
                    nonlocal wrote
                    wrote = True

                try:
                    result = write.apply_plan(plan, tree, on_first_write=_started)
                except Exception as exc:  # noqa: BLE001 -- re-raised or reported
                    if not wrote:
                        raise  # nothing written; the generic handler is right
                    # Something WAS written. The tree is kept deliberately, so the
                    # operator has to be told where it is and how to clear it --
                    # otherwise the next run refuses on an occupied path with a
                    # message pointing at the wrong cause.
                    print(
                        f"{exc}\n\n"
                        f"a partial write was left in {tree}\n"
                        "inspect it, then remove it with:\n"
                        f"  git -C {shlex.quote(str(args.defaults_dir))} "
                        f"worktree remove {shlex.quote(str(tree))} --force",
                        file=sys.stderr,
                    )
                    return 3
                applied = True
                if args.commit:
                    branch = args.commit_branch or commit_mod.default_branch(target)
                    sha = commit_mod.commit(
                        tree,
                        branch,
                        target,
                        render.commit_summary(plan, opts, notes),
                        list(result["written"]) + list(result["deleted"]),
                    )
                    committed = (branch, sha)

            _emit(
                args.format,
                plan,
                opts,
                notes,
                applied=applied,
                result=result,
                code=0 if applied else code,
                report_path=report_path,
                committed=committed,
                acceptance=(
                    render.acceptance_command(
                        tree,
                        args.base_dirs,
                        target=target,
                        newest=sorted(enablement.release_range(cfg))[-1],
                    )
                    if applied
                    else None
                ),
            )
            # A successful apply is 0. Returning the pre-apply status would report
            # 1 ("changes are ready to apply") for a run that just applied them.
            return 0 if applied else code
    except (
        worktree.WorktreeError,  # base: exists / create / cleanup all map to 3
        layers.LayerError,  # base: monolithic / empty-layer / unowned-010
        gate.GateError,  # base: unknown / wrong / mismatched / unparseable
        commit_mod.CommitError,  # base: branch exists / git refused
        write.WriteFailed,
        source.SourceError,
        cfgmod.ConfigError,
        ValueError,
        KeyError,
        OSError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 3


def _emit(
    fmt,
    plan,
    opts,
    notes,
    applied=None,
    result=None,
    acceptance=None,
    code=None,
    report_path=None,
    committed=None,
):
    """One emitter, so --format json stays a single parseable document.

    Printing "applied: ..." alongside a JSON payload corrupts it for any consumer,
    so everything extra becomes a field instead.
    """
    if fmt == "json":
        payload = render.json_payload(plan, opts, applied=applied, notes=notes)
        if result is not None:
            payload["written"] = result["written"]
            payload["deleted"] = result["deleted"]
        if acceptance is not None:
            payload["acceptance_command"] = acceptance
        if report_path is not None:
            payload["report_file"] = str(report_path)
        if applied:
            payload["commit_summary"] = render.commit_summary(plan, opts, notes)
        if committed is not None:
            payload["committed"] = {"branch": committed[0], "commit": committed[1]}
        print(json.dumps(payload, indent=1, sort_keys=True))
        return
    print(
        render.text(
            plan, opts, notes, code=code, report_path=report_path, applied=applied
        ),
        end="",
    )
    if result is not None:
        print(
            f"\napplied: {len(result['written'])} written, "
            f"{len(result['deleted'])} deleted"
        )
    if committed is not None:
        branch, sha = committed
        print(f"\ncommitted {sha[:12]} on {branch} in the worktree")
        print(
            "amend it to say why each semantic value was accepted or retained -- "
            "the\nprovenance is generated, the reasoning is not"
        )
    elif applied:
        print("\ncommit message block, for the commit that carries this apply:\n")
        print(render.commit_summary(plan, opts, notes), end="")
    if acceptance is not None:
        print("\nverify with:\n" + acceptance)


if __name__ == "__main__":
    sys.exit(main())
