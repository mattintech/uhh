"""First-run setup wizard for uhh."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_HOST = "http://localhost:11434"

# Curated picks for command-lookup use case. Order = preference.
RECOMMENDED_MODELS = [
    # (model_name, approx_size, blurb)
    ("qwen2.5-coder:14b-instruct-q4_K_M", "9.0 GB", "best for shell commands"),
    ("qwen3:14b",                          "9.3 GB", "newer general-purpose"),
    ("llama3.1:8b",                        "4.9 GB", "fast, solid all-rounder"),
    ("qwen2.5-coder:7b",                   "4.4 GB", "smaller coder model"),
    ("llama3.2:3b",                        "2.0 GB", "tiny + very fast"),
]


def _prompt(question: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{question}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit("[uhh] setup cancelled.")
    return ans or (default or "")


def _probe_ollama(host: str, timeout: float = 5.0) -> list[dict] | None:
    """Return list of installed models, or None if Ollama unreachable."""
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data.get("models", [])
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _pull_model(host: str, model: str) -> bool:
    """Stream /api/pull and render progress. Returns True on success."""
    body = json.dumps({"name": model, "stream": True}).encode()
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/pull",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    print(f"\nPulling {model} ... (this can take a while for large models)")
    last_status: str | None = None
    try:
        with urllib.request.urlopen(req, timeout=None) as resp:
            for raw in resp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in evt:
                    print(f"\n  ✗ {evt['error']}")
                    return False
                status = evt.get("status", "")
                if status != last_status:
                    if last_status is not None:
                        sys.stdout.write("\n")
                    last_status = status
                completed = evt.get("completed")
                total = evt.get("total")
                if completed and total:
                    pct = 100 * completed / total
                    sys.stdout.write(
                        f"\r  {status}: {pct:5.1f}%  "
                        f"({_fmt_bytes(completed)} / {_fmt_bytes(total)})  "
                    )
                else:
                    sys.stdout.write(f"\r  {status}{' ' * 40}")
                sys.stdout.flush()
                if status == "success":
                    sys.stdout.write("\n")
                    return True
        sys.stdout.write("\n")
        return True
    except urllib.error.HTTPError as e:
        print(f"\n  ✗ HTTP {e.code}: {e.read().decode(errors='replace')}")
        return False
    except urllib.error.URLError as e:
        print(f"\n  ✗ network error: {e.reason}")
        return False
    except KeyboardInterrupt:
        print("\n  ✗ pull cancelled.")
        return False


def _ask_host() -> tuple[str, list[dict]]:
    """Loop until we get a host that responds. Returns (host, installed_models)."""
    host = _prompt("Ollama host", DEFAULT_HOST)
    while True:
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        print(f"  checking {host} ...")
        models = _probe_ollama(host)
        if models is not None:
            print(f"  ✓ reachable, {len(models)} model(s) installed")
            return host, models
        print(f"  ✗ cannot reach Ollama at {host}")
        print("    Is `ollama serve` running on that host? Is the port reachable?")
        choice = _prompt("  [r]etry  [c]hange host  [q]uit", "r").lower()
        if choice == "q":
            sys.exit("[uhh] setup cancelled.")
        if choice == "c":
            host = _prompt("Ollama host", DEFAULT_HOST)


def _choose_model(host: str, installed_models: list[dict]) -> str:
    installed = {m.get("name", "") for m in installed_models}
    options: list[tuple[str, str, str]] = []  # (label, model, action)

    print("\nRecommended for command lookup:")
    n = 1
    for model, size, blurb in RECOMMENDED_MODELS:
        is_installed = model in installed
        mark = "✓ installed" if is_installed else f"pull ({size})"
        print(f"  [{n}] {model:<40} {mark:<14}  {blurb}")
        options.append((str(n), model, "use" if is_installed else "pull"))
        n += 1

    others = sorted(installed - {m for m, _, _ in RECOMMENDED_MODELS})
    if others:
        print("\nAlso installed locally:")
        for model in others:
            print(f"  [{n}] {model}")
            options.append((str(n), model, "use"))
            n += 1

    valid = {o[0] for o in options}
    while True:
        choice = _prompt("\nPick a default model", "1")
        if choice in valid:
            break
        print(f"  ! '{choice}' isn't a valid choice — pick from {sorted(valid, key=int)}")

    _, model, action = next(o for o in options if o[0] == choice)

    if action == "pull":
        if not _pull_model(host, model):
            print("[uhh] pull failed; falling back to model selection.")
            return _choose_model(host, _probe_ollama(host) or installed_models)

    return model


def _write_config(path: Path, host: str, model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '# uhh config — point at any Ollama instance.\n'
        '# Pick a profile with --profile NAME, UHH_PROFILE env var, or default_profile below.\n'
        '# Per-call overrides: --host, --model, UHH_HOST, UHH_MODEL.\n'
        '\n'
        'default_profile = "local"\n'
        '\n'
        '[profiles.local]\n'
        f'host  = "{host}"\n'
        f'model = "{model}"\n'
        '# shell = "bash"            # optional: force a shell dialect\n'
        '\n'
        '# Example remote profile:\n'
        '# [profiles.homelab]\n'
        '# host  = "http://homelab.lan:11434"\n'
        '# model = "qwen3:14b"\n'
        '# api_key = ""              # optional bearer token if Ollama is behind a proxy\n'
    )


def run_wizard(config_target: Path) -> None:
    """Interactive first-run setup. Writes config to config_target on success."""
    print("\nIt looks like this is the first time uhh has run —")
    print("you need to configure a few things.\n")

    host, models = _ask_host()
    model = _choose_model(host, models)
    _write_config(config_target, host, model)

    print(f"\n  ✓ wrote config to {config_target}")
    print(f"  ✓ default profile 'local' → {model} @ {host}")
    print("\nSetup complete. Re-run with `rm {} && uhh ...` to redo setup.\n".format(config_target))
