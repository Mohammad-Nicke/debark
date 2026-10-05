# Changelog

## 0.1 — 2026-10-05

- Install Debian packages in separate application directories on Arch Linux without running package maintainer scripts.
- Inspect package metadata and dependencies, map Debian dependencies to Arch packages, and check installed files against recorded hashes.
- Repair or remove managed files, create local snapshots, and roll back to a saved snapshot.
- Verify package digests and optional detached signatures, pin package versions, and export or import package records.
- Work with local packages through extraction and conversion commands, plus bulk operations, profiles, and directory watching.
- Search and inspect packages from already-synced experimental Debian indexes; reject CVE suite names that are absent from the tracker data.
- Invalidate repository caches when source settings change and discard them when a source is removed.
- Accept familiar pacman and dpkg shortcuts for common install, remove, query and file operations.
- Use the interactive terminal interface, shell completions, and the `debark(1)` manual page.
- Install from GitHub with pacman; when configured Arch repositories cannot provide required packages, use the signed x86_64 dependency bundle published in GitHub Releases.
