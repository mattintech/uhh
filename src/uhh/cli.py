"""uhh — ask a local LLM (Ollama) for the command you forgot."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5-coder:14b-instruct-q4_K_M"
DEFAULT_TIMEOUT = 120
YES_ABORT_SECONDS = 1.0

DEFAULT_CONFIG = """\
# uhh config — point at any Ollama instance.
# Pick a profile with --profile NAME, UHH_PROFILE env var, or default_profile below.
# Per-call overrides: --host, --model, UHH_HOST, UHH_MODEL.

default_profile = "local"

[profiles.local]
host  = "http://localhost:11434"
model = "qwen2.5-coder:14b-instruct-q4_K_M"
# shell = "bash"            # optional: force a shell dialect (bash/zsh/fish/powershell/cmd)

# Example remote profile:
# [profiles.homelab]
# host  = "http://homelab.lan:11434"
# model = "qwen3:14b"
# api_key = ""              # optional bearer token if Ollama is behind a proxy
"""

SYSTEM_PROMPT = """You are a CLI assistant. The user's CURRENT machine is {os_name} with {shell},
but you may be asked about ANY operating system or shell. Your job is to ALWAYS produce a
working command for whatever platform the user asks about. NEVER refuse a question just
because the platform differs from the current machine — that is the whole point of this tool.

Be lenient with typos and near-synonyms. Pick the most likely intent rather than refusing.
Examples: "keygaurd"/"keyguard" → "keychain"; "foler" → "folder"; "compres" → "compress".

Shell-syntax pitfalls — get these right; small mistakes here silently match nothing:
  - grep alternation: use `grep -E 'foo|bar'` (extended) or `grep 'foo\\|bar'` (basic).
    Plain `grep 'foo|bar'` does NOT alternate — `|` is literal in basic regex.
  - In grep regex, `[...]` is a character class. To match a literal `[` or `]`, escape:
    `grep '\\[api\\]'` matches the string `[api]`; `grep '[api]'` matches the letters a, p, OR i.
  - Prefer `awk '$N == "value"'` for whole-column matches when fields are space-delimited;
    avoids regex-escaping problems entirely.
  - Quote any pattern containing shell metacharacters (`'`, `$`, backticks, `*`, `?`, `[`, `]`,
    `;`, `&`, `|`, `<`, `>`, `(`, `)`).

Output format: a SINGLE JSON object — no prose, no markdown, no code fences — with these
string fields:
  "command":     the exact command for the target platform; single line preferred; no quoting wrapper.
  "explanation": one short sentence (<= 100 chars) describing what it does.
  "target_os":   exactly one of "Linux", "macOS", "Windows", or "any" (for portable commands).

Use SYSTEM FACTS values (ssh keys, hostnames, username, cwd) verbatim — but ONLY when
target_os matches the user's current machine. For cross-OS answers, use generic placeholders.

Only return command="" for requests that are truly impossible at the command line.

Worked examples (these are illustrative; produce the same JSON shape for any input):

User: "how do I unlock the keychain on mac"
{{"command": "security unlock-keychain ~/Library/Keychains/login.keychain-db", "explanation": "Unlocks the macOS login keychain (prompts for password).", "target_os": "macOS"}}

User: "list services in powershell"
{{"command": "Get-Service", "explanation": "Lists all Windows services and their status.", "target_os": "Windows"}}

User: "list listening ports on linux"
{{"command": "ss -tuln", "explanation": "Lists listening TCP and UDP sockets on Linux.", "target_os": "Linux"}}

User: "show current git branch"
{{"command": "git branch --show-current", "explanation": "Prints the name of the current Git branch.", "target_os": "any"}}

User: "show only lines tagged [api] or [db] in app.log"
{{"command": "grep -E ' \\[(api|db)\\] ' app.log", "explanation": "Filters lines whose component column is [api] or [db].", "target_os": "any"}}"""


def gather_context() -> dict[str, str]:
    """Read-only snapshot of safe system metadata to ground the model's answer."""
    facts: dict[str, str] = {}
    try:
        facts["hostname"] = socket.gethostname()
    except Exception:
        pass
    try:
        facts["username"] = getpass.getuser()
    except Exception:
        pass
    try:
        facts["os"] = f"{platform.system()} {platform.release()}"
    except Exception:
        pass
    try:
        facts["cwd"] = str(Path.cwd())
    except Exception:
        pass

    ssh_dir = Path.home() / ".ssh"
    if ssh_dir.is_dir():
        try:
            keys = sorted(p.name for p in ssh_dir.glob("*.pub") if p.is_file())
            if keys:
                facts["ssh_public_keys"] = ", ".join(keys)
        except Exception:
            pass
        ssh_config = ssh_dir / "config"
        if ssh_config.is_file():
            try:
                hosts: list[str] = []
                for line in ssh_config.read_text(errors="replace").splitlines():
                    s = line.strip()
                    if s.lower().startswith("host ") and not s.startswith("#"):
                        for h in s.split()[1:]:
                            if "*" not in h and "?" not in h:
                                hosts.append(h)
                if hosts:
                    facts["ssh_host_aliases"] = ", ".join(sorted(set(hosts))[:20])
            except Exception:
                pass
    return facts


def format_facts(facts: dict[str, str]) -> str:
    if not facts:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in facts.items())
    return (
        "\n\nSYSTEM FACTS (read-only snapshot of the user's machine; "
        "use values verbatim when relevant, otherwise ignore):\n" + lines
    )


def config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "uhh" / "config.toml"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .wizard import run_wizard
            run_wizard(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_CONFIG)
            print(f"[uhh] wrote default config to {path}", file=sys.stderr)
    with path.open("rb") as f:
        return tomllib.load(f)


def resolve_profile(cfg: dict, name: str | None) -> dict:
    profiles = cfg.get("profiles", {})
    name = name or os.environ.get("UHH_PROFILE") or cfg.get("default_profile") or "local"
    if not profiles:
        return {}
    if name not in profiles:
        sys.exit(f"[uhh] profile '{name}' not found. Available: {', '.join(profiles)}")
    return profiles[name]


def detect_shell(override: str | None) -> tuple[str, str]:
    """Return (shell_name, os_name) for the model prompt."""
    os_name = {"linux": "Linux", "darwin": "macOS", "win32": "Windows"}.get(
        sys.platform, sys.platform
    )
    if override:
        return override, os_name
    if sys.platform == "win32":
        return ("PowerShell" if shutil.which("pwsh") or shutil.which("powershell") else "cmd.exe"), os_name
    return Path(os.environ.get("SHELL", "/bin/sh")).name, os_name


class _Thinking:
    """Animated 'thinking' indicator on stderr; static line on non-TTY."""

    FRAMES = ("thinking   ", "thinking.  ", "thinking.. ", "thinking...")
    INTERVAL = 0.4

    def __init__(self, stream=sys.stderr):
        self.stream = stream
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tty = bool(getattr(stream, "isatty", lambda: False)())

    def __enter__(self):
        if not self._tty:
            self.stream.write("[uhh] thinking...\n")
            self.stream.flush()
            return self
        self.stream.write("\x1b[?25l")  # hide cursor
        self.stream.flush()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if not self._tty:
            return False
        self._stop.set()
        if self._thread:
            self._thread.join()
        # Clear the line and restore cursor.
        self.stream.write("\r" + " " * (len(self.FRAMES[-1]) + 8) + "\r\x1b[?25h")
        self.stream.flush()
        return False

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            self.stream.write("\r[uhh] " + self.FRAMES[i % len(self.FRAMES)])
            self.stream.flush()
            i += 1
            self._stop.wait(self.INTERVAL)


def ask_ollama(host: str, model: str, system: str, user: str,
               api_key: str | None, timeout: int) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }).encode()
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"[uhh] Ollama returned {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"[uhh] cannot reach Ollama at {host}: {e.reason}")
    content = payload.get("message", {}).get("content", "")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        sys.exit(f"[uhh] model did not return valid JSON:\n{content}")


def run_command(command: str, shell_name: str) -> int:
    if sys.platform == "win32":
        if shell_name.lower().startswith("power") or shell_name.lower() == "pwsh":
            exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            return subprocess.call([exe, "-NoProfile", "-Command", command])
        return subprocess.call(command, shell=True)
    shell_path = os.environ.get("SHELL") or shutil.which(shell_name) or "/bin/sh"
    return subprocess.call([shell_path, "-c", command])


def prompt_yes_no(prompt: str) -> bool:
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes")


def main() -> int:
    from . import __version__

    p = argparse.ArgumentParser(
        prog="uhh",
        description="Ask a local LLM for the command you forgot, then optionally run it.",
        epilog=(
            "tip: quote questions with apostrophes — your shell eats unmatched ' "
            "characters. Use double quotes (\"don't sleep\") or rewrite (\"do not sleep\")."
        ),
    )
    p.add_argument("--version", action="version", version=f"uhh {__version__}")
    p.add_argument(
        "question",
        nargs="*",
        help='natural-language question (quote it if it contains apostrophes, e.g. "don\'t")',
    )
    p.add_argument("--profile", help="config profile to use")
    p.add_argument("--host", help="Ollama host URL (overrides profile)")
    p.add_argument("--model", help="model name (overrides profile)")
    p.add_argument("--shell", help="shell dialect to target (overrides auto-detect)")
    p.add_argument("--no-context", action="store_true", help="don't send system facts to the model")
    p.add_argument("--show-context", action="store_true", help="print the system facts being sent")
    p.add_argument("--no-run", action="store_true", help="print command, skip the y/N prompt")
    p.add_argument("-y", "--yes", action="store_true", help="run without confirmation")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="request timeout in seconds")
    p.add_argument("--config", action="store_true", help="print config path and exit")
    p.add_argument("--list-profiles", action="store_true", help="list configured profiles")
    args = p.parse_args()

    if args.config:
        print(config_path())
        return 0

    first_run = not config_path().exists()
    cfg = load_config()

    if args.list_profiles:
        default = cfg.get("default_profile")
        for name in cfg.get("profiles", {}):
            print(f"{name}{'  (default)' if name == default else ''}")
        return 0

    if not args.question:
        if first_run:
            return 0
        p.print_help()
        return 2

    profile = resolve_profile(cfg, args.profile)
    host = args.host or os.environ.get("UHH_HOST") or profile.get("host", DEFAULT_HOST)
    model = args.model or os.environ.get("UHH_MODEL") or profile.get("model", DEFAULT_MODEL)
    api_key = os.environ.get("UHH_API_KEY") or profile.get("api_key")
    shell_override = args.shell or profile.get("shell")

    shell_name, os_name = detect_shell(shell_override)
    system = SYSTEM_PROMPT.format(os_name=os_name, shell=shell_name)

    if not args.no_context:
        facts = gather_context()
        if args.show_context:
            print("[uhh] system facts:", file=sys.stderr)
            for k, v in facts.items():
                print(f"  {k}: {v}", file=sys.stderr)
        system += format_facts(facts)

    question = " ".join(args.question)

    with _Thinking():
        result = ask_ollama(host, model, system, question, api_key, args.timeout)
    command = (result.get("command") or "").strip()
    explanation = (result.get("explanation") or "").strip()
    target_os = (result.get("target_os") or "").strip()

    if not command:
        print(f"[uhh] {explanation or 'no command produced'}", file=sys.stderr)
        return 1

    print(f"$ {command}")
    if explanation:
        print(f"  # {explanation}")

    cross_os = bool(target_os) and target_os.lower() not in (os_name.lower(), "any")
    if cross_os:
        sys.stdout.flush()
        print(
            f"\n[uhh] target = {target_os}; you're on {os_name} — won't offer to run here. "
            f"Copy this to a {target_os} machine.",
            file=sys.stderr,
        )
        return 0

    if args.no_run:
        return 0
    if args.yes:
        if sys.stdin.isatty():
            try:
                print(
                    f"\n[uhh] running in {YES_ABORT_SECONDS:.0f}s — Ctrl-C to abort.",
                    file=sys.stderr,
                )
                time.sleep(YES_ABORT_SECONDS)
            except KeyboardInterrupt:
                print("\n[uhh] aborted.", file=sys.stderr)
                return 130
        return run_command(command, shell_name)
    if prompt_yes_no("\nRun it? [y/N] "):
        return run_command(command, shell_name)
    return 0
