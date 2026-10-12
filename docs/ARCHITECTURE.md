# Architecture

## Installer and system packages

The installer always downloads the current project source archive from GitHub, then reads `dependencies/required-arch.txt`. It first asks pacman to install missing packages from the machine's configured repositories, using `sudo` or `doas` when needed. If that transaction fails, it can download the current x86_64 package bundle from the existing `0.1` GitHub Release. The weekly bundle contains the full dependency closure resolved from Arch's sync database, plus upstream signatures and checksums. Because Arch is a rolling release, 14 days is displayed as a freshness recommendation, but an older bundle does not stop installation. Pacman remains responsible for signature checks, dependency validation and the package transaction. The installer refuses to install a bundled package over an older installed version, which avoids partial system upgrades.

DebArk is installed system-wide under `/usr/local`, `/etc/debark`, and `/var/lib/debark`. The installer stages the new Python package and entry point and replaces those program files. Existing configuration and data directories are not removed: repository definitions, package records, snapshots, and DebArk's own caches remain in place. Application payloads may still be installed per-user.

The package archive is published as a GitHub Release asset rather than stored in the Git source tree. Its existing asset is refreshed weekly without creating a version or tag. The installer downloads this bundle from GitHub when needed; it does not accept a locally supplied bundle, so installation requires an internet connection.

The required packages are Python, GNU binutils, OpenSSL, libarchive, and zstd. Python runs DebArk, binutils provides `ar` for reading Debian packages, and libarchive plus zstd provide the tools used to build local ALPM packages for system-mode apps. Optional sandbox runtimes are not required for a normal install.

`debark update` refreshes the local dependency map and pacman's file-provider database, then checks the installed version against `src/debark/__init__.py` on the project's `main` branch. `debark upgrade` checks app versions in previously synced beta indexes, then performs the same DebArk version check. A newer DebArk version is shown with its matching changelog section and requires an interactive confirmation before the installer is downloaded and run. The installer always updates DebArk system-wide; it does not replace Debian apps.

## Runtime layout

- `debark` locates the source or installed Python package and dispatches to its CLI.
- `src/debark/cli.py` is the command entry point; `engine.py` owns archive handling, dependency inspection, transactions, and CLI commands.
- `resolver.py` loads a tiny local ranker. It only suggests names; it cannot authorize package installation.
- `plugins.py` loads only explicitly enabled local hooks after checking ownership and write permissions.

The runtime uses the Python standard library. System dependencies are invoked with argument arrays; package-controlled strings are not passed to a shell.

## Install flow

1. Resolve the selected system or user paths and check privilege requirements.
2. Download an HTTP(S) source if requested, then optionally verify its SHA256 or a detached signature against a caller-supplied keyring.
3. Read the Debian `ar` container and safely extract its control and data archives. Reject absolute paths, traversal, unsafe links, and unsupported special files.
4. Read package metadata, map declared Debian dependencies, inspect ELF `DT_NEEDED` entries, and query local Arch package and file metadata.
5. Show the plan and request confirmation. Only trusted package-name mappings are eligible for pacman; low-confidence local ranker results are hints.
6. Copy payload into a private staging directory, record hashes and xattrs, generate wrappers, verify the staged result, then rename the app tree into place.
7. Save the v3 manifest, database record, optional local snapshot, and append-only audit event.

Debian maintainer scripts are never invoked. In system mode, DebArk builds a small local ALPM package named `debark-app-PACKAGE`, with the Debian payload under `/opt/PACKAGE` and generated launchers and desktop entries in managed system locations. Pacman owns those files and handles package upgrades and removal. User mode keeps the payload and launchers in the user's directories without registering a system package. Existing paths are not silently overwritten.

## State

System mode uses `/etc/debark` and `/var/lib/debark`; user mode uses the caller's `~/.config/debark` and `~/.local/share/debark`. The database is a v3 envelope containing package manifests. A lock file serializes database updates, and writes use a same-directory temporary file followed by atomic replacement.

Each package record includes source metadata, source SHA256, installation mode, dependencies, ELF analysis, license declarations, and file records. File records contain a path, SHA256, size, mode, and whether the optional xattr marker was written. User-mode app markers can be refreshed from the central database; system-mode markers are part of the pacman-owned package and are never rewritten by database migration. If the central system inventory is lost, DebArk rebuilds records from the namespaced pacman package and its file list. The `fix` command migrates older records, then only offers explicit adoption for unregistered app directories that match DebArk markers or a Debian payload layout. The audit log is JSON Lines. Local restore points contain a compressed payload archive plus the corresponding manifest.

## Trust boundaries

- Archive paths are validated before extraction. Symlinks are normalized and must resolve inside the extraction root.
- Pacman-owned paths are not destinations for package payload. Existing launcher and desktop paths are rejected.
- System package removal and upgrades go through pacman. For legacy unmanaged records, removal checks pacman ownership, recorded hashes, and path containment before unlinking. User-modified and pacman-owned content is kept.
- Rollback checks the snapshot and manifest hashes before swapping the app directory. Untracked current files block rollback.
- Package scripts are data only and are never executed by DebArk.
- Sandbox support is an optional launcher integration with Firejail or bubblewrap. It disables networking, uses a temporary home and private temporary/device views, and exposes the system filesystem read-only. It is not enabled by default, may break apps that need networking or persistent settings, and is not a reliable security boundary for hostile applications.
- Experimental APT sync verifies signed Release metadata with a caller-selected keyring before accepting index checksums. Experimental package downloads are checked against the signed index before DebArk extracts them. System-wide and invoking-user aliases for apt, apt-get, apt-cache, and dpkg dispatch through the verified DebArk entry point. Suffix aliases such as `apt-real` and `dpkg-real` hand off to the original `/usr/bin` tools only after DebArk verifies its official installation. `dpkg -i` sends local Debian packages through DebArk's install flow, while `apt-get install -f` without operands reconciles the DebArk inventory. Package changes remain individual DebArk transactions; unsupported operations fail rather than invoking Debian maintainer scripts or changing unrelated system packages. Absolute `/usr/bin` paths bypass PATH aliases and remain pacman-owned.

## Deferred interfaces

APT repository definitions are saved locally. The beta sync verifies signed Release metadata before accepting package candidates; only repository-backed APT operations require enabling the preview. Hosted registries, remote build services, cloud sync, web APIs, binary caches, P2P, and community-maintained mappings are deferred.
