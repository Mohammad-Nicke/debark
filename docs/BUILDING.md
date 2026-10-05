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

On Arch Linux, install the system build tools, create an isolated Python
environment for Nuitka, and compile from the repository root:

```sh
sudo pacman -S --needed python python-pip base-devel patchelf openssl
mkdir -p .build
python3 -m venv .build/nuitka-env
.build/nuitka-env/bin/python -m pip install Nuitka
PATH="$PWD/.build/nuitka-env/bin:$PATH" bash scripts/build-standalone.sh
```

The local output is `dist/nuitka/standalone_entry.dist/`, with the executable
named `debark-cli`. Build on Arch Linux for the Arch systems where the result
will run. It uses the compiler and system libraries available on the build
host, so it is not a universal static ELF and may still rely on the target
system's ABI and tools such as pacman and OpenSSL.

A local build has no official release signature, so DebArk will refuse to run
it. To get a signed, runnable build, run the manual
[Build signed Nuitka standalone directory](../.github/workflows/standalone.yml)
workflow on `main`. It verifies the release key and manifest, runs the binary
inside an Arch Linux container, and uploads the signed directory as the
`debark-standalone-x86_64` workflow artifact. This does not create a tag or
replace the source-based installer.

Nuitka is a packaging and compilation option, not a guarantee against
reverse-engineering or a substitute for signed release verification. The
standalone artifact is separate from the Python source install.
