#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Feed Orca-managed remote Claude sessions into the light state.

Claude Code hooks only fire on the machine the claude process runs on. A
session started on an Orca SSH host, or on a paired remote Orca runtime, never
reaches this daemon: the hook binary is not installed over there and the unix
socket is not reachable from it. Orca does track those sessions -- it installs
its own hooks on every host it manages -- so we read them back out through the
`orca` CLI and feed them in as pseudo sessions keyed "orca:<scope>:<handle>".

Fidelity is lower than the hook path by construction:

  running     the terminal title carries an agent spinner glyph
  permission  best effort, from the terminal preview tail. Claude Code puts no
              permission marker in its title -- Orca's own "permission" title
              glyph is Gemini CLI's -- so scraping the preview is the only
              signal available.
  completed   emitted on a working -> idle transition and held, mirroring what
              the Stop hook does locally
  failure     not detectable from anything Orca exposes, so never emitted

The status vocabulary is Orca's, not ours: it is taken from
detectAgentStatusFromTitle() inside Orca.app, where a "✳ " prefix means idle
and a braille (U+2800-U+28FF) or quarter-circle (U+25D0-U+25D3) spinner means
working. If Orca changes it, this reads the wrong states rather than crashing.

Config lives in its own file, APP_DIR/orca.json, deliberately: settings.json is
round-tripped by the SwiftUI settings app through a Codable struct that encodes
only the keys it knows, so anything added there would be silently dropped the
next time the user saves from the app.
"""

import json
import subprocess
import threading
import time
from pathlib import Path

APP_DIR = Path.home() / "Library" / "Application Support" / "ClaudeHalo65"
CONFIG_PATH = APP_DIR / "orca.json"

SOURCE = "orca"

DEFAULT_CONFIG = {
    # Off by default. Polling starts subprocesses every couple of seconds and
    # reads the titles and previews of terminals across every paired runtime,
    # which is a bigger thing to switch on behind someone's back than a light
    # is. `install.py reconnect` turns it on.
    "enabled": False,
    "cli": "/usr/local/bin/orca",
    "poll_seconds": 2.5,
    "timeout_seconds": 6.0,
    # Sessions on this Mac already arrive through the hooks at full fidelity.
    # Picking them up here as well would only overwrite good states with coarse
    # ones -- and would lose "failure" entirely.
    "include_local_host": False,
    # "auto" discovers every paired remote runtime; a list pins specific ids or
    # names; [] polls the local runtime only (which still covers SSH hosts).
    "environments": "auto",
    "environment_refresh_seconds": 60,
    "detect_permission": True,
    # Lowercased substrings, any hit counts. Claude Code's prompt wording moves
    # between releases, so this is config rather than a regex buried in code.
    "permission_markers": [
        "do you want to proceed?",
        "do you want to make this edit",
        "do you want to create",
        "don't ask again",
        "tell claude what to do differently",
    ],
    "preview_tail_chars": 600,
}

# Orca.app: containsBrailleSpinner / containsQuarterCircleSpinner.
_SPINNER_RANGES = ((0x2800, 0x28FF), (0x25D0, 0x25D3))


def _log(message):
    """Replaced by the daemon with its own logger at construction time."""


def contains_spinner(title):
    return any(low <= ord(char) <= high
               for char in title for low, high in _SPINNER_RANGES)


def claude_status_from_title(title):
    """"working" / "idle" / None, using Orca's own title vocabulary.

    This is deliberately the same rule Orca applies in isClaudeAgent(): a "✳"
    prefix is Claude Code's idle title, and any agent spinner glyph counts as
    working. A different agent CLI (codex, say) painting a braille spinner will
    therefore also register as working -- Orca's own detection has the same
    blind spot, and "some agent is busy on a remote host" is still the signal
    the ring is there to carry.
    """
    if not title:
        return None
    if title == "✳" or title.startswith("✳ "):
        return "idle"
    if contains_spinner(title):
        return "working"
    return None


def looks_like_permission_prompt(preview, markers, tail_chars):
    if not preview:
        return False
    tail = preview[-tail_chars:].lower() if tail_chars > 0 else preview.lower()
    return any(marker in tail for marker in markers)


def load_config():
    """Read orca.json, falling back to defaults for anything missing or bad.

    Unlike settings.json this file is optional: a missing file means the bridge
    is off, which is also what a fresh install gets.
    """
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return config
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"orca: config unreadable, using defaults: {exc}")
        return config
    if not isinstance(raw, dict):
        return config

    for key in ("enabled", "include_local_host", "detect_permission"):
        if isinstance(raw.get(key), bool):
            config[key] = raw[key]
    if isinstance(raw.get("cli"), str) and raw["cli"]:
        config["cli"] = raw["cli"]
    for key in ("poll_seconds", "timeout_seconds", "environment_refresh_seconds"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and value > 0:
            config[key] = float(value)
    if isinstance(raw.get("preview_tail_chars"), int) and raw["preview_tail_chars"] >= 0:
        config["preview_tail_chars"] = raw["preview_tail_chars"]
    environments = raw.get("environments")
    if environments == "auto" or isinstance(environments, list):
        config["environments"] = environments
    markers = raw.get("permission_markers")
    if isinstance(markers, list) and all(isinstance(m, str) for m in markers):
        config["permission_markers"] = [m.lower() for m in markers]
    return config


class OrcaBridge(threading.Thread):
    """Polls the `orca` CLI and mirrors remote Claude sessions into the daemon."""

    def __init__(self, aggregator, settings_loader, logger=None):
        super().__init__(daemon=True, name="orca-bridge")
        global _log
        if logger is not None:
            _log = logger
        self.aggregator = aggregator
        self.settings_loader = settings_loader
        self.stop_event = threading.Event()

        self._prev_status = {}      # session key -> last status seen
        self._completed_until = {}  # session key -> when the green flash ends
        self._environments = []
        self._environments_at = 0.0
        self._backoff_until = 0.0
        self._last_error = None
        self.health = {"last_poll": None, "terminals": 0, "error": None}

    # -- CLI ---------------------------------------------------------------

    def _cli(self, config, args):
        """Run one orca subcommand, returning its `result` object or None.

        Every failure mode here is expected in normal use -- Orca quit, the
        remote runtime asleep, the CLI not installed -- so none of them are
        worth more than a single log line and a backoff.
        """
        try:
            proc = subprocess.run(
                [config["cli"], *args, "--json"],
                capture_output=True, text=True,
                timeout=config["timeout_seconds"])
        except FileNotFoundError:
            self._fail(f"orca CLI not found at {config['cli']}")
            return None
        except subprocess.TimeoutExpired:
            self._fail(f"orca {' '.join(args)} timed out")
            return None
        except OSError as exc:
            self._fail(f"orca {' '.join(args)} failed: {exc}")
            return None
        if proc.returncode != 0:
            self._fail(f"orca {' '.join(args)} exited {proc.returncode}: "
                       f"{(proc.stderr or '').strip()[:200]}")
            return None
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            self._fail(f"orca {' '.join(args)} gave non-JSON: {exc}")
            return None
        if not payload.get("ok"):
            self._fail(f"orca {' '.join(args)} not ok: "
                       f"{payload.get('error', {}).get('message', '?')}")
            return None
        return payload.get("result") or {}

    def _fail(self, message):
        """Log a polling failure once, then back off until it changes."""
        self.health["error"] = message
        if message != self._last_error:
            _log(f"orca: {message}")
            self._last_error = message

    def _environment_targets(self, config):
        wanted = config["environments"]
        if isinstance(wanted, list):
            return list(wanted)
        now = time.time()
        if now - self._environments_at < config["environment_refresh_seconds"]:
            return self._environments
        result = self._cli(config, ["environment", "list"])
        self._environments_at = now
        if result is None:
            return self._environments
        self._environments = [env["id"] for env in result.get("environments", [])
                              if isinstance(env, dict) and env.get("id")]
        return self._environments

    # -- polling -----------------------------------------------------------

    def _collect(self, config):
        """One sweep: every scope's terminals mapped to daemon state names.

        Returns None if nothing could be read at all, which is different from
        an empty dict -- an empty dict legitimately means "no remote sessions"
        and must clear the ring, while None must leave the last view standing.
        """
        scopes = [("local", [])]
        for env_id in self._environment_targets(config):
            scopes.append((env_id, ["--environment", env_id]))

        states = {}
        live = set()
        reached_any = False
        for scope, extra in scopes:
            result = self._cli(config, ["terminal", "list", *extra])
            if result is None:
                continue
            reached_any = True
            for terminal in result.get("terminals", []):
                key, state = self._classify(config, scope, terminal)
                if key is None:
                    continue
                live.add(key)
                if state is not None:
                    states[key] = state
        if not reached_any:
            return None
        self._last_error = None
        self.health["error"] = None
        return states, live

    def _classify(self, config, scope, terminal):
        """One terminal into (session key, state).

        A key with a state of None means "still there, but contributing no
        light" -- an idle terminal past its green hold. That is distinct from
        (None, None), which means the terminal is not ours to track at all, and
        the difference is what keeps per-terminal bookkeeping from being pruned
        while the terminal is merely quiet.
        """
        if not isinstance(terminal, dict) or not terminal.get("connected", True):
            return None, None
        # "local" is relative to whichever runtime answered, so this only skips
        # sessions on this Mac -- a remote runtime's own "local" host is the
        # remote machine, which is exactly what we are here for.
        if (scope == "local" and terminal.get("executionHostId") == "local"
                and not config["include_local_host"]):
            return None, None
        handle = terminal.get("handle")
        if not handle:
            return None, None

        status = claude_status_from_title(terminal.get("title") or "")
        if status is None:
            return None, None
        key = f"{SOURCE}:{scope}:{handle}"

        if (status != "working" and config["detect_permission"]
                and looks_like_permission_prompt(terminal.get("preview") or "",
                                                 config["permission_markers"],
                                                 config["preview_tail_chars"])):
            status = "permission"

        if status in ("working", "permission"):
            self._completed_until.pop(key, None)
            self._prev_status[key] = status
            return key, "running" if status == "working" else "permission"

        # Idle. Green only for the turn that just ended, never for a terminal
        # that has merely been sitting at the prompt -- otherwise every open
        # remote tab would hold the ring green forever.
        previous = self._prev_status.get(key)
        self._prev_status[key] = status
        if previous in ("working", "permission"):
            hold = self.settings_loader()["completed_hold_seconds"]
            self._completed_until[key] = time.time() + hold
        deadline = self._completed_until.get(key)
        if deadline is not None:
            if time.time() < deadline:
                return key, "completed"
            self._completed_until.pop(key, None)
        return key, None

    def _forget_absent(self, live_keys):
        """Drop bookkeeping for terminals Orca no longer reports."""
        for book in (self._prev_status, self._completed_until):
            for key in list(book):
                if key not in live_keys:
                    book.pop(key, None)

    def run(self):
        while not self.stop_event.is_set():
            config = load_config()
            if not config["enabled"]:
                self.stop_event.wait(5.0)
                continue
            now = time.time()
            if now < self._backoff_until:
                self.stop_event.wait(min(1.0, self._backoff_until - now))
                continue

            try:
                sweep = self._collect(config)
            except Exception as exc:               # never take the daemon down
                self._fail(f"poll crashed: {exc!r}")
                sweep = None

            if sweep is None:
                # Orca is not answering; wait longer rather than spawning a
                # doomed subprocess every couple of seconds.
                self._backoff_until = time.time() + config["poll_seconds"] * 8
            else:
                states, live = sweep
                self._forget_absent(live)
                self.health["last_poll"] = time.time()
                self.health["terminals"] = len(states)
                self.aggregator.sync_source(SOURCE, states)

            self.stop_event.wait(config["poll_seconds"])

    def stop(self):
        self.stop_event.set()
