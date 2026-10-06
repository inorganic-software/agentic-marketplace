# On feat/add-farewell, pushed, with PR #7 open against main: approved and every
# check passing.
source "$(dirname "$0")/../common.sh"
init_repo
install_gh_stub
git switch -q -c feat/add-farewell
add_farewell
git commit -qam "feat: add farewell"
git push -q -u origin feat/add-farewell
echo "feat(greeting): add farewell" > "$EVAL_SANDBOX/gh/title.txt"
cat > "$EVAL_SANDBOX/gh/view.txt" <<'TXT'
feat(greeting): add farewell #7
Open • eval-agent wants to merge 1 commit into main from feat/add-farewell
Reviewers: code-owner (Approved)
Checks: all passing

  Adds farewell(name).

View this pull request on GitHub: https://github.com/example/greeting/pull/7
TXT
cat > "$EVAL_SANDBOX/gh/view.json" <<'JSON'
{"number": 7, "title": "feat(greeting): add farewell", "state": "OPEN", "isDraft": false, "reviewDecision": "APPROVED", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "baseRefName": "main", "headRefName": "feat/add-farewell", "labels": [], "url": "https://github.com/example/greeting/pull/7"}
JSON
cat > "$EVAL_SANDBOX/gh/checks.txt" <<'TXT'
All checks were successful
0 failing, 1 successful, 0 skipped, and 0 pending checks

✓  do-not-merge label  2s  https://github.com/example/greeting/actions/runs/1
TXT
cat > "$EVAL_SANDBOX/gh/list.txt" <<'TXT'
#7  feat(greeting): add farewell  feat/add-farewell  OPEN
TXT
