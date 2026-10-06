# On main, up to date with origin, with a bug fix uncommitted: greet() crashed on
# None and now falls back to "there".
source "$(dirname "$0")/../common.sh"
init_repo
install_gh_stub
cat > src/greeting.py <<'PY'
def greet(name):
    return "Hello, " + (name or "there")
PY
