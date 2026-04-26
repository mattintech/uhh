# uhh

[![PyPI](https://img.shields.io/pypi/v/uhh.svg?cacheSeconds=300&v=2)](https://pypi.org/project/uhh/)
[![Python versions](https://img.shields.io/pypi/pyversions/uhh.svg?cacheSeconds=300&v=2)](https://pypi.org/project/uhh/)
[![Status](https://img.shields.io/pypi/status/uhh.svg?cacheSeconds=300&v=2)](https://pypi.org/project/uhh/)
[![License](https://img.shields.io/pypi/l/uhh.svg?cacheSeconds=300&v=2)](LICENSE)
[![Publish](https://img.shields.io/github/actions/workflow/status/mattintech/uhh/publish.yml?label=publish&cacheSeconds=300&v=2)](https://github.com/mattintech/uhh/actions/workflows/publish.yml)

> i forgor 💀 — ask your local LLM for the command you forgot, then run it.

`uhh` is a tiny CLI that asks a local [Ollama](https://ollama.com) instance for the command-line answer to a natural-language question, prints it, and optionally runs it. Cross-platform, zero runtime dependencies.

> **Heads up — Windows is unverified.** `uhh` is written to work on Windows (PowerShell / cmd detection, `%APPDATA%` config path, etc.) but I no longer have a Windows machine to test on. If you run `uhh` on Windows and something is broken, please [open an issue](https://github.com/mattintech/uhh/issues/new?template=bug_report.yml) — and if you'd like to help validate releases on Windows, please reach out via an issue or discussion. Linux and macOS are tested.

## Demo

```sh
$ uhh how do i scp test.txt to mhills@172.16.0.1
$ scp test.txt mhills@172.16.0.1:~/
  # Copies test.txt to the remote host's home directory.

Run it? [y/N] y
test.txt    100%   12B  ...
```

It reads safe local context — your username, hostname, current directory, names of `~/.ssh/*.pub` keys, ssh config Host aliases — so the suggested command uses your real values instead of placeholders.

If you ask about a different OS than your current one (e.g. you're on Linux but ask "how do I unlock the keychain on mac"), `uhh` produces the right command but skips the run prompt for safety.

## Quoting your question

`uhh` reads your question from `argv`, so your shell parses it before `uhh` ever sees it. Two characters will trip you up if left bare:

- **Apostrophes** (`'`) — `uhh how do I keep my mac from sleeping when I don't want it to` will leave your shell sitting on a continuation prompt waiting for the closing `'`. Wrap the question in double quotes, escape the apostrophe, or just rewrite without the contraction.
- **Shell metacharacters** (`|`, `>`, `<`, `&`, `;`, `$`, backticks) — these get interpreted by the shell. Quote the question if you're including any of them.

```sh
# bad — shell hangs on the apostrophe:
uhh how do I stop my mac sleeping when I don't want it to

# good — any of these works:
uhh "how do I stop my mac sleeping when I don't want it to"
uhh how do I stop my mac sleeping when I don\'t want it to
uhh how do I stop my mac sleeping when I do not want it to
```

## Install

```sh
pip install uhh
```

Or with [pipx](https://pipx.pypa.io/) for an isolated install:

```sh
pipx install uhh
```

You also need [Ollama](https://ollama.com/download) running somewhere reachable, plus at least one chat model. The first-run wizard walks you through this.

## First run

Any first invocation triggers a one-time setup wizard:

```text
$ uhh how do i list listening ports

It looks like this is the first time uhh has run —
you need to configure a few things.

Ollama host [http://localhost:11434]:
  ✓ reachable, 8 model(s) installed

Recommended for command lookup:
  [1] qwen2.5-coder:14b-instruct-q4_K_M  ✓ installed   best for shell commands
  [2] qwen3:14b                          ✓ installed   newer general-purpose
  [3] llama3.1:8b                        ✓ installed   fast, solid all-rounder
  [4] qwen2.5-coder:7b                   pull (4.4 GB) smaller coder model
  [5] llama3.2:3b                        pull (2.0 GB) tiny + very fast

Pick a default model [1]:
```

After picking a model (and pulling it if needed), your original question is answered. To re-run setup later, delete the config file and run `uhh ...` again.

## Configuration

Config lives at:
- Linux / macOS: `~/.config/uhh/config.toml`
- Windows: `%APPDATA%\uhh\config.toml`

Configure multiple Ollama instances ("profiles") and switch between them:

```toml
default_profile = "local"

[profiles.local]
host  = "http://localhost:11434"
model = "qwen2.5-coder:14b-instruct-q4_K_M"

[profiles.homelab]
host  = "http://homelab.lan:11434"
model = "qwen3:14b"
# api_key = "..."   # optional bearer token if Ollama is behind a proxy
```

Then:

```sh
uhh --profile homelab "rotate this nginx cert"
```

Override hierarchy (highest wins): `--host` / `--model` / `--profile` flags → `UHH_HOST` / `UHH_MODEL` / `UHH_PROFILE` env vars → config file.

## Useful flags

| Flag | Purpose |
|---|---|
| `--no-run` | Print the command but skip the y/N prompt |
| `-y`, `--yes` | Run without asking (only for same-OS commands) |
| `--show-context` | Print the system facts being sent to the model |
| `--no-context` | Don't send any system facts |
| `--shell SHELL` | Override shell-dialect detection (`bash`/`zsh`/`fish`/`powershell`) |
| `--list-profiles` | List configured profiles |
| `--config` | Print the config file path |

## What gets sent to the model

A small, read-only snapshot of your machine is included in the prompt by default so suggestions use your real values:

- Hostname, username, OS, current working directory
- Filenames of public keys in `~/.ssh/` (e.g. `id_ed25519.pub`) — never key contents
- Host alias names from `~/.ssh/config` — never destination hostnames

Nothing is sent to a third party — only to the Ollama instance you configured. Disable entirely with `--no-context`.

## Development

Install the latest unreleased code from the `develop` branch:

```sh
pip install git+https://github.com/mattintech/uhh.git@develop
```

Or with pipx:

```sh
pipx install git+https://github.com/mattintech/uhh.git@develop
```

Re-install from a different branch (e.g. a feature branch) — `pipx upgrade` won't switch refs, so use `--force`:

```sh
pipx install --force git+https://github.com/mattintech/uhh.git@feature/pypiready
```

For pip, add `--force-reinstall`:

```sh
pip install --force-reinstall git+https://github.com/mattintech/uhh.git@feature/pypiready
```

Check the installed version anytime with `uhh --version`.

Branches:
- `main` — tracks released versions; tag a release here to publish to PyPI
- `develop` — integration branch for in-progress work; install from here to try unreleased changes

Releasing: bump nothing — versions come from git tags via `hatch-vcs`. Merge `develop` → `main`, then GitHub UI → Releases → Draft → tag `vX.Y.Z` → Publish. The `publish.yml` workflow ships it to PyPI.

## Requirements

- Python 3.11+ (uses stdlib `tomllib`)
- [Ollama](https://ollama.com) reachable locally or remotely
- A chat-tuned model (the wizard helps install one)

## Reporting bugs

Run `uhh --bug` and your browser will open a pre-filled GitHub issue form with your `uhh` version, OS, Python version, shell, model, and a localhost-vs-remote indicator for your Ollama host. Add what you ran, what you expected, and what you got — then submit. (No GitHub auth happens from `uhh`; it just opens the URL.)

You can also pass the prompt that broke to pre-fill the "what you ran" field:

```sh
uhh --bug "show me lines tagged [api] in app.log"
```

## License

[MIT](LICENSE)
