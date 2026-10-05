# Security policy

## What DebArk protects

DebArk checks Debian archive paths and links before extracting package payloads, keeps payload files under a package-specific directory, and records hashes so later verification can detect changes. It does not run Debian maintainer scripts such as `preinst`, `postinst`, `prerm`, or `postrm`.

These measures reduce specific installation risks; they do not make an untrusted package safe. A package's programs run with the permissions of the user who launches them. They may access that user's files and network, and system-mode installation can perform privileged operations.

## Trust limits

- A SHA-256 check proves a package matches the digest supplied by the caller. It does not establish who published the package.
- Detached signatures are checked only when the caller supplies a keyring they trust. DebArk does not import keys or establish source trust automatically.
- Applications are not sandboxed by default. `--sandbox` asks Firejail or bubblewrap to disable networking, provide a temporary home and private temporary/device views, and expose the system filesystem read-only. Some session sockets may remain reachable, runtime behavior varies, and sandboxed apps may not work correctly. Do not rely on it as a security boundary for hostile software.
- Experimental APT sync verifies a detached `Release.gpg` signature using only the keyring supplied by the user, then checks package-index and package SHA256 values listed in the signed Release metadata. DebArk does not establish key trust, support `InRelease`-only sources, or automatically replace installed applications. The preview supports x86_64/amd64 only.
- Experimental CVE results use Debian Security Tracker data over HTTPS and may be incomplete or uncertain. Verify each result against Debian's tracker.
- Manifest hashes help detect later file changes; they do not certify that the original package was benign.

## Reporting a vulnerability

Please do not publish an unpatched vulnerability in a public issue. Use GitHub's private vulnerability reporting for this repository when it is available. Otherwise, contact the maintainer through the GitHub profile linked from the repository and include reproduction steps, affected versions, and the impact. The maintainer will acknowledge reports as soon as practical and coordinate a fix and disclosure with the reporter.

Security fixes are prioritized for the latest release. Older releases may not receive patches; upgrade to the latest release when possible.
