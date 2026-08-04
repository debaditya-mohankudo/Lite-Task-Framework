#!/usr/bin/env bash
# Predict the SHA git would assign to a commit before actually making it.
#
# Why this exists: this repo's SysML model packages carry a derivedFromCommit
# stamp naming the last commit that touched the code they model. A commit
# can't reference its own future SHA, so re-stamping has always trailed the
# code commit it describes by a separate follow-up commit. Predicting the
# hash first lets the stamp update land in the SAME commit instead — compute
# the SHA, write it into the .sysml file, stage everything, commit once.
#
# Uses a throwaway index copy so the real staging area (and the working
# tree) are never touched — safe to run at any point, whether or not you
# intend to commit right after.
#
# git commit-tree embeds an author/committer timestamp by default, so two
# calls with identical content but different clock times predict DIFFERENT
# hashes — the prediction only matches reality if the real commit reuses the
# exact date this script pins. That's why this prints two lines: the
# predicted hash on stdout, and the GIT_AUTHOR_DATE/GIT_COMMITTER_DATE
# assignment the real `git commit` must be run with, on stderr.
#
# Usage:
#   scripts/predict_commit_hash.sh "$(cat <<'EOF'
#   Subject line
#
#   task:<id>
#
#   Body.
#   EOF
#   )"
#
#   # then, to make the real commit match the prediction exactly:
#   GIT_AUTHOR_DATE="<printed date>" GIT_COMMITTER_DATE="<printed date>" \
#       git commit -F <message-file>
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <commit message>" >&2
    exit 1
fi

message="$1"
scratch_index="$(mktemp)"
trap 'rm -f "$scratch_index"' EXIT

cp .git/index "$scratch_index"
GIT_INDEX_FILE="$scratch_index" git add -A -- . > /dev/null 2>&1
tree="$(GIT_INDEX_FILE="$scratch_index" git write-tree)"

pinned_date="$(date +%s)"
hash="$(GIT_AUTHOR_DATE="$pinned_date" GIT_COMMITTER_DATE="$pinned_date" \
    git commit-tree "$tree" -p HEAD -m "$message")"

echo "$hash"
echo "Run the real commit with: GIT_AUTHOR_DATE=\"$pinned_date\" GIT_COMMITTER_DATE=\"$pinned_date\" git commit -F <message-file>" >&2
