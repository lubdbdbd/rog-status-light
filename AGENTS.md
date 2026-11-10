# Development and releases

- Keep the owner's existing keyboard app and Bluetooth / USB / RF behavior working. Do not use computer-use. Hardware tests must be started by the owner from the existing app after being announced.
- Run `python -m unittest discover -s tests -p 'test_*.py' -q` before publishing. Hardware availability or a successful API write alone is not evidence of visible lighting.
- Source is in `src/`; packaging inputs are in `packaging/`. Never add chat logs, runtime databases, local account paths, private certificates or authentication material.
- A push changing software on `main` triggers automatic Windows build and Release. The workflow assigns the next patch version when required. Do not manually create an extra release for the same commit.
- For edits made in the original local workspace, finish the install ZIP after testing. The local publisher synchronizes only the allowlisted software files once that ZIP and the source agree.
- Keep `scripts/sync_local.py` scoped to its fixed file mapping. Do not expand it to whole workspaces or user directories.
