# DebArk User Guide

This guide covers installing DebArk, installing Debian applications on Arch Linux, and maintaining those applications afterward. DebArk is maintained by **Mr.Nick (Mohammad Nick)**.

## What DebArk does

DebArk reads Debian `.deb` packages and places each application's files in its own managed directory. It records the files and their hashes, creates launchers where possible, and can inspect, verify, repair, snapshot, roll back, or remove managed applications.

DebArk does not replace pacman or run Debian maintainer scripts such as `postinst`. System-mode app installs are registered with pacman under a DebArk-specific package name; the Debian package itself is not treated as an Arch repository package. Some applications depend on maintainer scripts or Debian-specific services and may need manual setup. An installed application still runs with your normal user permissions; an isolated installation directory is not a security sandbox.

## Install or update DebArk

DebArk's online installer is the recommended setup path on Arch Linux:

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Mohammad-Nicke/debark/main/install.sh)"
```

If `curl` is unavailable, use `wget`:

```sh
bash -c "$(wget -qO- https://raw.githubusercontent.com/Mohammad-Nicke/debark/main/install.sh)"
```

The installer fetches the current source and dependency manifest from this repository. It first asks pacman to install missing requirements from configured Arch repositories. If that fails, the current x86_64 signed dependency bundle from GitHub Releases may be used. This fallback still needs GitHub access, a working Arch keyring, and a compatible package state; it is not an offline installer.

The dependency bundle is refreshed weekly. Since Arch is a rolling release, 14 days is a freshness recommendation only. An older signed fallback bundle no longer stops installation; signature, checksum, architecture, and pacman package-state checks remain active. DebArk still refuses a bundle that would downgrade a package already installed on the system.

DebArk itself is always installed system-wide:

| DebArk files | App files | Settings | DebArk data and cache |
| --- | --- | --- | --- |
| `/usr/local/bin` and `/usr/local/lib` | `/opt` for system apps, `~/.local/opt` for per-user apps | `/etc/debark` | `/var/lib/debark` |

The installer requests administrator access through `sudo` or `doas` when it writes system paths or installs Arch dependencies. DebArk commands that need system access request it through the same helper. App files can still be installed per-user with `debark install --user`; that option does not install a per-user copy of DebArk.

Re-running the installer replaces DebArk's system-wide program files. It keeps DebArk's configuration, registered Debian repositories, installed-app records, snapshots, and DebArk cache. `debark update` and `debark upgrade` check the signed source release after their usual work. A newer version or a newer maintenance commit within the same version is shown with its changelog notes and requires confirmation before installation. If the installed source is current, the self-update check is silent.

## Install a Debian application

For an interactive walkthrough:

```sh
debark --tui
```

For a local package or direct URL:

```sh
debark install ./application.deb
debark install --arch-system ./application.deb
debark install https://example.org/application.deb
```

Use `--user` for per-user app files and records:

```sh
debark install --user ./application.deb
```

Before installing, DebArk reads package metadata, checks archive paths, resolves declared dependencies, and shows a plan. In system mode, the app is registered with pacman as `debark-app-PACKAGE` while its Debian payload stays under `/opt/PACKAGE`. `--arch-system` makes system mode explicit for scripts; it cannot be combined with `--user`, and plain system installs use the same pacman-managed path. DebArk can offer mapped Arch dependencies to pacman. A name that is only a low-confidence suggestion is not installed automatically.

Common install options:

| Option | Effect |
| --- | --- |
| `--dry-run` | Show the proposed work without installing the app. |
| `--arch-system` | Explicitly require system mode and pacman registration; cannot be combined with `--user`. |
| `--no-deps` | Leave missing Arch dependencies for you to install yourself. |
| `--yes` | Accept DebArk prompts without pausing. |
| `--snapshot` | Save a restore point after installation. |
| `--sandbox` | Create a launcher that uses an available bubblewrap or Firejail sandbox. |
| `--sha256 HASH` | Require the downloaded or local package to match a known digest. |
| `--gpg-signature FILE --keyring FILE` | Verify a detached signature using a keyring you already trust. |
| `--verify` / `--no-verify` | Enable or skip the post-install file-hash check; verification is on by default. |

The digest and signature options verify the exact package against information or keys you supply. They do not decide whether you should trust the publisher.

## Everyday commands

```sh
debark list
debark search QUERY
debark info APP
debark files APP
debark verify APP
debark repair APP
debark remove APP
```

`info FILE.deb` inspects a package before installation; `info APP` shows a registered app. `verify` checks managed files against their saved hashes. `repair` attempts to restore missing or changed managed files from the recorded source. `remove` deletes unchanged managed files and leaves user-modified files in place.

System packages can also be queried or removed by pacman. DebArk keeps its own records for Debian metadata and recovery; if a package is removed directly with pacman, DebArk stops listing it after checking pacman's database.

DebArk also accepts familiar single-action pacman and dpkg shortcuts:

| Action | Long form | Short form |
| --- | --- | --- |
| Install | `debark install FILE.deb` | `debark -S FILE.deb`, `debark -i FILE.deb` |
| Remove | `debark remove APP` | `debark -R APP`, `debark -r APP` |
| List | `debark list` | `debark -Q`, `debark -l` |
| Search installed apps | `debark search TEXT` | `debark -Qs TEXT` |
| App information | `debark info APP` | `debark -Qi APP`, `debark -s APP` |
| Managed files | `debark files APP` | `debark -Ql APP`, `debark -L APP` |
| Verify files | `debark verify APP` | `debark -Qk APP`, `debark -V APP` |
| Refresh metadata | `debark update` | `debark -Fy` |
| Check upgrade status | `debark upgrade` | `debark -Qu` |

Short forms run one DebArk action. Combined operations such as `pacman -Syu` are not supported and do not update Arch Linux.

## Updates: DebArk, Arch, and Debian apps

The word “update” can refer to three different things:

- `pacman -Syu` updates Arch Linux and its Arch packages.
- `debark update` refreshes DebArk's local dependency map and, in system mode, pacman's file-provider data. It then checks the signed source version and commit and asks before replacing DebArk itself.
- `debark upgrade` lists newer versions for apps installed through the optional, already-synced APT integration. It does not replace those apps. It also checks DebArk's signed source and can offer a version or maintenance update.

The DebArk self-update replaces its system-wide program files and keeps its settings, repository list, package records, snapshots, and DebArk cache. It does not upgrade Arch packages or replace installed Debian applications.

## Dependencies and desktop integration

DebArk uses its built-in Debian-to-Arch dependency map, local overrides, installed Arch packages, pacman's file-provider information, and (when available) ELF metadata from `readelf`. The built-in map is cautious: uncertain package-name similarity is shown as a hint rather than treated as authorization to install a dependency.

When an installed package provides desktop entries or icons, DebArk places the integration in its managed locations and refreshes the desktop and icon caches when the matching system tools are available. Packages that rely on Debian maintainer scripts to generate those files may need manual setup.

## Optional sandbox

The `--sandbox` option creates a launcher using bubblewrap or Firejail if one is installed. The launcher disables network access, gives the app a temporary home and private temporary files, and exposes system paths read-only. It also hides common session sockets and agent variables.

Sandboxing is optional because it can break applications that need a network, persistent settings, or desktop-session services. It is an extra restriction, not a guarantee that an unknown application is safe. If the requested sandbox runtime is missing at launch time, the generated launcher stops with an error instead of silently running the app without the sandbox.

## Recover an older installation

Run `debark fix` after upgrading if package records are missing or use an older layout. It migrates saved records and reconstructs system-mode entries from pacman's database. `debark scan` lists older app directories that may belong to DebArk. After reviewing them, `debark fix --adopt` asks before registering each candidate. Adoption records current files and hashes; it does not reinstall the app or run Debian maintainer scripts. Files in legacy system launcher and desktop directories are adopted only when they still carry a DebArk ownership marker, and pacman-owned files are left alone.

## Optional Debian repositories and security checks

DebArk always installs `apt`, `apt-get`, `apt-cache`, and `dpkg` command shortcuts. It checks these links at startup and installs a pacman post-transaction hook for changes to packages named `apt` or `dpkg`. Run `debark fix-shortcuts` to check or restore them manually. The original commands remain pacman-managed in `/usr/bin`; DebArk never replaces those files or changes pacman's package database. Repository integration remains an opt-in beta feature. Enable that preview with:

```sh
debark config experimental_features true
```

Add a repository with a keyring you already trust, then synchronize its index:

```sh
debark repo add debian https://deb.debian.org/debian bookworm main \
  --keyring /path/to/trusted-archive-keyring.gpg
debark repo sync debian
debark repo search browser --repo debian
debark repo info PACKAGE --repo debian
debark repo install debian PACKAGE
```

The beta path accepts HTTPS repositories, verifies signed Release metadata with the supplied keyring, checks index metadata and downloaded package hashes, and supports amd64 indexes. DebArk does not import keys or choose which publishers to trust. Local package installation through `dpkg -i` and `apt-get install -f` inventory recovery do not need the repository preview.

When enabled, the wrappers support common requests for DebArk-managed applications:

```sh
apt-get update
apt-get install PACKAGE
apt-get install --only-upgrade PACKAGE
apt-get install -f -y
apt-get upgrade
apt-get remove PACKAGE
apt-cache search TEXT
apt-cache show PACKAGE
apt list --installed
dpkg --status PACKAGE
dpkg --listfiles PACKAGE
dpkg -i /path/to/package.deb
```

Package versions from repositories come from the verified indexes you have synced. APT package dependencies are handled through DebArk's Debian-to-Arch mapping; DebArk does not implement the full APT dependency solver. `apt-get install -f -y` without package names asks DebArk to reconcile its installed-app inventory, then returns so an updater can install a local package. `dpkg -i FILE.deb` sends each local package through DebArk's install or replacement flow, including dependency checks and the normal DebArk records. It does not register the package in dpkg's database. Batch changes run as separate app transactions, so an error partway through can leave earlier successful operations in place. The wrappers do not run Debian maintainer scripts. Operations that could change or remove unrelated system packages, including `full-upgrade`, `dist-upgrade`, `autoremove`, and direct dpkg database changes, are rejected. The original Arch commands remain package-managed and are not overwritten; use `apt-real`, `apt-get-real`, `apt-cache-real`, or `dpkg-real` through DebArk to run them. Normal PATH lookups resolve the shortcuts because the installer places them in `/usr/local/bin` and the invoking user's `~/.local/bin`.

PATH shortcuts cannot capture an explicit path such as `/usr/bin/apt` or a privileged service that discards `/usr/local/bin` from its PATH. Refresh a shell with `hash -r` in Bash or `rehash` in Zsh. For a service using `pkexec`, configure it to invoke `/usr/local/bin/apt-get` explicitly. Pacman remains able to update its original tools because DebArk never overwrites package-owned files.

When a caller uses `bash -c`, the command and its arguments must be one quoted string. For example, this form selects the DebArk wrapper after `pkexec` starts the shell:

```sh
pkexec --disable-internal-agent /bin/bash -c '/usr/local/bin/apt-get install -f -y'
```

The dpkg wrapper supports package listing, status, file listing, architecture queries, and local installation with `-i` or `--install`. Other dpkg mutations such as `--unpack` or `--configure` are rejected. The wrappers cover common package operations; they do not reproduce every APT option or output format. Unknown commands and options produce an error instead of being passed to the original APT binary.

With the preview enabled, `debark cve --suite bookworm` compares recorded Debian app versions against Debian Security Tracker data. Results are best-effort; confirm advisories with Debian before acting. Only suites present in the fetched tracker data are accepted.

## Restore points, profiles, and batch work

Save a restore point and roll back if needed:

```sh
debark snapshot APP
debark rollback APP
```

Other tools include `debark extract FILE.deb` to inspect/extract package payload without installing, `debark convert FILE.deb` to build an Arch package, `debark export` and `debark import FILE` to move package source records, `debark bulk install|remove FILE` for a text list, and `debark watch DIRECTORY` to monitor for new `.deb` files. Review `debark --help` for exact options before using these on a large set of packages.

`debark --tui --profile NAME` loads saved interactive choices. Profiles contain DebArk preferences; they are separate from package databases and repository definitions.

## Troubleshooting

### The installer cannot find a required tool

The online installer needs `curl` or `wget` to download itself. It then uses pacman for the required Arch packages and may need administrator access. If the configured mirrors are unavailable, check them and the system keyring, then retry. The GitHub dependency bundle is a fallback for x86_64. If it is over 14 days old, the installer warns and continues; signature, checksum, architecture, and pacman package-state checks still apply.

### An app does not launch

Check `debark info APP` and `debark verify APP`, then inspect missing library dependencies and the app's desktop entries. DebArk does not run maintainer scripts or create Debian services automatically. Some applications need additional Arch packages or manual configuration.

### A package dependency is unmapped

Use `debark info FILE.deb` to inspect its declared dependencies. Install the appropriate Arch package with pacman, or use `--no-deps` and manage dependencies yourself. DebArk does not guess when its mapping confidence is low.

### The sandboxed app fails

Try the application without `--sandbox` only if you trust its source and understand that it then runs with your normal user access. Some graphical programs require desktop-session services or networking that the sandbox intentionally hides.

### Where are my settings and data?

The system installation uses `/etc/debark` and `/var/lib/debark`; old per-user settings and records remain under `~/.config/debark` and `~/.local/share/debark`. Re-running the installer does not remove existing data directories.

## More help

Run `debark --help`, `debark help install`, or `man debark` for command details. Report security issues using [SECURITY.md](../SECURITY.md), and see [ARCHITECTURE.md](ARCHITECTURE.md) for implementation notes.

DebArk is maintained by **Mr.Nick (Mohammad Nick)**. It is distributed under the [DebArk Source-Available License 1.0](../LICENSE): it allows use of the complete, unmodified program, but not customized versions or reuse of code fragments. The project is source-available, not open source.
