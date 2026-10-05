# DebArk Bash completion · Mr.Nick (@Mohammad-Nicke)
# https://github.com/Mohammad-Nicke/debark

_debark_completions() {
    local current previous commands packages
    local -a mode_args

    current="${COMP_WORDS[COMP_CWORD]}"
    previous="${COMP_WORDS[COMP_CWORD-1]}"
    commands="install remove list search info files verify scan update upgrade config doctor repo cve repair gc log stats snapshot rollback extract convert export import bulk watch profile plugin pin license help about -S -i -R -r -Q -l -Qs -Ss -Qi -s -Si -Ql -L -Qk -V -Fy -Qu"

    case "$previous" in
        install|-S|-i|info|extract|convert)
            mapfile -t COMPREPLY < <(compgen -f -X '!*.deb' -- "$current")
            ;;
        remove|-R|-r|verify|-Qk|-V|files|-Ql|-L|-Qi|-s|repair|rollback|snapshot|license)
            mode_args=()
            if [[ " ${COMP_WORDS[*]} " == *" --user "* \
               || " ${COMP_WORDS[*]} " == *" -u "* ]]; then
                mode_args=(--user)
            fi
            packages="$(DEBARK_COMPLETION=1 debark "${mode_args[@]}" list --json 2>/dev/null \
                | python3 -c 'import json,sys; data=json.load(sys.stdin); print(" ".join(p["name"] for p in data.get("packages", [])))' 2>/dev/null)"
            mapfile -t COMPREPLY < <(compgen -W "$packages" -- "$current")
            ;;
        *)
            mapfile -t COMPREPLY < <(compgen -W "$commands --user --yes --dry-run --no-deps --no-color --quiet --verbose --json --threads --snapshot --sandbox --verify --profile --sha256 --gpg-signature --keyring" -- "$current")
            ;;
    esac
}

complete -F _debark_completions debark
