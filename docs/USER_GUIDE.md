# DebArk User Guide

This guide covers installing DebArk, installing Debian applications on Arch Linux, and maintaining those applications afterward. DebArk is maintained by **Mr.Nick (Mohammad Nick)**.

## What DebArk does

DebArk reads Debian `.deb` packages and places each application's files in its own managed directory. It records the files and their hashes, creates launchers where possible, and can inspect, verify, repair, snapshot, roll back, or remove managed applications.

DebArk does not replace pacman, register Debian packages in pacman's database, or run Debian maintainer scripts such as `postinst`. Some applications depend on those scripts or Debian-specific services and may need manual setup. An installed application still runs with your normal user permissions; an isolated installation directory is not a security sandbox.

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

The installer supports two layouts:

| Mode | Command | App files | Settings | DebArk data and cache |
| --- | --- | --- | --- | --- |
| System-wide | `/usr/local/bin/debark` | `/opt` | `/etc/debark` | `/var/lib/debark` |
| Per-user | `~/.local/bin/debark` | `~/.local/opt` | `~/.config/debark` | `~/.local/share/debark` |

System-wide installation needs administrator access when it writes system paths or installs Arch dependencies. The trusted installed command requests access through `sudo` or `doas` when needed. A per-user command selects its matching per-user data automatically; `--user` can also be added explicitly.

Re-running the installer replaces DebArk's program files in the selected mode. It keeps DebArk's configuration, registered Debian repositories, installed-app records, snapshots, and DebArk cache. `debark update` and `debark upgrade` check the program version after their usual work. When a newer DebArk version is found, the command shows the matching changelog notes and asks before installing it. If no newer version is available, the self-update check is silent.

## Install a Debian application

For an interactive walkthrough:

```sh
debark --tui
```

For a local package or direct URL:

```sh
debark install ./application.deb
debark install https://example.org/application.deb
```

Use `--user` for per-user app files and records:

```sh
debark install --user ./application.deb
```

Before installing, DebArk reads package metadata, checks archive paths, resolves declared dependencies, and shows a plan. In system mode it can offer mapped Arch dependencies to pacman. A name that is only a low-confidence suggestion is not installed automatically.

Common install options:

| Option | Effect |
| --- | --- |
| `--dry-run` | Show the proposed work without installing the app. |
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
- `debark update` refreshes DebArk's local dependency map and, in system mode, pacman's file-provider data. It then checks for a newer DebArk program version and asks before replacing DebArk itself.
- `debark upgrade` lists newer versions for apps installed through the optional, already-synced APT integration. It does not replace those apps. It also checks DebArk's own version and can offer to update DebArk.

The DebArk self-update uses the installation mode already in use. It replaces DebArk's program files and keeps its settings, repository list, package records, snapshots, and DebArk cache. It does not upgrade Arch packages or replace installed Debian applications.

## Dependencies and desktop integration

DebArk uses its built-in Debian-to-Arch dependency map, local overrides, installed Arch packages, pacman's file-provider information, and (when available) ELF metadata from `readelf`. The built-in map is cautious: uncertain package-name similarity is shown as a hint rather than treated as authorization to install a dependency.

When an installed package provides desktop entries or icons, DebArk places the integration in its managed locations and refreshes the desktop and icon caches when the matching system tools are available. Packages that rely on Debian maintainer scripts to generate those files may need manual setup.

## Optional sandbox

The `--sandbox` option creates a launcher using bubblewrap or Firejail if one is installed. The launcher disables network access, gives the app a temporary home and private temporary files, and exposes system paths read-only. It also hides common session sockets and agent variables.

Sandboxing is optional because it can break applications that need a network, persistent settings, or desktop-session services. It is an extra restriction, not a guarantee that an unknown application is safe. If the requested sandbox runtime is missing at launch time, the generated launcher stops with an error instead of silently running the app without the sandbox.

## Optional Debian repositories and security checks

The APT integration is an opt-in beta feature. Enable it in the installer or with:

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

The beta path accepts HTTPS repositories, verifies signed Release metadata with the supplied keyring, checks index metadata and downloaded package hashes, and supports amd64 indexes. DebArk does not import keys or choose which publishers to trust. It can install a new app from a synced index, but it cannot yet update an app already installed from APT transactionally.

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

The online installer needs `curl` or `wget` to download itself. It then uses pacman for the required Arch packages and may need administrator access. If the configured mirrors are unavailable, check them and the system keyring, then retry. The GitHub dependency bundle is a fallback for x86_64 and is time-limited to avoid installing a stale partial system upgrade.

### An app does not launch

Check `debark info APP` and `debark verify APP`, then inspect missing library dependencies and the app's desktop entries. DebArk does not run maintainer scripts or create Debian services automatically. Some applications need additional Arch packages or manual configuration.

### A package dependency is unmapped

Use `debark info FILE.deb` to inspect its declared dependencies. Install the appropriate Arch package with pacman, or use `--no-deps` and manage dependencies yourself. DebArk does not guess when its mapping confidence is low.

### The sandboxed app fails

Try the application without `--sandbox` only if you trust its source and understand that it then runs with your normal user access. Some graphical programs require desktop-session services or networking that the sandbox intentionally hides.

### Where are my settings and data?

System mode uses `/etc/debark` and `/var/lib/debark`; per-user mode uses `~/.config/debark` and `~/.local/share/debark`. The latter data directory contains DebArk's cache and package records. Re-running the installer or a DebArk self-update does not remove these directories.

## More help

Run `debark --help`, `debark help install`, or `man debark` for command details. Report security issues using [SECURITY.md](../SECURITY.md), and see [ARCHITECTURE.md](ARCHITECTURE.md) for implementation notes.

DebArk is maintained by **Mr.Nick (Mohammad Nick)**. New material is distributed under the [DebArk Source-Available License 1.0](../LICENSE): it allows use of the complete, unmodified program, but not customized versions or reuse of code fragments. The project is source-available, not open source. Earlier MIT-licensed copies remain under their original terms; see [LICENSE-MIT-LEGACY](../LICENSE-MIT-LEGACY).
