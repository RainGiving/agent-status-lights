#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Status-light daemon: maps Claude Code hook events onto the Halo65 V2 lights.

Two zones are driven independently and can run together:

  halo    the 50-LED ring around the base, via the vendor VIA channel added by
          firmware/halo-host-control.patch
  matrix  the typing-area RGB Matrix, via stock VIA

RGB Matrix effect 0 (NONE) blanks the whole LED driver and takes the ring down
with it, so effect 0 is rejected everywhere below.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path.home() / "Library" / "Application Support" / "ClaudeHalo65"
SOCKET_PATH = APP_DIR / "status.sock"
STATE_PATH = APP_DIR / "state.json"
SETTINGS_PATH = APP_DIR / "settings.json"
LEDCTL = APP_DIR / "halo65_ledctl"
LOG_PATH = APP_DIR / "daemon.log"
LOG_MAX_BYTES = 1024 * 1024

VERSION = "0.3.0"
SETTINGS_VERSION = 3

# Highest priority first. "idle" is the absence of any tracked session and is a
# configurable look of its own, not just "hand everything back".
PRIORITY = ("failure", "permission", "running", "completed")
ALL_STATES = PRIORITY + ("idle",)

HALO_MODES = ("release", "solid", "pulse", "comet", "strobe", "fill")
MATRIX_EFFECT_MAX = 42

# PostToolUse is what clears a lingering "permission" state: PreToolUse fires
# *before* the permission prompt, so only the post-event proves it was resolved.
EVENT_TO_STATE = {
    "UserPromptSubmit": "running",
    "PostToolUse": "running",
    "PostToolUseFailure": "failure",
    "PermissionRequest": "permission",
    "Stop": "completed",
    "StopFailure": "failure",
}


def halo(color, mode, speed, param, brightness=100):
    return {"color": color, "brightness": brightness,
            "mode": mode, "speed": speed, "param": param}


def matrix(color, effect, speed, brightness=60, follow_color=True, restore=False):
    return {"color": color, "brightness": brightness, "effect": effect,
            "speed": speed, "follow_color": follow_color, "restore": restore}


DEFAULT_SETTINGS = {
    "version": SETTINGS_VERSION,
    # Which zones the daemon drives at all. Turning one off leaves it entirely
    # alone -- it is never read, written or restored.
    #
    # The ring is off by default because it is the only part of this project
    # that a stock keyboard cannot do: it needs the firmware from
    # firmware/halo-host-control.patch, and a default that silently depends on
    # a reflash would just look broken on a keyboard that never got one. The
    # typing area works on factory firmware, so that is what ships on.
    # `install.py reconnect` turns the ring on once the firmware is there.
    "zones": {"halo": False, "matrix": True},
    "completed_hold_seconds": 10,
    "failure_hold_seconds": 4,
    # A session killed mid-turn never sends SessionEnd, so it would otherwise
    # sit in "running" and spin the comet until the 12-hour sweep. Active states
    # get a much shorter leash; Claude Code emits PostToolUse constantly while
    # actually working, so a genuinely busy session refreshes long before this.
    "stale_active_minutes": 30,
    "stale_session_hours": 12,
    # Motion carries the state; colour only reinforces it. A comet orbiting the
    # ring reads as "working" the way a spinner does, while a whole-ring pulse
    # reads as "you are being asked something" -- a distinction that survives
    # being seen out of the corner of an eye, which colour alone does not.
    #
    # The matrix follows the ring's colour by default so the two zones read as
    # one signal, and sits dimmer so the keys stay comfortable to look at.
    "states": {
        "running":    {"halo": halo("#00A8FF", "comet", 200, 12),
                       "matrix": matrix("#00A8FF", 5, 110)},
        "permission": {"halo": halo("#FFB000", "pulse", 215, 0),
                       "matrix": matrix("#FFB000", 5, 230, brightness=75)},
        "failure":    {"halo": halo("#FF2020", "strobe", 230, 50),
                       "matrix": matrix("#FF2020", 1, 128, brightness=75)},
        "completed":  {"halo": halo("#00E060", "fill", 215, 0),
                       "matrix": matrix("#00E060", 1, 128)},
        # Idle hands both zones back by default, so the lighting the user set
        # with Fn+M and Fn+arrows survives untouched.
        "idle":       {"halo": halo("#000000", "release", 0, 0),
                       "matrix": matrix("#000000", 1, 128, restore=True)},
    },
}


def log(message):
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def parse_hex(color):
    text = color.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def hex_to_rgb(color, brightness_pct):
    """#RRGGBB into a raw RGB triple plus a 0-255 brightness byte.

    The ring takes RGB directly, so unlike the matrix there is no HSV round
    trip to lose colour accuracy in.
    """
    r, g, b = parse_hex(color)
    return r, g, b, int(round(max(0, min(100, brightness_pct)) * 255 / 100))


def hex_to_hsv(color, brightness_pct):
    """#RRGGBB into QMK's 0-255 hue/sat/val triple."""
    r, g, b = (channel / 255.0 for channel in parse_hex(color))
    high, low = max(r, g, b), min(r, g, b)
    delta = high - low

    if delta == 0:
        hue = 0.0
    elif high == r:
        hue = ((g - b) / delta) % 6
    elif high == g:
        hue = (b - r) / delta + 2
    else:
        hue = (r - g) / delta + 4
    hue *= 60.0

    sat = 0.0 if high == 0 else delta / high
    val = high * (max(0, min(100, brightness_pct)) / 100.0)
    return (int(round(hue / 360.0 * 255)) & 0xFF,
            int(round(sat * 255)) & 0xFF,
            int(round(val * 255)) & 0xFF)


def is_hex(value):
    if not isinstance(value, str) or len(value.lstrip("#")) != 6:
        return False
    try:
        int(value.lstrip("#"), 16)
        return True
    except ValueError:
        return False


_SETTINGS_CACHE = {"mtime": None, "value": None}


def load_settings():
    """Read settings.json, falling back to defaults for anything invalid.

    Cached on mtime: this runs on every hook event, and PostToolUse alone fires
    a few hundred times per session.
    """
    try:
        mtime = SETTINGS_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if mtime is not None and _SETTINGS_CACHE["mtime"] == mtime:
        return _SETTINGS_CACHE["value"]

    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"settings unreadable, using defaults: {exc}")
        raw = {}

    for key in ("completed_hold_seconds", "failure_hold_seconds",
                "stale_active_minutes", "stale_session_hours"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and value > 0:
            settings[key] = value

    zones = raw.get("zones")
    if isinstance(zones, dict):
        for zone in ("halo", "matrix"):
            if isinstance(zones.get(zone), bool):
                settings["zones"][zone] = zones[zone]

    for name, entry in (raw.get("states") or {}).items():
        if name not in settings["states"] or not isinstance(entry, dict):
            continue
        _merge_halo(name, entry.get("halo"), settings["states"][name]["halo"])
        _merge_matrix(name, entry.get("matrix"), settings["states"][name]["matrix"])

    _SETTINGS_CACHE["mtime"] = mtime
    _SETTINGS_CACHE["value"] = settings
    return settings


def _merge_halo(state, raw, target):
    if not isinstance(raw, dict):
        return
    if is_hex(raw.get("color")):
        target["color"] = raw["color"]
    if isinstance(raw.get("brightness"), int) and 0 <= raw["brightness"] <= 100:
        target["brightness"] = raw["brightness"]
    mode = raw.get("mode")
    # "release" is only meaningful for idle; on an active state it would blank
    # the ring exactly when it is supposed to be saying something.
    allowed = HALO_MODES if state == "idle" else HALO_MODES[1:]
    if mode in allowed:
        target["mode"] = mode
    elif mode is not None:
        log(f"{state}.halo: mode {mode!r} not one of {allowed}, keeping default")
    for key, limit in (("speed", 255), ("param", 255)):
        if isinstance(raw.get(key), int) and 0 <= raw[key] <= limit:
            target[key] = raw[key]


def _merge_matrix(state, raw, target):
    if not isinstance(raw, dict):
        return
    if is_hex(raw.get("color")):
        target["color"] = raw["color"]
    if isinstance(raw.get("brightness"), int) and 0 <= raw["brightness"] <= 100:
        target["brightness"] = raw["brightness"]
    effect = raw.get("effect")
    # Effect 0 blanks the LED driver and takes the ring down with it. 42 is the
    # top of the enum in the firmware built from firmware/halo-host-control.patch
    # (RGB_MATRIX_EFFECT_MAX == 43), confirmed on hardware by writing 255 and
    # reading back the clamped value. NuPhy's own factory build stopped at 45 --
    # a different effect set, so this number is per-firmware, not per-model.
    if isinstance(effect, int) and 1 <= effect <= MATRIX_EFFECT_MAX:
        target["effect"] = effect
    elif effect is not None:
        log(f"{state}.matrix: effect {effect!r} out of range 1-{MATRIX_EFFECT_MAX}, keeping default")
    if isinstance(raw.get("speed"), int) and 0 <= raw["speed"] <= 255:
        target["speed"] = raw["speed"]
    for flag in ("follow_color", "restore"):
        if isinstance(raw.get(flag), bool):
            target[flag] = raw[flag]


class Keyboard:
    """Serialises all HID access and remembers the pre-takeover matrix setting."""

    def __init__(self):
        self.lock = threading.Lock()
        self.saved = self._load_saved()
        self.last_halo = None
        self.last_matrix = None

    def _load_saved(self):
        try:
            with open(STATE_PATH, encoding="utf-8") as handle:
                saved = json.load(handle).get("saved")
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(saved, dict) and {"effect", "hue", "sat", "val", "speed"} <= saved.keys():
            return saved
        return None

    def _persist_saved(self):
        try:
            tmp = STATE_PATH.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"saved": self.saved}, handle)
            os.replace(tmp, STATE_PATH)
        except OSError as exc:
            log(f"could not persist saved light state: {exc}")

    def _run(self, *args, quiet=False):
        try:
            result = subprocess.run(
                [str(LEDCTL), *[str(a) for a in args]],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if not quiet:
                log(f"ledctl {args[0]} failed: {exc}")
            return None
        if result.returncode != 0:
            if not quiet:
                log(f"ledctl {args[0]} rc={result.returncode} {result.stderr.strip()}")
            return None
        return result.stdout.strip()

    # -- matrix -----------------------------------------------------------

    def matrix_read(self):
        out = self._run("get")
        if not out:
            return None
        try:
            kv = dict(part.split("=", 1) for part in out.split())
            return {"effect": int(kv["EFFECT"]), "hue": int(kv["HUE"]),
                    "sat": int(kv["SAT"]), "val": int(kv["VAL"]), "speed": int(kv["SPEED"])}
        except (KeyError, ValueError) as exc:
            log(f"could not parse ledctl output {out!r}: {exc}")
            return None

    def matrix_apply(self, effect, hue, sat, val, speed):
        """Apply a matrix look, capturing the user's own setting on first use."""
        with self.lock:
            applied = {"effect": effect, "hue": hue, "sat": sat, "val": val, "speed": speed}
            if applied == self.last_matrix:
                return True
            if self.saved is None:
                current = self.matrix_read()
                if current is None:
                    return False
                # Never record one of our own looks as if it were the user's.
                if current == self.last_matrix:
                    return False
                self.saved = current
                self._persist_saved()
                log(f"saved original matrix state: {self.saved}")
            if self._run("restore", effect, hue, sat, val, speed) is None:
                return False
            self.last_matrix = applied
            return True

    def matrix_restore(self):
        with self.lock:
            if self.saved is None:
                return
            saved = self.saved
            if self._run("restore", saved["effect"], saved["hue"],
                         saved["sat"], saved["val"], saved["speed"]) is None:
                return
            log(f"restored original matrix state: {saved}")
            self.saved = None
            self.last_matrix = None
            self._persist_saved()

    # -- halo -------------------------------------------------------------

    def halo_read(self):
        """Current ring animation, or None on firmware without the patch.

        Quiet on failure: unpatched firmware answers "unsupported" to every
        poll, and the settings app polls this to decide what UI to show.
        """
        out = self._run("halo-get", quiet=True)
        if not out:
            return None
        try:
            kv = dict(part.split("=", 1) for part in out.split())
            return {"mode": kv["MODE"], "r": int(kv["R"]), "g": int(kv["G"]), "b": int(kv["B"]),
                    "speed": int(kv["SPEED"]), "param": int(kv["PARAM"]), "bright": int(kv["BRIGHT"])}
        except (KeyError, ValueError):
            return None

    def halo_apply(self, mode, r, g, b, speed, param, bright):
        """Drive the ring. No save/restore bookkeeping is needed: the firmware's
        RELEASE mode hands the ring straight back, so the user's own setting is
        never read, stored or overwritten."""
        with self.lock:
            applied = (mode, r, g, b, speed, param, bright)
            if applied == self.last_halo:
                return True
            if self._run("halo", mode, r, g, b, speed, param, bright) is None:
                return False
            self.last_halo = applied
            return True

    def halo_release(self):
        with self.lock:
            if self.last_halo is None:
                return
            if self._run("halo", "release", 0, 0, 0, 0, 0, 0) is not None:
                self.last_halo = None


def apply_state(keyboard, settings, name):
    """Push one state's look at both zones."""
    spec = settings["states"].get(name) or settings["states"]["idle"]
    zones = settings["zones"]

    if zones.get("halo"):
        halo_spec = spec["halo"]
        if halo_spec["mode"] == "release":
            keyboard.halo_release()
        else:
            r, g, b, bright = hex_to_rgb(halo_spec["color"], halo_spec["brightness"])
            keyboard.halo_apply(halo_spec["mode"], r, g, b,
                                halo_spec["speed"], halo_spec["param"], bright)

    if zones.get("matrix"):
        matrix_spec = spec["matrix"]
        if matrix_spec.get("restore"):
            keyboard.matrix_restore()
        else:
            # Following the ring's colour is what makes the two zones read as
            # one signal rather than two unrelated lights.
            color = spec["halo"]["color"] if matrix_spec.get("follow_color") else matrix_spec["color"]
            hue, sat, val = hex_to_hsv(color, matrix_spec["brightness"])
            keyboard.matrix_apply(matrix_spec["effect"], hue, sat, val, matrix_spec["speed"])


class Aggregator:
    """Collapses every live Claude Code session into one global light state."""

    def __init__(self, keyboard):
        self.keyboard = keyboard
        self.lock = threading.Lock()
        self.sessions = {}
        self.completed_since = None
        self.current = None
        # Key prefixes owned by a polling source. Those sessions are re-asserted
        # every tick, so the completed-hold sweep below must leave them alone --
        # dropping one only to have the next poll put it straight back would
        # re-trigger the finish look on a loop.
        self.polled_prefixes = set()
        # While a preview runs the settings app owns the keyboard; the
        # 1-second tick in serve() is what hands it back when the preview ends.
        self.preview_until = 0.0

    def handle(self, event):
        name = event.get("hook_event_name")
        session = event.get("session_id") or "unknown"

        with self.lock:
            if name == "SessionEnd":
                self.sessions.pop(session, None)
            else:
                state = EVENT_TO_STATE.get(name)
                if state is None:
                    return
                now = time.time()
                entry = self.sessions.get(session)
                # A Bash exit code of 1 fires PostToolUseFailure, so failures are
                # routine (~3% of tool calls). The alert is therefore a brief
                # flash: hold it, then let the next tool call clear it, rather
                # than staying red for the rest of the turn.
                hold = load_settings()["failure_hold_seconds"]
                if (entry and entry["state"] == "failure" and state == "running"
                        and now - entry["updated_at"] < hold):
                    return
                self.sessions[session] = {"state": state, "updated_at": now}
        self.refresh()

    def sync_source(self, source, states):
        """Replace, in one step, every session a polling source is tracking.

        Hook events arrive one at a time and each one is the truth about a
        single session; a poller instead sees the whole world at once, so
        anything it stops reporting has genuinely gone away and must be dropped
        rather than left to age out. Keys are namespaced by source so the two
        never collide.
        """
        prefix = f"{source}:"
        now = time.time()
        with self.lock:
            self.polled_prefixes.add(prefix)
            for key in list(self.sessions):
                if key.startswith(prefix) and key not in states:
                    self.sessions.pop(key, None)
            for key, state in states.items():
                entry = self.sessions.get(key)
                if entry and entry["state"] == state:
                    entry["updated_at"] = now
                else:
                    self.sessions[key] = {"state": state, "updated_at": now}
        self.refresh()

    def resolve(self):
        states = {entry["state"] for entry in self.sessions.values()}
        for candidate in PRIORITY:
            if candidate in states:
                return candidate
        return "idle"

    def refresh(self):
        settings = load_settings()
        with self.lock:
            now = time.time()
            if self.preview_until:
                if now < self.preview_until:
                    return                # the preview owns the keyboard
                self.preview_until = 0.0
                self.current = None       # force a re-apply of the real state
            self._expire(settings)
            target = self.resolve()
            if target == "completed":
                if self.completed_since is None:
                    self.completed_since = time.time()
            else:
                self.completed_since = None
            changed = target != self.current
            self.current = target

        if changed:
            apply_state(self.keyboard, settings, target)
            log(f"state -> {target}")

    def _expire(self, settings):
        """Caller must hold the lock."""
        now = time.time()
        stale = settings["stale_session_hours"] * 3600
        stale_active = settings["stale_active_minutes"] * 60
        for key, entry in list(self.sessions.items()):
            limit = stale_active if entry["state"] in ("running", "permission", "failure") else stale
            if now - entry["updated_at"] > limit:
                log(f"dropping stale session in {entry['state']} after "
                    f"{int(now - entry['updated_at'])}s")
                self.sessions.pop(key, None)
        hold = settings["completed_hold_seconds"]
        if self.completed_since and now - self.completed_since >= hold:
            for key, entry in list(self.sessions.items()):
                if entry["state"] != "completed":
                    continue
                if any(key.startswith(p) for p in self.polled_prefixes):
                    continue          # its poller ends the hold, not us
                self.sessions.pop(key, None)
            self.completed_since = None


# Remote sessions have no hooks pointing here, so they arrive by polling Orca
# instead. Optional on purpose: a missing or broken bridge must cost the local
# path nothing.
ORCA_BRIDGE = None


def start_orca_bridge(aggregator):
    global ORCA_BRIDGE
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import orca_bridge
    except ImportError as exc:
        log(f"orca bridge unavailable: {exc}")
        return
    if not orca_bridge.load_config()["enabled"]:
        log("orca bridge disabled by orca.json")
        return
    ORCA_BRIDGE = orca_bridge.OrcaBridge(aggregator, load_settings, logger=log)
    ORCA_BRIDGE.start()
    log("orca bridge started")


def handle_command(aggregator, request):
    """Control channel for the settings app. Returns a JSON-serialisable reply."""
    command = request.get("command")
    settings = load_settings()

    if command == "status":
        with aggregator.lock:
            sessions = len(aggregator.sessions)
            breakdown = {}
            remote = 0
            for key, entry in aggregator.sessions.items():
                breakdown[entry["state"]] = breakdown.get(entry["state"], 0) + 1
                if any(key.startswith(p) for p in aggregator.polled_prefixes):
                    remote += 1
            state = aggregator.current or "idle"
            previewing = aggregator.preview_until > time.time()
        with aggregator.keyboard.lock:
            current = aggregator.keyboard.matrix_read()
            ring = aggregator.keyboard.halo_read()
        return {
            "ok": True, "version": VERSION, "state": state, "sessions": sessions,
            "sessions_by_state": breakdown, "remote_sessions": remote,
            "orca": dict(ORCA_BRIDGE.health) if ORCA_BRIDGE else None,
            "previewing": previewing, "zones": settings["zones"],
            "keyboard": current, "halo": ring, "halo_supported": ring is not None,
        }

    if command == "preview":
        seconds = request.get("seconds", 3)
        if not isinstance(seconds, (int, float)) or not 0 < seconds <= 30:
            return {"ok": False, "error": "seconds must be within 0-30"}
        name = request.get("state")
        if name not in ALL_STATES:
            return {"ok": False, "error": f"unknown state {name!r}"}
        try:
            apply_state(aggregator.keyboard, settings, name)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": f"bad preview: {exc}"}
        with aggregator.lock:
            aggregator.preview_until = time.time() + seconds
        return {"ok": True}

    if command == "reset":
        with aggregator.lock:
            aggregator.sessions.clear()
            aggregator.completed_since = None
            aggregator.preview_until = 0.0
            aggregator.current = "idle"
        apply_state(aggregator.keyboard, settings, "idle")
        return {"ok": True}

    if command == "reload":
        with aggregator.lock:
            aggregator.preview_until = 0.0
            aggregator.current = None
        aggregator.refresh()
        return {"ok": True}

    return {"ok": False, "error": f"unknown command {command!r}"}


def serve(aggregator):
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o600)
    server.listen(16)
    server.settimeout(1.0)
    log(f"daemon {VERSION} listening on {SOCKET_PATH}")

    while True:
        try:
            connection, _ = server.accept()
        except socket.timeout:
            aggregator.refresh()   # drives preview and completed-hold expiry
            continue
        except OSError as exc:
            log(f"accept failed: {exc}")
            continue
        try:
            connection.settimeout(0.5)
            chunks = []
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
            payload = b"".join(chunks).split(b"\n", 1)[0]
            if payload:
                message = json.loads(payload.decode("utf-8"))
                if isinstance(message, dict) and "command" in message:
                    reply = handle_command(aggregator, message)
                    connection.sendall(
                        json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
                else:
                    aggregator.handle(message)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log(f"bad event: {exc}")
        finally:
            connection.close()


def main():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    existing = None
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as handle:
            existing = json.load(handle).get("version")
    except (OSError, json.JSONDecodeError):
        pass
    if existing != SETTINGS_VERSION:
        if existing is not None:
            backup = SETTINGS_PATH.with_name(
                f"settings.v{existing}-{time.strftime('%Y%m%d-%H%M%S')}.json")
            SETTINGS_PATH.replace(backup)
            log(f"settings v{existing} superseded by v{SETTINGS_VERSION}, kept {backup.name}")
        with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump(DEFAULT_SETTINGS, handle, indent=2, ensure_ascii=False)
        os.chmod(SETTINGS_PATH, 0o600)

    keyboard = Keyboard()
    aggregator = Aggregator(keyboard)
    start_orca_bridge(aggregator)
    # A crash mid-task leaves a status look running; put the idle look back.
    apply_state(keyboard, load_settings(), "idle")
    try:
        serve(aggregator)
    except KeyboardInterrupt:
        pass
    finally:
        if ORCA_BRIDGE:
            ORCA_BRIDGE.stop()
        apply_state(keyboard, load_settings(), "idle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
