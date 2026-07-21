#!/bin/bash
#
# Generate a release notes section for a follow-up OSISM release
#
# This script generates the release notes section for a follow-up release
# (e.g. 10.1.0) as published at https://osism.tech/docs/release-notes/ and
# optionally inserts it into docs/release-notes/osism-<major>.md of
# osism/osism.github.io.
#
# The section is derived from this repository: the base.yml of the release
# is diffed against the previous release, and for every changed
# OSISM-maintained component (mapping: etc/changelog-repositories.yml) the
# matching CHANGELOG.md sections of the component repository are collected.
# When docker_images.kolla_ansible changed, the upstream
# openstack/kolla-ansible changes pulled in by the image rebuild (the image
# is built from the branch of the OpenStack version in use) are collected
# as well: commit subjects and reno release notes between the two build
# times, for a dedicated "OpenStack services" subsection. The added and
# removed downstream patches (patches/<openstack_version>) of the
# kolla-ansible image and the kolla service images are part of the input
# too, so that removals of patches that landed upstream are neither
# reported as removals nor duplicated as new features; the effective
# OSISM kolla defaults (osism/defaults) are included as a reference so
# that configuration advice matches what OSISM actually sets (Ubuntu
# only, no kolla-ansible command, osism apply instead). Claude turns
# this input into an operator-focused release notes body in the style of
# docs/release-notes/osism-10.md. The changelog entries are only PR title
# lists, so Claude is allowed to look up the referenced pull requests
# itself via the GitHub CLI (gh pr view/diff, read-only) to describe
# operator-relevant changes in detail; chores and dependency bumps
# (Renovate) are not looked up.
#
# The recurring "images have been rebuilt" bullets are derived
# deterministically from the docker_images.kolla version (never generated
# by the model). The model output is sanitized deterministically: it must
# start with a "### " subsection, "# "/"## " headings are demoted so that
# the generated body can never overwrite other release sections, and any
# preamble is stripped (src/release-notes.py sanitize).
#
# Insertion into osism.github.io follows the osism-10.md layout: a plain
# row in the release table (no anchor link) and a "## <version>" section
# (no date suffix) inserted before the first existing release section, or
# directly after the release table if the page has none yet. Without
# --site-dir a temporary clone is used; --commit commits on a branch,
# --pr additionally pushes the branch and opens a pull request via gh.
# Without any of these options the generated section is only written to a
# local file for review.
#
# Usage: ./scripts/generate-release-changelog.sh [options] <version>
#
# Options:
#   -p, --previous     Previous release (default: autodetected)
#   -d, --date         Release date as YYYYMMDD (default: today)
#   -n, --no-run       Only generate the input file, do not run Claude
#   -i, --input        Reuse an existing input file instead of generating
#                      one (e.g. from an earlier -n run, optionally edited)
#   -o, --output       Output file (default: release-notes-<version>.md)
#       --site-dir     Existing osism.github.io checkout to insert into
#   -c, --commit       Insert into osism.github.io and commit on a branch
#       --pr           Like --commit, plus push the branch and open a PR
#   -h, --help         Show this help message
#
# Examples:
#   ./scripts/generate-release-changelog.sh 10.1.0
#   ./scripts/generate-release-changelog.sh -n 10.1.0
#   ./scripts/generate-release-changelog.sh -i release-notes-input-10.1.0.md 10.1.0
#   ./scripts/generate-release-changelog.sh --site-dir ../osism.github.io 10.1.0
#   ./scripts/generate-release-changelog.sh --pr 10.1.0
#

set -e

VERSION=""
PREVIOUS=""
DATE=$(date +%Y%m%d)
OUTPUT_FILE=""
INPUT_FILE_ARG=""
RUN_CLAUDE=true
SITE_DIR=""
DO_COMMIT=false
DO_PR=false

# Model used for release notes generation
CLAUDE_MODEL="claude-sonnet-5"
CLAUDE_EFFORT="xhigh"

# Read-only gh commands Claude may run to look up pull requests referenced
# in the changelog entries
CLAUDE_ALLOWED_TOOLS="Bash(gh pr view:*),Bash(gh pr diff:*),Bash(gh issue view:*)"

SITE_REPO="osism/osism.github.io"

RELEASE_NOTES_PY="$(cd "$(dirname "$0")/.." && pwd)/src/release-notes.py"

# Run the Python helper: uv provisions its dependencies from the inline
# script metadata (PEP 723); plain python3 is the fallback and requires
# requests/PyYAML to be installed
release_notes_py() {
    if command -v uv >/dev/null 2>&1; then
        uv run -q "$RELEASE_NOTES_PY" "$@"
    else
        python3 "$RELEASE_NOTES_PY" "$@"
    fi
}

show_help() {
    echo "Usage: $0 [options] <version>"
    echo ""
    echo "Options:"
    echo "  -p, --previous     Previous release (default: autodetected)"
    echo "  -d, --date         Release date as YYYYMMDD (default: today)"
    echo "  -n, --no-run       Only generate the input file, do not run Claude"
    echo "  -i, --input        Reuse an existing input file instead of generating"
    echo "                     one (e.g. from an earlier -n run, optionally edited)"
    echo "  -o, --output       Output file (default: release-notes-<version>.md)"
    echo "      --site-dir     Existing osism.github.io checkout to insert into"
    echo "  -c, --commit       Insert into osism.github.io and commit on a branch"
    echo "      --pr           Like --commit, plus push the branch and open a PR"
    echo "  -h, --help         Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 10.1.0"
    echo "  $0 -n 10.1.0"
    echo "  $0 -i release-notes-input-10.1.0.md 10.1.0"
    echo "  $0 --site-dir ../osism.github.io 10.1.0"
    echo "  $0 --pr 10.1.0"
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        -p|--previous)
            PREVIOUS="$2"
            shift 2
            ;;
        -d|--date)
            DATE="$2"
            shift 2
            ;;
        -n|--no-run)
            RUN_CLAUDE=false
            shift
            ;;
        -i|--input)
            INPUT_FILE_ARG="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        --site-dir)
            SITE_DIR="$2"
            shift 2
            ;;
        -c|--commit)
            DO_COMMIT=true
            shift
            ;;
        --pr)
            DO_COMMIT=true
            DO_PR=true
            shift
            ;;
        -h|--help)
            show_help
            ;;
        [0-9]*)
            VERSION="$1"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use -h or --help for usage information"
            exit 1
            ;;
    esac
done

if [ -z "$VERSION" ]; then
    echo "Error: No release version given (e.g. 10.1.0)"
    exit 1
fi

if [ ! -f "$VERSION/base.yml" ]; then
    echo "Error: $VERSION/base.yml not found (run this script from the repository root)"
    exit 1
fi

if [ "$DO_COMMIT" = true ] && [ "$RUN_CLAUDE" = false ]; then
    echo "Error: --commit/--pr cannot be combined with --no-run (nothing is generated)"
    exit 1
fi

if [ -n "$INPUT_FILE_ARG" ] && [ "$RUN_CLAUDE" = false ]; then
    echo "Error: --input cannot be combined with --no-run (nothing would be done)"
    exit 1
fi

if [ -n "$INPUT_FILE_ARG" ] && [ ! -f "$INPUT_FILE_ARG" ]; then
    echo "Error: input file $INPUT_FILE_ARG not found"
    exit 1
fi

if [ "$RUN_CLAUDE" = true ] && ! command -v gh >/dev/null 2>&1; then
    echo "Error: the GitHub CLI (gh) is required so that Claude can look up"
    echo "pull requests (use -n to only generate the input file)"
    exit 1
fi

if ! [[ "$DATE" =~ ^[0-9]{8}$ ]]; then
    echo "Error: --date must be given as YYYYMMDD"
    exit 1
fi

# Autodetect the previous release: the highest release directory below the
# target version
if [ -z "$PREVIOUS" ]; then
    PREVIOUS=$(ls -d [0-9]*/ 2>/dev/null | tr -d '/' \
        | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' \
        | sort -V | awk -v v="$VERSION" '$0 == v {exit} {prev=$0} END {print prev}')
    if [ -z "$PREVIOUS" ]; then
        echo "Error: Could not autodetect the previous release for $VERSION"
        exit 1
    fi
fi

if [ ! -f "$PREVIOUS/base.yml" ]; then
    echo "Error: $PREVIOUS/base.yml not found"
    exit 1
fi

if [ -z "$OUTPUT_FILE" ]; then
    OUTPUT_FILE="release-notes-${VERSION}.md"
fi
INPUT_FILE="release-notes-input-${VERSION}.md"
MAJOR="${VERSION%%.*}"

if [ -n "$INPUT_FILE_ARG" ]; then
    INPUT_FILE="$INPUT_FILE_ARG"
    echo "Reusing existing input file: $INPUT_FILE"
    echo "  Release:          $VERSION"
    echo "  Previous release: $PREVIOUS"
    echo "  Release date:     $DATE"
    if ! grep -q "^- New release: ${VERSION}\$" "$INPUT_FILE"; then
        echo ""
        echo "Warning: $INPUT_FILE does not state '- New release: $VERSION';"
        echo "it may belong to a different release or predate a base.yml change."
    fi
else
    echo "Generating release notes input..."
    echo "  Release:          $VERSION"
    echo "  Previous release: $PREVIOUS"
    echo "  Release date:     $DATE"

    release_notes_py input \
        --previous "$PREVIOUS" --current "$VERSION" --output "$INPUT_FILE"
fi

# The standard bullets derive from base.yml directly and are cheap to
# recompute, also when an existing input file is reused
BULLETS=$(release_notes_py bullets \
    --previous "$PREVIOUS" --current "$VERSION")

if [ "$RUN_CLAUDE" = false ]; then
    echo ""
    echo "Next steps:"
    echo "  Review (and optionally edit) $INPUT_FILE, then reuse it:"
    echo "     $0 -i $INPUT_FILE $VERSION"
    echo ""
    echo "  Or run the steps manually:"
    echo "  1. Run Claude on the input file:"
    echo "     claude --model $CLAUDE_MODEL --effort $CLAUDE_EFFORT --allowedTools '$CLAUDE_ALLOWED_TOOLS' -p \"\$(cat $INPUT_FILE)\""
    echo "  2. Sanitize the result:"
    echo "     uv run src/release-notes.py sanitize"
    echo "  3. Assemble the section in $OUTPUT_FILE and insert it into"
    echo "     docs/release-notes/osism-${MAJOR}.md of $SITE_REPO"
    exit 0
fi

# The prompt: placeholders are substituted below so that no shell expansion
# happens inside the template (it contains backticks and code fences).
# Written to a file first because heredocs inside $(...) with backticks
# break the bash 3.2 parser on macOS.
PROMPT_TEMPLATE_FILE=$(mktemp)
cat > "$PROMPT_TEMPLATE_FILE" <<'EOF'
# Release Notes Generation Prompt

You are writing the release notes section for OSISM __VERSION__, a follow-up
(point) release of OSISM __MAJOR__, published at
https://osism.tech/docs/release-notes/.

Below you find (1) the version changes between OSISM __PREVIOUS__ and OSISM
__VERSION__, (2) excerpts from the CHANGELOG.md files of all changed
OSISM components, covering exactly the changes contained in this release,
and possibly (3) a "kolla-ansible upstream changes" section with the
upstream openstack/kolla-ansible changes (commit subjects and reno release
notes) that the rebuild of the kolla-ansible container image pulled in,
including the added and removed downstream patches of that image, (4)
the downstream patch changes of the kolla service images, and (5) an
"OSISM kolla defaults (reference)" section with the effective kolla
defaults OSISM ships in this release.

Write the body of the release notes section for OSISM __VERSION__.

Selection rules:
- The target audience are operators of OSISM clouds, not developers
- Only include changes that are relevant for operators: breaking changes,
  required configuration changes, new features, new parameters, changed
  defaults, and behavior changes
- Ignore internal changes: unit tests, CI, linting, refactorings, project
  board automation, release preparation, SBOM/build tooling
- Ignore routine dependency bumps (Renovate) and other chores; mention a
  third-party component only if the version jump is significant (e.g. a
  new major version) or has operator impact

OSISM conventions:
- OSISM supports Ubuntu as the only host distribution: ignore changes
  that only affect other distributions (Rocky Linux, CentOS, RHEL).
  Debian-family changes apply to Ubuntu, keep those. If a change is
  useful but its description is framed around another distribution
  (e.g. a migration whose example is a Rocky upgrade), describe it
  without that framing
- Operators never run kolla-ansible directly, there is no kolla-ansible
  command in OSISM: never show one. A kolla-ansible subcommand maps to
  "osism apply <name>" (e.g. "kolla-ansible migrate-valkey" becomes
  "osism apply migrate-valkey"); service upgrades are
  "osism apply -a upgrade <service>". If you cannot derive the OSISM
  equivalent, describe the change without a command
- ALWAYS check configuration advice against the "OSISM kolla defaults
  (reference)" section before writing it: OSISM overrides many upstream
  kolla-ansible defaults there (later files win). Advice is only valid
  if the operator actually has to act. Never tell operators to restore
  an upstream default that osism/defaults already neutralizes, and never
  recommend keeping a value OSISM does not support in the first place
  (example: upstream dropped linuxbridge, but OSISM defaults to
  neutron_plugin_agent "ovn", so no linuxbridge advice belongs in OSISM
  release notes)

Looking up pull requests:
- The changelog entries reference pull requests like (org/repo#123). For
  every entry you decide to include whose changelog line alone does not
  tell an operator what they get or what they must do, read the pull
  request before writing about it:
      gh pr view <number> --repo <org>/<repo>
  If the description is thin, also look at the change itself:
      gh pr diff <number> --repo <org>/<repo>
- Use what you find to name the new parameters, their defaults, the
  affected files, and the required steps; a bullet that merely rephrases
  the PR title has no value
- Do NOT look up entries you are going to ignore anyway (dependency
  bumps, chores, CI/linting, release housekeeping)
- Everything you state beyond a changelog line must come from a pull
  request you actually read; if a lookup fails, fall back to the plain
  changelog line rather than guessing

OpenStack services (kolla-ansible):
- If the input contains a "kolla-ansible upstream changes" section, write
  a dedicated "### OpenStack services" subsection covering both the
  operator-relevant changes of the OSISM kolla-ansible container image
  (from the container-image-kolla-ansible changelog: patches, new
  parameters, behavior changes) and the operator-relevant upstream
  kolla-ansible changes pulled in by the image rebuild
- For the upstream changes rely on the reno release notes (upgrade,
  features, deprecations, security and fixes sections); use the commit
  subjects only as supporting context and ignore test-only, CI, zuul and
  documentation-only commits
- Name the OpenStack version the changes apply to (it is part of the
  section heading in the input)
- The upstream changes are Gerrit changes without GitHub pull requests:
  do NOT try to look them up with gh, use only the input
- The build background in the input (that the image was rebuilt, which
  upstream branch it is built from, that downstream patches exist or
  were applied) is context for your selection, never content: a sentence
  like "The kolla-ansible container image was rebuilt from the
  stable/2025.1 branch together with OSISM's own downstream patches"
  must never appear; write only the concrete changes themselves

Downstream patches (backports):
- OSISM carries downstream patches in container-image-kolla-ansible and
  container-images-kolla (patches/<openstack version>) to ship upstream
  changes early; a patch is removed again once the change landed
  upstream. The input lists the added and removed patches per image
- NEVER report the removal of a downstream patch as a removal, and never
  as its own bullet: when the change landed upstream, the behavior does
  not change for operators
- Reconcile the upstream changes with the removed patches: an upstream
  change whose downstream patch was removed in the same range was
  already shipped in a previous OSISM release; do NOT present it as new,
  leave it out entirely
- The same change must never appear twice (once from the changelog or a
  patch, once from the upstream release notes); write it once
- Only a patch removed WITHOUT the change landing upstream takes a
  feature away and belongs in the release notes; if the reason for a
  removal is unclear, read the pull request that removed the patch (the
  component changelog references it) before writing anything

Structure:
- Breaking changes and required configuration or migration steps come
  first, each as its own "### " subsection with concrete instructions and,
  when helpful, a yaml/bash code block (see the example below)
- Group the remaining noteworthy changes into "### " subsections by
  component or topic (e.g. "### Inventory reconciler improvements") with
  short bullet lists; use a single "### Notable changes" subsection if
  there are only a few items
- One topic per subsection: never combine two components or topics in
  one heading ("### osism CLI and NetBox-manager", "### Baremetal and
  SONiC", "### Networking and inventory reconciler" are all wrong,
  split them). OSISM services and components (inventory reconciler,
  osism CLI, netbox-manager, SONiC, baremetal, ...) each get their own
  dedicated subsection, never a combined one; only items too small for
  a subsection of their own go into "### Notable changes"
- Summarize instead of enumerating every changelog entry; release notes
  are curated, not a changelog

Writing style:
- Write in English, plain and concrete, as an experienced operator would
  write for colleagues
- Name the parameter, file, or command instead of describing it in the
  abstract; show, don't announce
- No quality labels ("robust", "significantly improved", "seamless") and
  no marketing tone; state what changed and what to do
- No filler openers ("note that", "it is important to", "please be
  aware"); start with the fact
- Vary sentence length and let paragraphs end plainly, without a closing
  slogan
- Use at most one em-dash (—) and at most one "not X, but Y" contrast in
  the whole section; otherwise use a period, comma, or colon
- Every sentence must be readable in one breath; split long ones

Output format:
- Do NOT invent anything that is not covered by the input or the pull
  requests you read; when unsure, leave it out
- Do NOT include PR/issue references like (org/repo#123)
- Do NOT include the release heading "## __VERSION__" and do NOT mention
  rebuilt service images; both are added by the calling script
- Your output MUST start with the first "### " subsection heading
- Only use "### " and "#### " headings, never "# " or "## "
- Do NOT wrap your output in a code fence (no leading ```markdown); code
  fences are only used for code examples inside the sections
- Do NOT include any preamble, explanation, or commentary about your task
  or the input - output ONLY the raw markdown
- Print the markdown directly as your final response; the gh lookups
  described above are the only tool use allowed, and do NOT attempt to
  write any files

Example of the desired style (from a previous release, for format only):

### Removal of the community.general.yaml Ansible plugin

If `community.general.yaml` has been set for `stdout_callback` in `ansible.cfg`,
this entry must be removed and replaced with `result_format=yaml`.

### TLS for ProxySQL is now enabled by default

If you are already using ProxySQL, but without TLS, set the following parameter in
`environments/kolla/configuration.yml`.

```yaml title="environments/kolla/configuration.yml"
database_enable_tls_internal: "no"
```

### Inventory reconciler improvements

* **Secrets extraction**: Ansible Vault encrypted values from the `secrets` custom field on NetBox
  devices are extracted and written to `999-netbox-secrets.yml` with proper `!vault` tags.
* **Configurable dnsmasq DHCP lease time**: New `DNSMASQ_LEASE_TIME` environment variable
  (default: `28d`).

---

EOF

PROMPT_TEMPLATE=$(cat "$PROMPT_TEMPLATE_FILE")
rm -f "$PROMPT_TEMPLATE_FILE"

PROMPT="${PROMPT_TEMPLATE//__VERSION__/$VERSION}"
PROMPT="${PROMPT//__PREVIOUS__/$PREVIOUS}"
PROMPT="${PROMPT//__MAJOR__/$MAJOR}"
PROMPT="$PROMPT

$(cat "$INPUT_FILE")"

echo ""
echo "Generating release notes section with Claude..."

# stderr is kept apart from the result: with tool use enabled it may
# carry noise that must not end up in the generated body
CLAUDE_STDERR_FILE=$(mktemp)
RESULT=$(claude --model "$CLAUDE_MODEL" --effort "$CLAUDE_EFFORT" \
    --allowedTools "$CLAUDE_ALLOWED_TOOLS" -p "$PROMPT" \
    2> "$CLAUDE_STDERR_FILE") || true

if ! BODY=$(echo "$RESULT" | release_notes_py sanitize); then
    echo ""
    echo "Error: Claude did not return a usable release notes body:"
    echo "----------------------------------------"
    echo "$RESULT"
    cat "$CLAUDE_STDERR_FILE"
    echo "----------------------------------------"
    rm -f "$CLAUDE_STDERR_FILE"
    exit 1
fi
rm -f "$CLAUDE_STDERR_FILE"

{
    echo "## $VERSION"
    echo ""
    if [ -n "$BULLETS" ]; then
        echo "$BULLETS"
        echo ""
    fi
    echo "$BODY"
} > "$OUTPUT_FILE"

echo ""
echo "Release notes section written to: $OUTPUT_FILE"
echo ""
echo "Content:"
echo "----------------------------------------"
cat "$OUTPUT_FILE"
echo "----------------------------------------"

if [ -z "$SITE_DIR" ] && [ "$DO_COMMIT" = false ]; then
    echo ""
    echo "Next steps:"
    echo "  Insert the section into docs/release-notes/osism-${MAJOR}.md of $SITE_REPO,"
    echo "  or re-run with --site-dir/--commit/--pr to do this automatically."
    exit 0
fi

# Insert the section into osism.github.io
if [ -z "$SITE_DIR" ]; then
    SITE_DIR=$(mktemp -d)/osism.github.io
    echo ""
    echo "Cloning $SITE_REPO into $SITE_DIR..."
    if command -v gh >/dev/null 2>&1; then
        gh repo clone "$SITE_REPO" "$SITE_DIR" -- --depth 1
    else
        git clone --depth 1 "https://github.com/${SITE_REPO}.git" "$SITE_DIR"
    fi
fi

SITE_FILE="$SITE_DIR/docs/release-notes/osism-${MAJOR}.md"
if [ ! -f "$SITE_FILE" ]; then
    echo "Error: $SITE_FILE not found"
    exit 1
fi

release_notes_py insert \
    --site-file "$SITE_FILE" --section-file "$OUTPUT_FILE" \
    --version "$VERSION" --date "$DATE"

if [ "$DO_COMMIT" = true ]; then
    cd "$SITE_DIR"

    BRANCH="release-notes/${VERSION}"
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
        if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
            git switch "$BRANCH"
        else
            git switch -c "$BRANCH"
        fi
    fi

    COMMIT_MSG_FILE=$(mktemp)
    {
        echo "release notes: add OSISM $VERSION"
        echo ""
        echo "Generated from the component CHANGELOGs between OSISM $PREVIOUS and"
        echo "OSISM $VERSION by scripts/generate-release-changelog.sh in"
        echo "osism/release."
        echo ""
        echo "Assisted-by: Claude:$CLAUDE_MODEL"
    } > "$COMMIT_MSG_FILE"

    git add "docs/release-notes/osism-${MAJOR}.md"
    git commit -s -F "$COMMIT_MSG_FILE"
    rm -f "$COMMIT_MSG_FILE"

    echo ""
    echo "Committed on branch $BRANCH:"
    git log -1 --format='%B' | sed 's/^/  /'

    if [ "$DO_PR" = true ]; then
        echo "Pushing $BRANCH and creating pull request..."

        git push -u origin "$BRANCH"

        gh pr create \
            --title "release notes: add OSISM $VERSION" \
            --body "Adds the release notes section for OSISM $VERSION, generated from the component CHANGELOGs between OSISM $PREVIOUS and OSISM $VERSION.

Generated with \`generate-release-changelog.sh\` using $CLAUDE_MODEL. Please review the generated section before merging."
    fi
fi
