# On feat/add-farewell, one Conventional Commit ahead of main, not pushed yet.
source "$(dirname "$0")/../common.sh"
init_repo
install_gh_stub
git switch -q -c feat/add-farewell
add_farewell
git commit -qam "feat: add farewell"
