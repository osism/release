#!/bin/bash
#
# Generate changelog input file for Claude
#
# This script collects all commits and diffs between the latest tag (or a
# specified tag) and the previous tag, then creates a file with a prompt
# for Claude to generate a changelog entry.
#
# For large releases, commits are processed in batches to avoid prompt
# length limits. Batching is based on diff size, not commit count.
#
# Usage: ./scripts/generate-changelog-input.sh [options] [tag]
#
# Options:
#   -n, --no-run       Only generate the input file(s), do not run Claude
#   -o, --output       Specify output file (default: changelog-input-<tag>.md)
#   -s, --max-size     Max lines per batch (default: 2000)
#   -h, --help         Show this help message
#
# Examples:
#   ./scripts/generate-changelog-input.sh                     # Use latest tag, run Claude
#   ./scripts/generate-changelog-input.sh v0.20251130.1       # Use specific tag, run Claude
#   ./scripts/generate-changelog-input.sh -n                  # Only generate file
#   ./scripts/generate-changelog-input.sh -s 3000             # 3000 lines per batch
#

set -e

# Parse arguments
LATEST_TAG=""
OUTPUT_FILE=""
RUN_CLAUDE=true
MAX_BATCH_SIZE=2000

show_help() {
    echo "Usage: $0 [options] [tag]"
    echo ""
    echo "Options:"
    echo "  -n, --no-run       Only generate the input file(s), do not run Claude"
    echo "  -o, --output       Specify output file (default: changelog-input-<tag>.md)"
    echo "  -s, --max-size     Max lines per batch (default: 2000)"
    echo "  -h, --help         Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                          # Use latest tag, run Claude"
    echo "  $0 v0.20251130.1            # Use specific tag, run Claude"
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

# Get the latest tag if not specified
if [ -z "$LATEST_TAG" ]; then
    LATEST_TAG=$(git tag --sort=-version:refname | head -1)
fi

if [ -z "$LATEST_TAG" ]; then
    echo "Error: No tags found in repository"
    exit 1
fi

# Set default output file with tag name
if [ -z "$OUTPUT_FILE" ]; then
    OUTPUT_FILE="changelog-input-${LATEST_TAG}.md"
fi

# Verify the tag exists
if ! git rev-parse "$LATEST_TAG" >/dev/null 2>&1; then
    echo "Error: Tag '$LATEST_TAG' does not exist"
    exit 1
fi

# Get the previous tag (the one before LATEST_TAG)
PREVIOUS_TAG=$(git tag --sort=-version:refname | grep -A1 "^${LATEST_TAG}$" | tail -1)

if [ -z "$PREVIOUS_TAG" ] || [ "$LATEST_TAG" = "$PREVIOUS_TAG" ]; then
    echo "Error: No previous tag found before $LATEST_TAG"
    exit 1
fi

echo "Generating changelog input..."
echo "  Target tag:   $LATEST_TAG"
echo "  Previous tag: $PREVIOUS_TAG"

# Get the date from the tag name (format: v0.YYYYMMDD.X or vX.YYYYMMDD.X)
# Extract YYYYMMDD from tag and convert to YYYY-MM-DD
if [[ "$LATEST_TAG" =~ v[0-9]+\.([0-9]{4})([0-9]{2})([0-9]{2})\.[0-9]+ ]]; then
    TAG_DATE="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
else
    # Fallback to tag creation date if tag doesn't match expected format
    TAG_DATE=$(git for-each-ref --format='%(creatordate:short)' "refs/tags/$LATEST_TAG")
fi

# Get list of commits (oldest first for logical order)
COMMITS=()
while IFS= read -r commit; do
    COMMITS+=("$commit")
done < <(git log --reverse --format="%h" "$PREVIOUS_TAG".."$LATEST_TAG")
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
- Output ONLY the markdown changelog entries, no explanations

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
- Output ONLY the final markdown, starting with ## [$LATEST_TAG] - $TAG_DATE

$(cat "$OUTPUT_FILE")"

    # Run final merge
    FINAL_RESULT=$(claude -p "$MERGE_PROMPT" 2>&1) || true

    # Write final result
    echo "$FINAL_RESULT" > "$OUTPUT_FILE"

    echo "Final changelog written to: $OUTPUT_FILE"
    echo ""
    echo "Content:"
    echo "----------------------------------------"
    cat "$OUTPUT_FILE"
    echo "----------------------------------------"

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
