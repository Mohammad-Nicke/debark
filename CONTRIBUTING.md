# Contributing

Bug reports, documentation improvements, and carefully sourced dependency mappings are welcome.

## Before opening a pull request

- Check existing issues and pull requests for duplicate work.
- Keep changes focused and explain the user-visible behavior they affect.
- Do not add package-controlled script execution or broaden privileged operations without documenting the trust boundary.
- Never include real Debian package payloads, credentials, personal data, or generated build output in a change.

## Dependency mappings

For each proposed Debian-to-Arch mapping, provide the Debian package name, Arch package name, target architecture, and a source that confirms the Arch package provides the required library or command. A similar package name alone is not enough. Keep uncertain suggestions out of the automatic built-in map.

## Installer requirements

Keep required Arch package names in `dependencies/required-arch.txt`. The installer reads that manifest and uses pacman to install missing packages from the user's configured Arch repositories. Do not commit Arch package archives to the source tree: the weekly workflow resolves the full dependency closure and publishes a signed x86_64 bundle as a GitHub Release asset. The workflow builds its package list from the manifest, so update the installer documentation too when that list changes.

## Local checks

DebArk uses Python's standard library for runtime code. From the repository root, run:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m debark --help
bash -n install.sh completions/debark.bash
shellcheck install.sh completions/debark.bash
ruff check src tests
ruff format --check tests
```

Format changed Python files with `ruff format`. CI runs the unit suite on supported Python versions and a scheduled/manual compatibility check against current Arch Linux.

Tests must use temporary directories and synthetic fixtures. They must not invoke `pacman`, write to system paths, or execute maintainer scripts.

## Pull requests

Include a concise summary, the reason for the change, and the checks you ran. For security-sensitive changes, describe the threat addressed and any remaining limitation. Avoid combining unrelated formatting and behavior changes.
