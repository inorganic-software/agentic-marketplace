# On feat/exclaim, pushed, behind origin/main: someone moved greet() to an f-string
# on main, and this branch adds "!" to the same line. A rebase conflicts; resolved by
# intent, greet() returns f"Hello, {name}!". This clone has not fetched main yet.
source "$(dirname "$0")/../common.sh"
init_repo
install_gh_stub
use_f_string() {
  cat > src/greeting.py <<'PY'
def greet(name):
    return f"Hello, {name}"
PY
}
git switch -q -c feat/exclaim
cat > src/greeting.py <<'PY'
def greet(name):
    return "Hello, " + name + "!"
PY
git commit -qam "feat: greet with an exclamation mark"
git push -q -u origin feat/exclaim
push_to_main_from_elsewhere "refactor: use an f-string in greet" use_f_string
