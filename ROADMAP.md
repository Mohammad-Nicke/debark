# Roadmap

This roadmap describes areas of work, not release promises. Priorities may change as the project and its users change.

## Current focus

- Keep the signed APT preview clear about stale indexes, suite coverage and repository trust.
- Keep CI green across supported Python versions and periodically check current Arch Linux.
- Improve the dependency map through sourced, reviewable contributions.

## Next areas to evaluate

- Improve the optional sandbox integration and document its effective permissions before considering any default behavior.
- Prepare reproducible release packaging and evaluate an AUR package.
- Add transactional updates for apps installed through the experimental APT integration, with preview and rollback.
- Let users optionally pin an expected signing-key fingerprint for each Debian source.

## Deferred

Automatic replacement of installed apps, remote registries, hosted build services, and additional architecture support need separate designs and maintenance capacity. APT index synchronization, package discovery, update checks and CVE checks exist as opt-in beta features; they are not yet a complete package-upgrade system.
