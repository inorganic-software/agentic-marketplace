# On feat/add-farewell, pushed, with PR #7 open against main: approved and every
# check passing.
source "$(dirname "$0")/../common.sh"
init_repo
install_gh_stub
git switch -q -c feat/add-farewell
add_farewell
git commit -qam "feat: add farewell"
git push -q -u origin feat/add-farewell
write_pr <<'JSON'
{"number": 7, "title": "feat(greeting): add farewell", "body": "Adds farewell(name).",
 "state": "OPEN", "isDraft": false, "reviewDecision": "APPROVED",
 "headRefName": "feat/add-farewell", "baseRefName": "main", "labels": [],
 "checks": [{"name": "do-not-merge label", "conclusion": "SUCCESS"}]}
JSON
