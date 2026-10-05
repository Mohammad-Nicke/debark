# Architecture

## Installer and system packages

The installer always downloads the current project source archive from GitHub, then reads `dependencies/required-arch.txt`. It first asks pacman to install missing packages from the machine's configured repositories, using `sudo` or `doas` when needed. If that transaction fails, it can download the current x86_64 package bundle from the repository's rolling GitHub Release. The weekly bundle contains the full dependency closure resolved from Arch's sync database, plus upstream signatures and checksums, and is rejected after 14 days. Pacman remains responsible for signature checks, dependency validation and the package transaction. The installer refuses to install a bundled package over an older installed version, which avoids partial system upgrades.

The rolling package archives are published as a GitHub Release asset rather than stored in the Git source tree. The installer downloads this bundle from GitHub when needed; it does not accept a locally supplied bundle, so installation requires an internet connection.

The required packages are Python and GNU binutils. Python runs DebArk, and binutils provides `ar` for reading Debian packages. Tools for optional features, such as signature checking or sandbox launchers, are not required for a normal install.

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

Debian maintainer scripts are never invoked. The payload remains inside one package directory. Generated launchers and desktop files are separately tracked and are not overwritten when a path already exists.

## State

System mode uses `/etc/debark` and `/var/lib/debark`; user mode uses the caller's `~/.config/debark` and `~/.local/share/debark`. The database is a v3 envelope containing package manifests. A lock file serializes database updates, and writes use a same-directory temporary file followed by atomic replacement.

Each package record includes source metadata, source SHA256, installation mode, dependencies, ELF analysis, license declarations, and file records. File records contain a path, SHA256, size, mode, and whether the optional xattr marker was written. The audit log is JSON Lines. Local restore points contain a compressed payload archive plus the corresponding manifest.

## Trust boundaries

- Archive paths are validated before extraction. Symlinks are normalized and must resolve inside the extraction root.
- Pacman-owned paths are not destinations for package payload. Existing launcher and desktop paths are rejected.
- Removal checks recorded hashes and containment before unlinking. User-modified content is kept.
- Rollback checks the snapshot and manifest hashes before swapping the app directory. Untracked current files block rollback.
- Package scripts are data only and are never executed by DebArk.
- Sandbox support is an optional launcher integration with Firejail or bubblewrap. It requests network isolation, read-only home access, and private temporary/device views. It is not enabled by default, and behavior depends on the installed runtime and kernel; do not treat it as a reliable security boundary for hostile applications.

## Deferred interfaces

Repository definitions are local metadata only. Remote Debian index sync must verify signed Release metadata before package candidates can be trusted. Hosted registries, remote build services, cloud sync, web APIs, binary caches, P2P, and community-maintained mappings are deferred.
