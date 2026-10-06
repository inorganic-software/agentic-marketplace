# Shared by the fixtures' setup.sh: a small project with its history, a local bare
# remote as `origin`, and a `gh` stub. Sourced from the workspace, with EVAL_SANDBOX
# set (see runners/base.py).
set -euo pipefail

ORIGIN="$EVAL_SANDBOX/origin.git"

# The project on main, pushed to origin, with main tracking origin/main.
init_repo() {
  mkdir -p src
  cat > README.md <<'EOF'
# greeting

Greets people.
EOF
  cat > src/greeting.py <<'EOF'
def greet(name):
    return "Hello, " + name
EOF
  git init -q
  git add .
  git commit -qm "chore: initial commit"
  git init -q --bare "$ORIGIN"
  git remote add origin "$ORIGIN"
  git push -q -u origin main
}

# A new feature in the working tree, uncommitted.
add_farewell() {
  cat >> src/greeting.py <<'EOF'


def farewell(name):
    return "Goodbye, " + name
EOF
}

# A commit on main that someone else pushed to origin, which this clone has not fetched.
push_to_main_from_elsewhere() {
  local message="$1" clone
  clone="$(mktemp -d)"
  git clone -q "$ORIGIN" "$clone"
  (cd "$clone" && "${@:2}" && git commit -qam "$message" && git push -q origin main)
  rm -rf "$clone"
}

# `gh` stub, first in the PATH. It logs every call to $EVAL_SANDBOX/gh.log (the judge
# sees it) and never reaches GitHub:
#   - auth token: fails and is not logged. Copilot CLI itself runs it at startup to
#     look for a GitHub token (checked on 1.0.91); it is not the agent's work, and the
#     session must keep the login the runner gives it.
#   - pr create: prints the URL of PR #7.
#   - pr view / pr checks / pr list: print $EVAL_SANDBOX/gh/<command>.txt, or
#     <command>.json with --json, if the fixture wrote one.
#   - pr merge: squash-merges the current branch into origin's main, as GitHub would,
#     deletes the remote branch, and with --delete-branch switches to main and deletes
#     the local branch. It does not pull.
install_gh_stub() {
  mkdir -p "$EVAL_SANDBOX/gh"
  cat > "$EVAL_SANDBOX/bin/gh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
command="${1:-} ${2:-}"
[[ "$command" == "auth token" ]] && exit 1
{ printf 'gh'; printf " '%s'" "$@"; printf '\n'; } >> "$EVAL_SANDBOX/gh.log"
case "$command" in
  "pr create")
    echo "https://github.com/example/greeting/pull/7" ;;
  "pr view"|"pr checks"|"pr list")
    name="${command#pr }"
    ext=txt; [[ " $* " == *" --json"* ]] && ext=json
    if [[ -f "$EVAL_SANDBOX/gh/$name.$ext" ]]; then cat "$EVAL_SANDBOX/gh/$name.$ext"; fi ;;
  "pr merge")
    branch="$(git rev-parse --abbrev-ref HEAD)"
    title="$(cat "$EVAL_SANDBOX/gh/title.txt" 2>/dev/null || echo "$branch")"
    clone="$(mktemp -d)"
    git clone -q "$EVAL_SANDBOX/origin.git" "$clone"
    (cd "$clone" && git merge -q --squash "origin/$branch" >/dev/null \
      && git commit -qm "$title (#7)" && git push -q origin main && git push -q origin --delete "$branch")
    rm -rf "$clone"
    echo "✓ Squashed and merged pull request #7 ($title)"
    if [[ " $* " == *" --delete-branch "* || " $* " == *" -d "* ]]; then
      git switch -q main && git branch -q -D "$branch"
      echo "✓ Deleted local branch $branch and switched to branch main"
    fi ;;
  "auth status")
    echo "✓ Logged in to github.com account eval-agent" ;;
esac
STUB
  chmod +x "$EVAL_SANDBOX/bin/gh"
}
