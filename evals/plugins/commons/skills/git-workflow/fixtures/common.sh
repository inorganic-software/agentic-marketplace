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

# The `gh` stub (fixtures/gh), first in the PATH: GitHub without leaving the machine, with
# origin.git as github.com/example/greeting. Its docstring says what it imitates and
# where it keeps what the agent did; anything else fails as the real gh would and is
# reported as a gap of the stub.
install_gh_stub() {
  mkdir -p "$EVAL_SANDBOX/gh"
  cp "$(dirname "${BASH_SOURCE[0]}")/gh" "$EVAL_SANDBOX/bin/gh"
  chmod +x "$EVAL_SANDBOX/bin/gh"
}

# A PR that already exists when the agent starts, as JSON on stdin: number, title, body,
# state, isDraft, reviewDecision, headRefName, baseRefName, labels and checks (each
# {"name", "conclusion"}, a null conclusion while it runs). gh pr merge only merges it
# if it is approved and every check passed, as main's protection would.
write_pr() {
  cat > "$EVAL_SANDBOX/gh/pr.json"
}
