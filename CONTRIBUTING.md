# Contributing

Bug reports and security reports are welcome. DebArk's current license does not permit third-party code changes, customized builds, or derivative versions. Do not submit source patches or copy project code into other work unless Mr.Nick has first granted written permission under a separate agreement.

## Before opening an issue or request

- Check existing issues and pull requests for duplicate work.
- Describe the affected command, expected behavior, and relevant system details.
- Never include Debian package payloads, credentials, personal data, or generated build output.

## Dependency mappings

The built-in Debian-to-Arch map is maintained by Mr.Nick. Reports about a possible mapping should include the Debian package name, Arch package name, target architecture, and a source confirming the Arch package provides the required library or command. A similar package name alone is not enough.

## Installer requirements

Keep required Arch package names in `dependencies/required-arch.txt`. The installer reads that manifest and uses pacman to install missing packages from the user's configured Arch repositories. Do not commit Arch package archives to the source tree: the weekly workflow resolves the full dependency closure and publishes a signed x86_64 bundle as a GitHub Release asset. The workflow builds its package list from the manifest, so update the installer documentation too when that list changes.

## Maintainer checks

The maintainer uses these checks from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/check-cli-help.py
bash -n install.sh completions/debark.bash
shellcheck install.sh completions/debark.bash
ruff check src tests
ruff format --check tests
```

Format changed Python files with `ruff format`. CI runs the unit suite on supported Python versions and a scheduled/manual compatibility check against current Arch Linux.

Tests must use temporary directories and synthetic fixtures. They must not invoke `pacman`, write to system paths, or execute maintainer scripts.

## Maintainer workflow

Source changes are maintained by Mr.Nick. Do not open a pull request containing code changes unless you have first received written permission under a separate agreement. Security reports and bug reports should use the channels described in `SECURITY.md`.
