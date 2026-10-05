# DebArk Fish completion · Mr.Nick (@Mohammad-Nicke)
# https://github.com/Mohammad-Nicke/debark
set -l commands install remove list search info files verify scan update upgrade config doctor repo cve repair gc log stats snapshot rollback extract convert export import bulk watch profile plugin pin license help about -S -i -R -r -Q -l -Qs -Ss -Qi -s -Si -Ql -L -Qk -V -Fy -Qu
complete -c debark -f -n '__fish_use_subcommand' -a "$commands"
complete -c debark -s S -d 'Install a Debian package (pacman-style)'
complete -c debark -s i -d 'Install a Debian package (dpkg-style)'
complete -c debark -s R -d 'Remove a DebArk package (pacman-style)'
complete -c debark -s r -d 'Remove a DebArk package (dpkg-style)'
complete -c debark -s Q -d 'List installed DebArk packages'
complete -c debark -s l -d 'List installed DebArk packages (dpkg-style)'
complete -c debark -s s -d 'Show installed package details (dpkg-style)'
complete -c debark -s L -d 'List managed files (dpkg-style)'
complete -c debark -s V -d 'Verify managed package files'
complete -c debark -s u -l user -d 'Use per-user paths'
complete -c debark -s y -l yes -d 'Skip confirmation prompts'
complete -c debark -l dry-run -d 'Preview without installing'
complete -c debark -l no-deps -d 'Skip dependency installation'
complete -c debark -l no-color -d 'Disable colored output'
complete -c debark -s q -l quiet -d 'Suppress informational output'
complete -c debark -s v -l verbose -d 'Show diagnostic details'
complete -c debark -l json -d 'Emit machine-readable output where supported'
complete -c debark -l threads -x -d 'Parallel copy workers'
complete -c debark -l snapshot -d 'Save a local restore point'
complete -c debark -l sandbox -d 'Launch through an available sandbox runtime'
complete -c debark -n '__fish_seen_subcommand_from install -S -i info -Qi -s extract convert' -F

function __debark_package_names
    set -l mode_args
    if contains -- --user (commandline -opc); or contains -- -u (commandline -opc)
        set mode_args --user
    end
    env DEBARK_COMPLETION=1 debark $mode_args list --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(p["name"] for p in d.get("packages", [])))' 2>/dev/null
end

complete -c debark -n '__fish_seen_subcommand_from remove -R -r verify -Qk -V files -Ql -L info -Qi -s repair snapshot rollback license' -a '(__debark_package_names)'
