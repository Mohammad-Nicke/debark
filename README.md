<div align="center">

# DebArk

**Run Debian apps on Arch Linux, without spreading their files across the system.**

[![Version](https://img.shields.io/github/v/tag/Mohammad-Nicke/debark?label=version)](https://github.com/Mohammad-Nicke/debark/tags)
[![License](https://img.shields.io/github/license/Mohammad-Nicke/debark)](UNLICENSE)
[![CI](https://github.com/Mohammad-Nicke/debark/actions/workflows/ci.yml/badge.svg)](https://github.com/Mohammad-Nicke/debark/actions/workflows/ci.yml)

</div>

DebArk installs a Debian <code>.deb</code> app into its own directory and gives you commands to inspect, verify, repair and remove it. It uses Arch packages for system libraries when it can find a reliable match.

The goal is to make the common path straightforward: point DebArk at a package, review the plan, and install it. DebArk does not run Debian maintainer scripts, so packages that depend on those scripts may need extra setup.

## Install DebArk

On Arch Linux, run the installer with <code>curl</code>:

~~~sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Mohammad-Nicke/debark/main/install.sh)"
~~~

Or use <code>wget</code>:

~~~sh
bash -c "$(wget -qO- https://raw.githubusercontent.com/Mohammad-Nicke/debark/main/install.sh)"
~~~

The installer always downloads the current source and required-package list from this GitHub repository. It first asks pacman to install missing packages through your configured Arch repositories. If those repositories cannot provide them, it downloads the current x86_64 package bundle from this project's GitHub Releases. The bundle includes the full dependency closure resolved from Arch's package database, upstream package signatures and checksums; pacman checks signatures with your system keyring. The required packages are Python and GNU binutils; binutils provides <code>ar</code>, which DebArk uses to read <code>.deb</code> files. No Python packages need to be downloaded from PyPI.

The release bundle is refreshed weekly and the installer refuses a bundle older than 14 days. To avoid a partial upgrade, it also refuses the fallback if a package in the dependency closure is older on the system than in the bundle. Update Arch fully with pacman first, then retry. Installation requires an internet connection to GitHub, pacman, a working Arch keyring and administrator access when installing system packages. The bundle does not replace pacman or make installing Arch packages possible on a non-Arch system. It currently covers x86_64; other architectures use their configured pacman repositories.

The command needs a downloader (<code>curl</code> or <code>wget</code>) to fetch the installer and source archive. It always installs the current version from GitHub, including when you start it from a local checkout.

<details>
<summary>Choose where DebArk is installed</summary>

The installer offers two modes:

| Mode | DebArk command | Apps and settings |
| --- | --- | --- |
| System-wide | <code>/usr/local/bin</code> | Apps under <code>/opt</code>; settings in <code>/etc/debark</code> |
| Current user | <code>~/.local/bin</code> | Apps under <code>~/.local/opt</code>; settings in <code>~/.config/debark</code> |

The interactive default is system-wide. When there is no terminal and the installer runs as an ordinary user, it chooses user mode. System-wide mode needs administrator access. User mode keeps DebArk's app files in your home directory, but installing required Arch packages still uses pacman and may ask for administrator access if those packages are missing.

The installer also adds Bash, Zsh and Fish completions, along with the <code>debark(1)</code> manual page.

</details>

## Install a Debian app

Use the interactive screen if you want DebArk to guide you:

~~~sh
debark --tui
~~~

Or install a package directly:

~~~sh
debark install ./app.deb
debark install --user ./app.deb
debark install --user https://example.org/app.deb
~~~

Before installing, DebArk reads the package metadata, checks its files and dependencies, and shows you the plan. In system mode, it can ask pacman to install mapped Arch dependencies. Uncertain name matches are only suggestions; DebArk will not install them automatically.

<details>
<summary>What does “isolated app directory” mean?</summary>

DebArk keeps an app's payload together under <code>/opt/APP</code> or <code>~/.local/opt/APP</code>. It records the files it installed and their hashes, and creates launchers that can use libraries bundled with that app. It does not copy the app's files into Arch's system library directories or register the Debian package in pacman's database.

This keeps installation and removal more contained. It does not make the app harmless: when launched, an app still runs with your user permissions and can access files or the network available to that user.

</details>

<details>
<summary>What are Debian maintainer scripts?</summary>

Debian packages can include scripts such as <code>preinst</code>, <code>postinst</code>, <code>prerm</code> and <code>postrm</code>. Debian normally runs them during installation or removal to perform extra setup. DebArk never runs these scripts. That avoids silently running package-provided commands with administrator access, but it also means an app that relies on one of them may not be fully configured.

</details>

## Use and manage installed apps

These are the commands most people need:

~~~sh
debark list
debark search QUERY
debark info app.deb
debark verify APP
debark repair APP
debark remove APP
~~~

Removal checks the recorded hashes first. If you changed a file after installation, DebArk leaves it in place and tells you about it.

<details>
<summary>Snapshots, rollback and other options</summary>

Save and restore a local copy of an installed app:

~~~sh
debark snapshot APP
debark rollback APP
~~~

Useful install options:

| Option | What it does |
| --- | --- |
| <code>--dry-run</code> | Show the plan without installing the app |
| <code>--no-deps</code> | Leave missing Arch dependencies for you to handle |
| <code>--yes</code> | Accept DebArk and pacman prompts without asking |
| <code>--snapshot</code> | Save a restore point after installation |
| <code>--verify</code> / <code>--no-verify</code> | Control the post-install file-hash check; verification is on by default |
| <code>--sandbox</code> | Disable network access and use a temporary, empty home in an available sandbox launcher; see the explanation below |
| <code>--sha256 HASH</code> | Require an exact package digest |
| <code>--gpg-signature FILE --keyring FILE</code> | Check a detached signature against a keyring you already trust |
| <code>--json</code> | Print structured output where supported |

For the full command list, run <code>debark --help</code> or read <code>man debark</code>.

Other commands include <code>extract</code> and <code>convert</code> for working with package files, <code>bulk</code> and <code>watch</code> for handling several packages, and <code>profile</code> for saving TUI preferences.

</details>

## Dependencies and updates

DebArk compares Debian dependency names with a built-in map, locally configured overrides, installed Arch packages and pacman's file-provider data. It can also inspect ELF library requirements with <code>readelf</code>. The built-in map is intentionally cautious: similar names alone are not enough to trigger an automatic install.

Run <code>debark update</code> to refresh Arch file-provider data used during dependency checks. This does not update installed Debian apps.

<details>
<summary>Try the experimental APT and security tools</summary>

The installer offers an opt-in beta preview for signed APT index sync, installing new packages from a synced index, Debian Security Tracker checks, and update detection. The preview may be incomplete or fail. It does not automatically replace already-installed apps.

If you skipped the prompt, enable the preview with <code>debark config experimental_features true</code>. Add a repository with a keyring you already trust, then sync and install from it:

~~~sh
debark repo add debian https://deb.debian.org/debian bookworm main --keyring /path/to/trusted-archive-keyring.gpg
debark repo sync debian
debark repo install debian PACKAGE [--sandbox]
debark cve --suite bookworm
debark upgrade
~~~

The APT preview accepts HTTPS repositories only. It verifies the signed <code>Release</code> file with <code>gpgv</code>, checks index hashes and sizes from that file, and verifies the downloaded package against its indexed SHA256. You must obtain and trust the keyring yourself; DebArk does not import keys or decide which publishers to trust. A missing <code>gpgv</code> command requires the Arch <code>gnupg</code> package.

The CVE command compares saved Debian package versions with tracker data for the suite you name. It can report uncertain results; check each advisory with Debian before acting. <code>debark upgrade</code> only lists newer versions from already-synced indexes. Replacing an installed package is not part of this beta preview yet.

</details>

## Sandbox and package safety

<details>
<summary>What does DebArk's sandbox option do?</summary>

A sandbox is an extra launcher layer that restricts an app's access to the system. With <code>--sandbox</code>, DebArk uses bubblewrap or Firejail if one is already installed. It disables network access, gives the app a temporary empty home and private temporary files, and exposes the system filesystem read-only. It hides the logged-in session's runtime sockets and removes common session-bus, SSH-agent, GPG-agent, X11 and Wayland environment variables.

Sandboxing is optional and is not enabled by default because it prevents network access and persistent home-directory settings, and may stop graphical apps or apps that need session services from working. A sandboxed app can still read system files permitted to your account. Treat it as an extra restriction, not a dependable security boundary for hostile software or a substitute for trusting the package source. If the selected runtime is unavailable when you launch the app, its launcher exits with an error instead of running it unsandboxed.

</details>

Before installing packages, keep these limits in mind:

- A SHA-256 digest only proves the file matches the digest you supplied; it does not identify who published it.
- Signature checks use only a keyring you provide. DebArk does not import keys or decide which publishers to trust.
- APT repository sync and install are experimental, require a trusted keyring, and support amd64 indexes only.
- Some packages require maintainer scripts, system services or Debian-specific setup. DebArk does not perform those steps automatically.

See [feature status](docs/FEATURES.md) for what is implemented and what is still planned, and [architecture](docs/ARCHITECTURE.md) for how installation works.

## Troubleshooting

<details>
<summary>DebArk says a required tool is missing</summary>

The installer downloads <code>dependencies/required-arch.txt</code> from GitHub and asks pacman to install missing packages. If configured repositories cannot supply them, it downloads the current signed package bundle from this repository's Releases. The installer requires an internet connection. If pacman needs administrator access, it uses <code>sudo</code> or <code>doas</code> when available. On Arch, you can also install the listed packages yourself through your normal pacman setup.

</details>

<details>
<summary>The installer has no terminal</summary>

In a non-interactive run, the installer accepts pacman's normal confirmation prompts automatically. If system packages are missing, it still needs root access or passwordless <code>sudo</code>/<code>doas</code>, because there is no terminal in which to enter a password. Run it from a terminal or start it as root if that access is unavailable.

</details>

<details>
<summary>An app's dependency could not be mapped</summary>

Check the package with <code>debark info</code>, then install the appropriate Arch package with pacman. You can also use <code>--no-deps</code> and manage dependencies yourself. DebArk will not guess an automatic mapping when confidence is low.

</details>

<details>
<summary>The app did not appear in the desktop menu</summary>

Only packages that include a usable desktop entry can be added to the menu. Some packages rely on their Debian maintainer scripts to create one; DebArk does not run those scripts. Check <code>debark info</code> and the package's desktop files to see what it provides.

</details>

## Project

Maintained by [Mr.Nick (@Mohammad-Nicke)](https://github.com/Mohammad-Nicke). DebArk is released under the [Unlicense](UNLICENSE).

Project guides: [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Roadmap](ROADMAP.md)
