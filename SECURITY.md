# Security policy

## What DebArk protects

DebArk checks Debian archive paths and links before extracting package payloads, keeps payload files under a package-specific directory, and records hashes so later verification can detect changes. It does not run Debian maintainer scripts such as `preinst`, `postinst`, `prerm`, or `postrm`.

These measures reduce specific installation risks; they do not make an untrusted package safe. A package's programs run with the permissions of the user who launches them. They may access that user's files and network, and system-mode installation can perform privileged operations.

## Trust limits

- A SHA-256 check proves a package matches the digest supplied by the caller. It does not establish who published the package.
- Detached signatures are checked only when the caller supplies a keyring they trust. DebArk does not import keys or establish source trust automatically.
- Applications are not sandboxed by default. The optional Firejail or bubblewrap launcher depends on the local runtime and its policy; do not rely on it as a security boundary for hostile software.
- Debian repository definitions are informational. DebArk does not currently fetch APT indexes or verify signed repository metadata.
- Manifest hashes help detect later file changes; they do not certify that the original package was benign.

## Reporting a vulnerability

Please do not publish an unpatched vulnerability in a public issue. Use GitHub's private vulnerability reporting for this repository when it is available. Otherwise, contact the maintainer through the GitHub profile linked from the repository and include reproduction steps, affected versions, and the impact. The maintainer will acknowledge reports as soon as practical and coordinate a fix and disclosure with the reporter.

Security fixes are prioritized for the latest release. Older releases may not receive patches; upgrade to the latest release when possible.
