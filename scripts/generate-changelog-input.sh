#!/bin/bash
#
# Generate changelog input file for Claude
#
# This script collects all commits and diffs between the latest tag (or a
# specified tag) and the previous tag, then creates a file with a prompt
# for Claude to generate a changelog entry.
#
# With --from, it processes all tags from the specified tag onwards,
# generating release notes for each one. If commits exist beyond the last
# tag, a virtual tag with today's date is added automatically.
#
# With --auto, it reads CHANGELOG.md to find the last documented tag,
# then processes all tags after it (including a virtual tag for today
# if there are unreleased commits).
#
# For large releases, commits are processed in batches to avoid prompt
# length limits. Batching is based on diff size, not commit count.
#
# For release preparation: If a tag with today's date is specified but
# doesn't exist yet, the script will use HEAD as reference and collect
# commits from the last existing tag to HEAD.
#
# Usage: ./scripts/generate-changelog-input.sh [options] [tag]
#
# Options:
#   -a, --auto         Auto-detect: read last tag from CHANGELOG.md, process all after it
#   -f, --from         Process all tags from this tag onwards
#   -n, --no-run       Only generate the input file(s), do not run Claude
#   -o, --output       Specify output file (default: changelog-input-<tag>.md)
#   -s, --max-size     Max lines per batch (default: 2000)
#   -h, --help         Show this help message
#
# Examples:
#   ./scripts/generate-changelog-input.sh                     # Use latest tag, run Claude
#   ./scripts/generate-changelog-input.sh v0.20251130.1       # Use specific tag, run Claude
#   ./scripts/generate-changelog-input.sh v9.20251205.0       # Prepare new release (tag doesn't exist yet)
#   ./scripts/generate-changelog-input.sh --auto              # Auto-detect from CHANGELOG.md
#   ./scripts/generate-changelog-input.sh --from v0.20260301.0  # All tags from v0.20260301.0 onwards
#   ./scripts/generate-changelog-input.sh -n                  # Only generate file
#   ./scripts/generate-changelog-input.sh -s 3000             # 3000 lines per batch
#

set -e

# Parse arguments
LATEST_TAG=""
FROM_TAG=""
AUTO_MODE=false
OUTPUT_FILE=""
RUN_CLAUDE=true
MAX_BATCH_SIZE=2000

show_help() {
    echo "Usage: $0 [options] [tag]"
    echo ""
    echo "Options:"
    echo "  -a, --auto         Auto-detect: read last tag from CHANGELOG.md, process all after it"
    echo "  -f, --from         Process all tags from this tag onwards"
    echo "  -n, --no-run       Only generate the input file(s), do not run Claude"
    echo "  -o, --output       Specify output file (default: changelog-input-<tag>.md)"
    echo "  -s, --max-size     Max lines per batch (default: 2000)"
    echo "  -h, --help         Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                          # Use latest tag, run Claude"
    echo "  $0 v0.20251130.1            # Use specific tag, run Claude"
    echo "  $0 --auto                   # Auto-detect from CHANGELOG.md"
    echo "  $0 --from v0.20260301.0     # All tags from v0.20260301.0 onwards"
    echo "  $0 -n                       # Only generate file"
    echo "  $0 -s 3000                  # 3000 lines per batch"
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        -n|--no-run)
            RUN_CLAUDE=false
            shift
            ;;
        -o|--output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        -s|--max-size)
            MAX_BATCH_SIZE="$2"
            shift 2
            ;;
        -a|--auto)
            AUTO_MODE=true
            shift
            ;;
        -f|--from)
            FROM_TAG="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            ;;
        v*)
            LATEST_TAG="$1"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use -h or --help for usage information"
            exit 1
            ;;
    esac
done

# Handle --auto mode: read last tag from CHANGELOG.md
if [ "$AUTO_MODE" = true ]; then
    if [ -n "$FROM_TAG" ] || [ -n "$LATEST_TAG" ]; then
        echo "Error: --auto cannot be combined with --from or a specific tag"
        exit 1
    fi

    if [ ! -f "CHANGELOG.md" ]; then
        echo "Error: CHANGELOG.md not found"
        exit 1
    fi

    # Extract the last (most recent) tag from CHANGELOG.md
    LAST_CHANGELOG_TAG=$(sed -n 's/^## \[\([^]]*\)\].*/\1/p' CHANGELOG.md | head -1)

    if [ -z "$LAST_CHANGELOG_TAG" ]; then
        echo "Error: No version tag found in CHANGELOG.md (expected format: ## [v...])"
        exit 1
    fi

    echo "Auto mode: Last tag in CHANGELOG.md is $LAST_CHANGELOG_TAG"

    # Verify this tag exists in git
    if ! git rev-parse "$LAST_CHANGELOG_TAG" >/dev/null 2>&1; then
        echo "Error: Tag '$LAST_CHANGELOG_TAG' from CHANGELOG.md not found in git"
        exit 1
    fi

    # Collect all tags after the last changelog tag
    FOUND=false
    AFTER=false
    AUTO_TAGS=()
    while IFS= read -r tag; do
        if [ "$AFTER" = true ]; then
            AUTO_TAGS+=("$tag")
        fi
        if [ "$tag" = "$LAST_CHANGELOG_TAG" ]; then
            FOUND=true
            AFTER=true
        fi
    done < <(git tag --sort=version:refname | grep '^v0\.')

    if [ "$FOUND" = false ]; then
        echo "Error: Tag '$LAST_CHANGELOG_TAG' not found in tag list"
        exit 1
    fi

    # Check if HEAD has commits beyond last existing tag
    if [ ${#AUTO_TAGS[@]} -gt 0 ]; then
        LAST_EXISTING_TAG="${AUTO_TAGS[${#AUTO_TAGS[@]}-1]}"
    else
        LAST_EXISTING_TAG="$LAST_CHANGELOG_TAG"
    fi

    COMMITS_AFTER=$(git rev-list --count "$LAST_EXISTING_TAG"..HEAD)
    if [ "$COMMITS_AFTER" -gt 0 ]; then
        TODAY_TAG="v0.$(date +%Y%m%d).0"
        if [ "$LAST_EXISTING_TAG" != "$TODAY_TAG" ]; then
            echo "Note: Found $COMMITS_AFTER commit(s) after $LAST_EXISTING_TAG, adding $TODAY_TAG"
            AUTO_TAGS+=("$TODAY_TAG")
        fi
    fi

    if [ ${#AUTO_TAGS[@]} -eq 0 ]; then
        echo "No new tags found after $LAST_CHANGELOG_TAG. CHANGELOG.md is up to date."
        exit 0
    fi

    # Use the collected tags via FROM_TAG logic path
    FROM_TAG="__auto__"
fi

# Build list of tags to process
TAGS_TO_PROCESS=()

if [ "$FROM_TAG" = "__auto__" ]; then
    TAGS_TO_PROCESS=("${AUTO_TAGS[@]}")

    echo "Tags to process (${#TAGS_TO_PROCESS[@]}):"
    for t in "${TAGS_TO_PROCESS[@]}"; do
        echo "  - $t"
    done
    echo ""
elif [ -n "$FROM_TAG" ]; then
    # Multi-tag mode: process all tags from FROM_TAG onwards
    if ! git rev-parse "$FROM_TAG" >/dev/null 2>&1; then
        echo "Error: Tag '$FROM_TAG' not found"
        exit 1
    fi

    FOUND=false
    while IFS= read -r tag; do
        if [ "$tag" = "$FROM_TAG" ]; then
            FOUND=true
        fi
        if [ "$FOUND" = true ]; then
            TAGS_TO_PROCESS+=("$tag")
        fi
    done < <(git tag --sort=version:refname | grep '^v0\.')

    if [ "$FOUND" = false ]; then
        echo "Error: Tag '$FROM_TAG' not found in tag list"
        exit 1
    fi

    # Check if HEAD has commits beyond last existing tag
    LAST_EXISTING_TAG="${TAGS_TO_PROCESS[${#TAGS_TO_PROCESS[@]}-1]}"
    COMMITS_AFTER=$(git rev-list --count "$LAST_EXISTING_TAG"..HEAD)
    if [ "$COMMITS_AFTER" -gt 0 ]; then
        TODAY_TAG="v0.$(date +%Y%m%d).0"
        if [ "$LAST_EXISTING_TAG" != "$TODAY_TAG" ]; then
            echo "Note: Found $COMMITS_AFTER commit(s) after $LAST_EXISTING_TAG, adding $TODAY_TAG"
            TAGS_TO_PROCESS+=("$TODAY_TAG")
        fi
    fi

    echo "Tags to process (${#TAGS_TO_PROCESS[@]}):"
    for t in "${TAGS_TO_PROCESS[@]}"; do
        echo "  - $t"
    done
    echo ""
else
    # Single-tag mode
    if [ -z "$LATEST_TAG" ]; then
        LATEST_TAG=$(git tag --sort=-version:refname | grep '^v0\.' | head -1)
    fi
    if [ -z "$LATEST_TAG" ]; then
        echo "Error: No tags found in repository"
        exit 1
    fi
    TAGS_TO_PROCESS=("$LATEST_TAG")
fi

_USER_OUTPUT_FILE="$OUTPUT_FILE"

# Process each tag
for CURRENT_TAG in "${TAGS_TO_PROCESS[@]}"; do
LATEST_TAG="$CURRENT_TAG"

if [ ${#TAGS_TO_PROCESS[@]} -gt 1 ]; then
    echo "========================================"
    echo "Processing tag: $LATEST_TAG"
    echo "========================================"
fi

# Set output file for this tag
if [ -z "$_USER_OUTPUT_FILE" ] || [ -n "$FROM_TAG" ]; then
    OUTPUT_FILE="changelog-input-${LATEST_TAG}.md"
else
    OUTPUT_FILE="$_USER_OUTPUT_FILE"
fi

# Check if tag exists or if we're preparing a new release
TAG_EXISTS=true
COMMIT_REF="$LATEST_TAG"

if ! git rev-parse "$LATEST_TAG" >/dev/null 2>&1; then
    TAG_EXISTS=false

    # Check if the tag matches today's date (format: v*.YYYYMMDD.* or vYYYYMMDD.*)
    TODAY=$(date +%Y%m%d)
    if [[ "$LATEST_TAG" =~ [0-9]*\.?($TODAY)\.[0-9]+ ]] || [[ "$LATEST_TAG" =~ ^v($TODAY)\.[0-9]+$ ]]; then
        echo "Note: Tag '$LATEST_TAG' does not exist yet, but matches today's date."
        echo "      Using HEAD as reference for upcoming release preparation."
        COMMIT_REF="HEAD"
    else
        echo "Error: Tag '$LATEST_TAG' does not exist"
        echo "       (To prepare a new release, use a tag with today's date: $(date +%Y%m%d))"
        exit 1
    fi
fi

# Get the previous tag
if [ "$TAG_EXISTS" = true ]; then
    # Tag exists: get the tag before LATEST_TAG
    PREVIOUS_TAG=$(git tag --sort=-version:refname | grep '^v0\.' | grep -A1 "^${LATEST_TAG}$" | tail -1)
else
    # Tag doesn't exist: use the most recent existing tag
    PREVIOUS_TAG=$(git tag --sort=-version:refname | grep '^v0\.' | head -1)
fi

if [ -z "$PREVIOUS_TAG" ] || [ "$LATEST_TAG" = "$PREVIOUS_TAG" ]; then
    echo "Error: No previous tag found before $LATEST_TAG"
    exit 1
fi

echo "Generating changelog input..."
echo "  Target tag:   $LATEST_TAG"
if [ "$TAG_EXISTS" = false ]; then
    echo "  Commit ref:   $COMMIT_REF (tag not yet created)"
fi
echo "  Previous tag: $PREVIOUS_TAG"

# Get the date from the tag name (format: v0.YYYYMMDD.X or vX.YYYYMMDD.X)
# Extract YYYYMMDD from tag and convert to YYYY-MM-DD
if [[ "$LATEST_TAG" =~ v[0-9]+\.([0-9]{4})([0-9]{2})([0-9]{2})\.[0-9]+ ]]; then
    TAG_DATE="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
elif [ "$TAG_EXISTS" = false ]; then
    # Tag doesn't exist yet, use today's date
    TAG_DATE=$(date +%Y-%m-%d)
else
    # Fallback to tag creation date if tag doesn't match expected format
    TAG_DATE=$(git for-each-ref --format='%(creatordate:short)' "refs/tags/$LATEST_TAG")
fi

# Get list of commits (oldest first for logical order)
COMMITS=()
while IFS= read -r commit; do
    COMMITS+=("$commit")
done < <(git log --reverse --format="%h" "$PREVIOUS_TAG".."$COMMIT_REF")
TOTAL_COMMITS=${#COMMITS[@]}

echo "  Total commits: $TOTAL_COMMITS"
echo "  Max batch size: $MAX_BATCH_SIZE lines"

# Create the prompt template
PROMPT_TEMPLATE='# Changelog Generation Prompt

Analyze the following Git commits and diffs and create CHANGELOG entries in the following format:

```markdown
### Added
- New features (if any)

### Changed
- Changes to existing features (if any)

### Fixed
- Bug fixes (if any)

### Removed
- Removed features (if any)

### Dependencies
- package-name 1.0.0 → 1.1.0
```

Important notes:
- Group related commits together
- Ignore pure merge commits
- Write entries in English
- Use concise, understandable descriptions
- Remove empty categories (e.g., if there are no "Removed" items)
- Start each entry with a capital letter
- Do not use periods at the end of entries
- Include dependency updates from Renovate in the "Dependencies" section
- Format dependency updates as: "package-name old_version → new_version" (use → arrow, lowercase package name)
- Do NOT include a version header (## [v...]) — start directly with ### Added, ### Changed, etc.
- Do NOT include any preamble, explanation, or commentary — output ONLY the raw markdown

---

'

# Max lines for a single diff (to prevent oversized commits)
MAX_DIFF_LINES=1500

# Function to get commit content
get_commit_content() {
    local commit="$1"
    local content=""
    local diff_content
    local diff_lines

    content+="## Commit: $commit"$'\n\n'
    content+=$(git log -1 --format="**Author:** %an%n**Date:** %ci%n%n**Message:**%n%s%n%n%b" "$commit")
    content+=$'\n\n### Diff\n\n```diff\n'

    # Get diff and truncate if too large
    diff_content=$(git show --format="" "$commit")
    diff_lines=$(echo "$diff_content" | wc -l)

    if [ "$diff_lines" -gt "$MAX_DIFF_LINES" ]; then
        content+=$(echo "$diff_content" | head -n "$MAX_DIFF_LINES")
        content+=$'\n\n... (diff truncated, was '"$diff_lines"' lines) ...\n'
    else
        content+="$diff_content"
    fi

    content+=$'\n```\n\n---\n\n'

    echo "$content"
}

# Create batches based on size
BATCH_FILES=()
BATCH_NUM=1
CURRENT_BATCH=""
CURRENT_SIZE=0
PROMPT_SIZE=$(echo "$PROMPT_TEMPLATE" | wc -l)

for commit in "${COMMITS[@]}"; do
    COMMIT_CONTENT=$(get_commit_content "$commit")
    COMMIT_SIZE=$(echo "$COMMIT_CONTENT" | wc -l)

    # If adding this commit would exceed the limit, save current batch and start new one
    if [ $((CURRENT_SIZE + COMMIT_SIZE + PROMPT_SIZE)) -gt $MAX_BATCH_SIZE ] && [ -n "$CURRENT_BATCH" ]; then
        BATCH_FILE="${OUTPUT_FILE%.md}-batch${BATCH_NUM}.md"
        BATCH_FILES+=("$BATCH_FILE")

        {
            echo "$PROMPT_TEMPLATE"
            echo "## Release Information"
            echo ""
            echo "- **Version:** $LATEST_TAG"
            echo "- **Date:** $TAG_DATE"
            echo "- **Batch:** $BATCH_NUM"
            echo ""
            echo "---"
            echo ""
            echo "# Commits in this batch"
            echo ""
            echo "$CURRENT_BATCH"
        } > "$BATCH_FILE"

        echo "  Created: $BATCH_FILE ($CURRENT_SIZE lines)"
        BATCH_NUM=$((BATCH_NUM + 1))
        CURRENT_BATCH=""
        CURRENT_SIZE=0
    fi

    CURRENT_BATCH+="$COMMIT_CONTENT"
    CURRENT_SIZE=$((CURRENT_SIZE + COMMIT_SIZE))
done

# Save the last batch
if [ -n "$CURRENT_BATCH" ]; then
    BATCH_FILE="${OUTPUT_FILE%.md}-batch${BATCH_NUM}.md"
    BATCH_FILES+=("$BATCH_FILE")

    {
        echo "$PROMPT_TEMPLATE"
        echo "## Release Information"
        echo ""
        echo "- **Version:** $LATEST_TAG"
        echo "- **Date:** $TAG_DATE"
        echo "- **Batch:** $BATCH_NUM"
        echo ""
        echo "---"
        echo ""
        echo "# Commits in this batch"
        echo ""
        echo "$CURRENT_BATCH"
    } > "$BATCH_FILE"

    echo "  Created: $BATCH_FILE ($CURRENT_SIZE lines)"
fi

NUM_BATCHES=${#BATCH_FILES[@]}
echo "  Number of batches: $NUM_BATCHES"

# Create the main output file with header
cat > "$OUTPUT_FILE" << EOF
# Changelog for $LATEST_TAG

**Date:** $TAG_DATE
**Previous Version:** $PREVIOUS_TAG
**Total Commits:** $TOTAL_COMMITS

---

EOF

echo ""
echo "Output files created:"
echo "  Main: $OUTPUT_FILE"
for f in "${BATCH_FILES[@]}"; do
    echo "  Batch: $f"
done

if [ "$RUN_CLAUDE" = true ]; then
    echo ""
    echo "Processing batches with Claude..."
    echo ""

    # Process each batch
    for (( batch=0; batch<NUM_BATCHES; batch++ )); do
        BATCH_FILE="${BATCH_FILES[$batch]}"
        echo "Processing batch $((batch+1)) of $NUM_BATCHES..."

        # Run Claude and append output to main file
        RESULT=$(claude -p "$(cat "$BATCH_FILE")" 2>&1) || true

        echo "" >> "$OUTPUT_FILE"
        echo "## Batch $((batch+1))" >> "$OUTPUT_FILE"
        echo "" >> "$OUTPUT_FILE"
        echo "$RESULT" >> "$OUTPUT_FILE"
        echo "" >> "$OUTPUT_FILE"
    done

    echo ""
    echo "All batches processed. Now merging results..."
    echo ""

    # Create merge prompt
    MERGE_PROMPT="Merge the following changelog batches into a single, clean changelog entry.

Rules:
- Combine all entries into one cohesive changelog
- Remove duplicate entries
- Group by category (Added, Changed, Fixed, Removed, Dependencies)
- Remove empty categories
- Keep the format consistent
- Your response MUST start with ## [$LATEST_TAG] - $TAG_DATE
- Do NOT include any preamble, explanation, or commentary — output ONLY the raw markdown

$(cat "$OUTPUT_FILE")"

    # Run final merge
    FINAL_RESULT=$(claude -p "$MERGE_PROMPT" 2>&1) || true

    # Strip any preamble before the first ## [v line
    CLEAN_RESULT=$(echo "$FINAL_RESULT" | sed -n '/^## \[v/,$p')
    if [ -z "$CLEAN_RESULT" ]; then
        CLEAN_RESULT="$FINAL_RESULT"
    fi

    # Write final result
    echo "$CLEAN_RESULT" > "$OUTPUT_FILE"

    echo "Final changelog written to: $OUTPUT_FILE"
    echo ""
    echo "Content:"
    echo "----------------------------------------"
    cat "$OUTPUT_FILE"
    echo "----------------------------------------"

    # Insert into CHANGELOG.md
    if [ -f "CHANGELOG.md" ]; then
        INSERT_LINE=$(grep -n '^## \[v' CHANGELOG.md | head -1 | cut -d: -f1)
        if [ -n "$INSERT_LINE" ]; then
            {
                head -n $((INSERT_LINE - 1)) CHANGELOG.md
                cat "$OUTPUT_FILE"
                echo ""
                tail -n +"$INSERT_LINE" CHANGELOG.md
            } > CHANGELOG.md.tmp
            mv CHANGELOG.md.tmp CHANGELOG.md
            echo "Inserted into CHANGELOG.md"
        fi
    fi

    # Cleanup batch files
    for f in "${BATCH_FILES[@]}"; do
        rm -f "$f"
    done
else
    echo ""
    echo "Next steps:"
    echo "  1. Run Claude on each batch file:"
    for f in "${BATCH_FILES[@]}"; do
        echo "     claude -p \"\$(cat $f)\""
    done
    echo "  2. Merge the results into $OUTPUT_FILE"
    echo "  3. Add the generated changelog entry to CHANGELOG.md"
fi

done  # end of TAGS_TO_PROCESS loop
