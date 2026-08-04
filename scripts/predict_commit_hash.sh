#!/usr/bin/env bash
# Predict the SHA git would assign to a commit before actually making it.
#
# Useful for referencing an upcoming commit's hash from OUTSIDE its own tree
# — telling a human or an external system the SHA in advance. It does NOT
# solve this repo's SysML derivedFromCommit self-reference problem: writing
# a predicted hash into a tracked file changes the tree, which changes the
# hash, so a commit's own stamp can never correctly reference that same
# commit (confirmed empirically — predicting, embedding, and re-predicting
# gives a different hash every time; there is no fixed point to converge on).
# Model re-stamps stay a genuine two-commit pattern: make the code commit,
# its real SHA is then already known with no prediction needed, and a
# separate commit writes that real SHA into the stamp.
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
