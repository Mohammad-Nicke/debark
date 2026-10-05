# Feature status

DebArk is a local helper for running Debian apps on Arch. This page is a quick guide to what it handles today, where it relies on another system tool, and what it does not do yet.

## Local package lifecycle

| Capability | Status |
|---|---|
| Local and direct HTTP(S) `.deb` install | Implemented |
| Installer dependency setup | Downloads the package list and source from GitHub; tries configured pacman repositories, then a full signed x86_64 dependency closure from GitHub Releases |
| System and user modes | Implemented; system is the default |
| `ar` and tar extraction with path checks | Implemented |
| Maintainer-script execution | Never performed |
| List, search, info, files, verify, remove | Implemented |
| Manifest migration and per-package manifests | Implemented for legacy map and v3 data |
| xattr package/version/hash/time markers | Best-effort, with manifest hash fallback |
| Parallel payload copy | Implemented |
| Repair from the saved package source | Implemented for payload files; external launchers need manual review |
| SHA256 and detached GPG verification | Implemented when expected digest or trusted keyring is supplied |
| Version pins | Implemented for install-time enforcement |
| Snapshot and rollback | Local compressed payload snapshots; BTRFS/ZFS/LVM snapshots are not used |
| JSON and quiet output | Implemented for structured command output; interactive watch is not JSON |

## Dependency resolution and launch

| Capability | Status |
|---|---|
| Built-in Debian-to-Arch map and local overrides | Implemented |
| Installed Arch package and pacman file-database lookups | Implemented when pacman metadata is available |
| ELF `DT_NEEDED` inspection via `readelf` | Implemented |
| SONAME provider lookup via pacman file database | Implemented when its index is current |
| Offline ranker under 20 MiB | Implemented; hint-only, never autoruns pacman |
| Isolated per-package payload | Implemented |
| Private library paths in generated wrappers | Implemented for bundled library directories |
| Firejail/bubblewrap launcher | Optional integration; disables network, uses a temporary home, private temporary/device views, hides common session sockets and makes the system filesystem read-only; runtime must already be installed |
| Automatic library copying, seccomp profiles, cgroups, AppArmor generation | Deferred |
| Pre-flight pacman dependency transaction | Partial: dependency ownership and availability are checked; pacman performs its own transaction |

The sandbox option adds a launcher that uses an already-installed bubblewrap or Firejail. It requests network isolation, a temporary empty home, private temporary files and devices, and read-only access to the system filesystem. It also hides common session sockets. It remains optional because some apps need network access, writable settings, a display server or session services. The runtime and kernel must support the requested restrictions; this should not be treated as a dependable security boundary around hostile software.

## Local operations and integration

| Capability | Status |
|---|---|
| Package extraction and `.pkg.tar.zst` conversion | Implemented with explicit output paths and no overwrite |
| Bulk install/remove from a text list | Implemented |
| Export/import source records | Implemented; import requires local or direct URL sources |
| Directory watch | Implemented with stable-file polling and confirmation |
| TUI profiles | Implemented for install preferences |
| Local Python hooks | Implemented, disabled unless explicitly enabled; plugins are trusted code |
| Shell completions and man page | Bash, Zsh, Fish and `debark(1)` included |
| Audit log, statistics, marker scan, garbage collection | Implemented |
| License inventory | Best effort from package control metadata and documentation |
| CVE scan | Experimental opt-in; compares installed Debian versions to Debian Security Tracker data for a selected suite |
| Debian source definition management | Saved locally; HTTPS only for experimental sync |
| Signed APT index sync and new-package install | Experimental opt-in; detached Release signature, signed SHA256/size checks, and x86_64/amd64 only |
| Desktop entries and launch wrappers | Implemented; existing destinations are not replaced |
| MIME, DBus, systemd, udev, cron and Polkit registration | Deferred |

`update` refreshes Arch file-provider data used by dependency checks. `upgrade` can detect newer versions from experimental indexes, but it does not replace installed apps. All APT and CVE features are opt-in beta and may fail.

## Hosted and community capabilities

Remote registries, community mappings, reputation systems, hosted dashboards, cloud synchronization, remote binary caches, P2P, auto-update services, GUI shells, and cross-distro package conversion are deferred. The dependency ranker is a small local rules-and-weights helper; it performs no network requests.

## Project quality

| Capability | Status |
|---|---|
| Unit and focused archive-security tests | Implemented with synthetic fixtures plus a real `.deb` container install/remove lifecycle in temporary user paths |
| GitHub Actions on pushes and pull requests | Implemented for Python 3.10–3.13; GitHub Action dependencies are checked weekly by Dependabot |
| Current Arch rolling compatibility check | Scheduled weekly and available by manual workflow dispatch |
| Coverage report | Uploaded as a workflow artifact; no hosted coverage badge is configured |
| Dependency-map contribution guidance | Documented in `CONTRIBUTING.md` |
