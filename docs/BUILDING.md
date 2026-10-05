# Building DebArk

## Python source install

The normal installer remains the supported way to install DebArk. It downloads
the current project source from GitHub and uses pacman, with the existing
dependency-bundle fallback, to provide the required Arch packages.

Running directly from a source checkout is a maintainer/development workflow.
Tests can exercise modules directly; normal command-line startup requires a
valid signature from the official release key.

The `Publish signed source release` workflow packages only the files used by
the online installer and updates the assets on the existing `v0.1` release.
It never creates a tag. Its signing job uses the repository's
`DEBARK_RELEASE_PRIVATE_KEY` Actions secret after a separate, secret-free
packaging job. Protect the `main` branch and consider requiring an approval on
a `release-signing` GitHub environment before allowing workflows to use that
secret.

## Optional Nuitka standalone build

The project also has an optional Nuitka build that keeps the current CLI and
creates a standalone **directory**. It does not use `--onefile` and does not
replace the Python source installer.

On Arch Linux, install the build tools and compile from the repository root:

```sh
sudo pacman -S --needed python nuitka base-devel patchelf openssl
./scripts/build-standalone.sh
```

The result is placed in `dist/nuitka/debark.dist/`; run its `debark` executable
from inside that directory. Build on Arch Linux for the Arch systems where the
result will run. The build uses the compiler and system libraries available on
the build host, so a standalone directory is not a universal static ELF and
may still rely on the target system's ABI and system tools such as pacman and
OpenSSL.

Nuitka is a packaging and compilation option, not a guarantee against
reverse-engineering or a substitute for signed release verification. Build
outputs are generated locally and are not part of the Python source install.
The manual standalone workflow signs its directory with the same release key
and publishes it as a workflow artifact; it does not replace the source-based
installer.
