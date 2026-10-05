# Roadmap

This roadmap describes areas of work, not release promises. Priorities may change as the project and its users change.

## Current focus

- Expand security-focused tests for archive extraction, dependency mapping, and file integrity.
- Keep continuous checks working across supported Python versions and periodically check current Arch Linux.
- Document package trust limits and safe contribution practices.

## Next areas to evaluate

- Improve the optional sandbox integration and document its effective permissions before considering any default behavior.
- Prepare reproducible release packaging and evaluate an AUR package.
- Improve dependency mapping with sourced, reviewable contributions.
- Design authenticated Debian repository metadata handling before enabling APT index synchronization.

## Deferred

Automatic package upgrades, CVE feeds, hosted build services, remote registries, and additional architecture support need separate designs and maintenance capacity. They are not commitments for a particular release.
