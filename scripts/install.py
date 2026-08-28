#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Build, install, inspect and remove the Halo75 V2 status-light service."""

import argparse
import json
import os
import plistlib
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
APP_DIR = Path.home() / "Library" / "Application Support" / "ClaudeHalo75"
BUILD_DIR = REPO / "build"
APP_SRC = REPO / "macos-app"
ASSETS = REPO / "assets"
ICON_SRC = ASSETS / "icon.png"
APP_NAME = "Claude Halo75.app"
APP_BUNDLE_ID = "com.claudehalo75.settings"
USER_APPS = Path.home() / "Applications"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.claudehalo75.daemon.plist"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
CODEX_HOOKS = Path.home() / ".codex" / "hooks.json"
ORCA_CONFIG = APP_DIR / "orca.json"
SETTINGS_JSON = APP_DIR / "settings.json"
LABEL = "com.claudehalo75.daemon"

# The marker that identifies our entries inside a settings.json that other
# tools also write to, so uninstall removes ours and nothing else.
HOOK_MARKER = "ClaudeHalo75"

HOOK_EVENTS = (
    "UserPromptSubmit",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "StopFailure",
    "SessionEnd",
)

# Codex CLI ships the same hook contract -- same event names, and the same
# `session_id` / `hook_event_name` keys on stdin, which are the only two fields
# this project reads. So halo75_hook is used verbatim; only the config file
# differs. The two events missing here do not exist in Codex: it has no
# PostToolUseFailure or StopFailure, so a Codex session never turns the ring
# red. Everything else maps one to one.
CODEX_HOOK_EVENTS = (
    "UserPromptSubmit",
    "PermissionRequest",
    "PostToolUse",
    "Stop",
    "SessionEnd",
)


def run(cmd, **kwargs):
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def build():
    BUILD_DIR.mkdir(exist_ok=True)
    run(["clang", "-Wall", "-Wextra", "-Werror", "-O2",
         "-o", BUILD_DIR / "halo75_ledctl", SRC / "halo75_ledctl.c",
         "-framework", "CoreFoundation", "-framework", "IOKit"])
    run(["clang", "-Wall", "-Wextra", "-Werror", "-O2",
         "-o", BUILD_DIR / "halo75_hook", SRC / "halo75_hook.c"])
    run(["clang", "-Wall", "-Wextra", "-Werror", "-O2",
         "-o", BUILD_DIR / "via_scan", SRC / "via_scan.c",
         "-framework", "CoreFoundation", "-framework", "IOKit"])
    print("built:", ", ".join(("halo75_ledctl", "halo75_hook", "via_scan")))


def hook_command():
    """Claude Code runs hook commands through /bin/sh, and APP_DIR contains a
    space ("Application Support"), so the path must be quoted. Unquoted it
    splits at the space and every hook dies with exit 127."""
    return shlex.quote(str(APP_DIR / "halo75_hook"))


def hook_entry():
    return {"hooks": [{"type": "command", "command": hook_command()}]}


def verify_hook_command():
    """Run the hook exactly the way Claude Code will -- through /bin/sh -- so a
    quoting or permissions bug surfaces at install time instead of as a few
    hundred silent exit-127 failures later."""
    probe = subprocess.run(
        ["/bin/sh", "-c", hook_command()],
        input=b'{"hook_event_name":"SessionEnd","session_id":"install-selftest"}',
        capture_output=True,
    )
    if probe.returncode != 0:
        print(f"  ! hook self-test failed (exit {probe.returncode}): "
              f"{probe.stderr.decode(errors='replace').strip()}")
        return False
    if probe.stdout:
        print(f"  ! hook wrote to stdout, which Claude Code parses as a decision: "
              f"{probe.stdout[:120]!r}")
        return False
    print("  hook self-test: ok (exit 0, no stdout, via /bin/sh)")
    return True


def merge_hooks():
    """Add our hooks to ~/.claude/settings.json without disturbing anyone else's."""
    CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    settings = {}
    if CLAUDE_SETTINGS.exists():
        try:
            with open(CLAUDE_SETTINGS, encoding="utf-8") as handle:
                settings = json.load(handle)
        except json.JSONDecodeError as exc:
            print(f"  ! {CLAUDE_SETTINGS} is not valid JSON ({exc}); refusing to touch it")
            return False
        backup = CLAUDE_SETTINGS.with_name(
            f"settings.json.halo75-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(CLAUDE_SETTINGS, backup)
        print(f"  backed up existing settings to {backup.name}")

    hooks = settings.setdefault("hooks", {})
    added = 0
    repaired = 0
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            print(f"  ! hooks.{event} is not a list; skipping")
            continue
        # Replace rather than skip: an earlier install may have written a
        # broken command, and skipping would leave it broken forever.
        kept = [e for e in entries if HOOK_MARKER not in json.dumps(e)]
        repaired += len(entries) - len(kept)
        kept.append(hook_entry())
        hooks[event] = kept
        added += 1

    tmp = CLAUDE_SETTINGS.with_suffix(".json.halo75-tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, CLAUDE_SETTINGS)
    print(f"  hooks: {added} installed ({repaired} stale entries replaced)")
    return True


def remove_hooks():
    if not CLAUDE_SETTINGS.exists():
        return
    try:
        with open(CLAUDE_SETTINGS, encoding="utf-8") as handle:
            settings = json.load(handle)
    except json.JSONDecodeError:
        print("  ! settings.json unreadable; left untouched")
        return
    backup = CLAUDE_SETTINGS.with_name(
        f"settings.json.halo75-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(CLAUDE_SETTINGS, backup)

    hooks = settings.get("hooks", {})
    removed = 0
    for event in list(hooks):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if HOOK_MARKER not in json.dumps(e)]
        removed += len(entries) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)
    tmp = CLAUDE_SETTINGS.with_suffix(".json.halo75-tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, CLAUDE_SETTINGS)
    print(f"  removed {removed} hook entries (backup: {backup.name})")


def write_launch_agent():
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/python3", str(APP_DIR / "claude_halo75_daemon.py")],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardErrorPath": str(APP_DIR / "launchd.err.log"),
    }
    with open(LAUNCH_AGENT, "wb") as handle:
        plistlib.dump(plist, handle)


def bootout():
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                   capture_output=True)


def bootstrap():
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(LAUNCH_AGENT)],
                   capture_output=True)


def install():
    build()
    APP_DIR.mkdir(parents=True, exist_ok=True)
    # Before touching any config: say what is actually plugged in. A scan that
    # finds nothing is the single most likely reason an install "does not work",
    # and it is much cheaper to say so here than to debug it afterwards.
    describe_scan(run_scan())
    for name in ("halo75_ledctl", "halo75_hook", "via_scan"):
        shutil.copy2(BUILD_DIR / name, APP_DIR / name)
        os.chmod(APP_DIR / name, 0o755)
    for module in ("claude_halo75_daemon.py", "orca_bridge.py"):
        shutil.copy2(SRC / module, APP_DIR / module)
    # The menu bar app shells out to this for the hook buttons; only the
    # hooks-* subcommands are used from there, and those never touch REPO.
    shutil.copy2(Path(__file__).resolve(), APP_DIR / "install.py")
    print(f"  runtime files -> {APP_DIR}")

    if not verify_hook_command():
        print("  aborting: refusing to install a hook that does not run")
        return 1
    if not merge_hooks():
        return 1
    bootout()
    write_launch_agent()
    bootstrap()
    print(f"  launch agent -> {LAUNCH_AGENT}")
    time.sleep(1)
    status()
    print("\nRestart Claude Code (fully quit, not just the window) to load the hooks.")
    return 0


# macOS asks for a different size in each place an app shows up -- Dock, Cmd-Tab
# switcher, Finder list, Get Info -- and a bundle missing the size in use falls
# back to the generic app icon rather than scaling a neighbour, so all ten are
# generated from one source image.
ICON_SIZES = (16, 32, 128, 256, 512)


def make_icns():
    """assets/icon.png -> build/AppIcon.icns, or None if there is no source."""
    if not ICON_SRC.exists():
        return None
    probe = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(ICON_SRC)],
                           capture_output=True, text=True)
    dimensions = dict(
        line.strip().split(": ", 1) for line in probe.stdout.splitlines() if ": " in line)
    width = int(dimensions.get("pixelWidth", 0))
    height = int(dimensions.get("pixelHeight", 0))
    if width != height:
        print(f"  ! {ICON_SRC.name} is {width}x{height}; macOS icons are square and it "
              f"will be squashed. Crop it to a square first.")
    if min(width, height) < 1024:
        print(f"  ! {ICON_SRC.name} is only {width}x{height}; 1024x1024 is the size the "
              f"Retina Dock icon needs, below that it will look soft.")

    BUILD_DIR.mkdir(exist_ok=True)
    iconset = BUILD_DIR / "AppIcon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for size in ICON_SIZES:
        for scale, suffix in ((1, ""), (2, "@2x")):
            target = iconset / f"icon_{size}x{size}{suffix}.png"
            pixels = size * scale
            subprocess.run(["sips", "-z", str(pixels), str(pixels), str(ICON_SRC),
                            "--out", str(target)], capture_output=True)

    icns = BUILD_DIR / "AppIcon.icns"
    result = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
                            capture_output=True, text=True)
    shutil.rmtree(iconset)
    if result.returncode != 0:
        print(f"  ! iconutil failed: {result.stderr.strip()}")
        return None
    print(f"  icon: {ICON_SRC.name} ({width}x{height}) -> {icns.name}")
    return icns


def build_app():
    """Assemble the menu bar app by hand: a SwiftPM executable plus an Info.plist
    with LSUIElement, so it lives in the menu bar and never in the Dock."""
    if not APP_SRC.exists():
        print(f"  ! {APP_SRC} not found")
        return None
    run(["swift", "build", "-c", "release", "--package-path", APP_SRC])
    binary = APP_SRC / ".build" / "release" / "ClaudeHalo75"
    if not binary.exists():
        print("  ! swift build produced no binary")
        return None

    bundle = BUILD_DIR / APP_NAME
    if bundle.exists():
        shutil.rmtree(bundle)
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (bundle / "Contents" / "Resources").mkdir()
    shutil.copy2(binary, macos / "ClaudeHalo75")
    os.chmod(macos / "ClaudeHalo75", 0o755)

    icns = make_icns()
    if icns is not None:
        shutil.copy2(icns, bundle / "Contents" / "Resources" / "AppIcon.icns")

    info = {
        "CFBundleName": "Claude Halo75",
        "CFBundleDisplayName": "Claude Halo75",
        "CFBundleIdentifier": APP_BUNDLE_ID,
        "CFBundleExecutable": "ClaudeHalo75",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "0.2.0",
        "LSMinimumSystemVersion": "13.0",
        # It owns a real settings window now, so it behaves like a normal app:
        # Dock icon, Cmd-Tab, and a menu bar item for quick access.
        "LSUIElement": False,
        "NSHumanReadableCopyright": "MIT",
    }
    if icns is not None:
        # CFBundleIconFile is what the Finder and the Dock read for a bundle
        # that was not built by Xcode; CFBundleIconName is what newer AppKit
        # prefers. Both name the same file, and setting only one leaves the
        # icon missing in whichever surface reads the other.
        info["CFBundleIconFile"] = "AppIcon"
        info["CFBundleIconName"] = "AppIcon"
    with open(bundle / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump(info, handle)

    # Ad-hoc signature: enough for Gatekeeper to run a locally built app.
    subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)],
                   capture_output=True)
    print(f"  built {bundle}")
    return bundle


def install_app():
    bundle = build_app()
    if bundle is None:
        return 1
    USER_APPS.mkdir(parents=True, exist_ok=True)
    target = USER_APPS / APP_NAME
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(bundle, target)
    print(f"  installed {target}")
    print(f'\nOpen it with:  open "{target}"')
    return 0


def hooks_install():
    if not verify_hook_command():
        print("  aborting: refusing to install a hook that does not run")
        return 1
    return 0 if merge_hooks() else 1


def hooks_uninstall():
    remove_hooks()
    return 0


# The vendor/product ids this project has actually been tested against. A board
# not on this list is not rejected -- the scan reports what it found and says
# what is missing, because "your keyboard has RGB Matrix but no ring" is a
# useful answer, not an error.
KNOWN_BOARDS = {
    ("0x19f5", "0x32f5"): "NuPhy Halo75 V2",
}


def run_scan(deep=False):
    """Ask via_scan what is plugged in. Returns the parsed JSON, or None."""
    scanner = APP_DIR / "via_scan"
    if not scanner.exists():
        scanner = BUILD_DIR / "via_scan"
    if not scanner.exists():
        return None
    args = [str(scanner)] + (["--deep"] if deep else [])
    probe = subprocess.run(args, capture_output=True, text=True, timeout=90)
    try:
        return json.loads(probe.stdout)
    except json.JSONDecodeError:
        return None


def describe_scan(report):
    """Print a scan in the terms someone deciding whether to install cares about.

    The order matters: is anything there, what is it, what can it light up, and
    only then what this project would drive. Someone whose keyboard is on 2.4G
    needs the first answer, not the fourth.
    """
    if report is None:
        print("  ! scanner not built; run 'build' first")
        return None
    devices = report.get("devices") or []
    if not devices:
        print("  no QMK/VIA keyboard found on USB.")
        print("  A 2.4G dongle or Bluetooth does not expose the VIA interface -- this")
        print("  only ever works over a USB data cable (and some cables are charge-only).")
        return None

    for device in devices:
        vid, pid = device.get("vendor_id"), device.get("product_id")
        name = device.get("product") or "(no product string)"
        vendor = device.get("manufacturer") or "?"
        known = KNOWN_BOARDS.get((vid, pid))
        # Plenty of boards repeat the vendor inside the product string.
        label = name if name.lower().startswith(vendor.lower()) else f"{vendor} {name}"
        print(f"  found: {label}  [{vid}:{pid}]"
              + (f"  -- tested: {known}" if known else "  -- not tested here, see below"))

        if not device.get("reachable"):
            print(f"    ! {device.get('error', 'not reachable')}")
            continue
        print(f"    VIA protocol {device.get('via_protocol')}", end="")
        if device.get("uptime_ms") is not None:
            print(f", up {device['uptime_ms'] // 1000}s", end="")
        print()

        lighting = device.get("lighting") or {}
        if not lighting:
            print("    lighting: none reachable over VIA. This project has nothing to drive.")
            continue
        for key, channel in lighting.items():
            values = channel.get("values") or {}
            detail = ", ".join(f"{k}={v}" for k, v in values.items())
            print(f"    {channel.get('channel')} {key:<10} {channel.get('description')}")
            if detail:
                print(f"               now: {detail}")
        extra = device.get("extra_channels") or []
        if extra:
            print(f"    also answering on undocumented channels: {', '.join(extra)}")
        elif not device.get("deep_scan"):
            print("    (only the standard channels were probed; 'scan --deep' sweeps all 256)")

        # What that means for this project, stated plainly.
        has_matrix = "rgb_matrix" in lighting
        has_ring = "halo_ring" in lighting
        print(f"    -> 内圈 (RGB Matrix): {'可用' if has_matrix else '不可用'}"
              f"    外圈 (Halo ring): {'可用' if has_ring else '需要刷本项目的固件补丁'}")
    return devices


def scan(deep=False):
    print("scanning USB for QMK keyboards that speak VIA...")
    return 0 if describe_scan(run_scan(deep=deep)) else 1


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(default))
    return value if isinstance(value, dict) else json.loads(json.dumps(default))


def write_json(path, payload, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".halo75-tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)
    os.chmod(path, mode)


def ring_firmware_present():
    """True when the keyboard answers on the vendor VIA channel the ring uses.

    This is the one honest test for "is the patched firmware on there": the
    stock firmware answers 0xff (unhandled) to channel 0x10, so halo-get fails.
    """
    ledctl = APP_DIR / "halo75_ledctl"
    if not ledctl.exists():
        ledctl = BUILD_DIR / "halo75_ledctl"
    if not ledctl.exists():
        return None
    probe = subprocess.run([str(ledctl), "halo-get"], capture_output=True, text=True)
    return probe.returncode == 0 and probe.stdout.strip().startswith("MODE=")


FLASH_RECIPE = """\
  The ring needs firmware this project builds; the factory firmware exposes it
  to Fn+M only and to nothing over VIA. Entering DFU wipes EEPROM, so back the
  keymap up first:

    build/via_backup > backups/keymap.txt
    # hold Esc and plug the keyboard in
    dfu-util -a 0 -d 0483:df11 -s 0x08000000:leave \\
             -D ~/qmk_nuphy/nuphy_halo75_v2_ansi_via.bin
    build/via_restore backups/keymap.txt

  Full instructions, including the toolchain: firmware/README.md.
  Roll back with backups/stock-firmware-backup.bin, flashed the same way."""


def reconnect(off=False):
    """Opt in to (or back out of) the two things a stock setup does not do.

    Out of the box only the typing area lights up, from local sessions. This
    turns on the ring -- which needs the patched firmware -- and the Orca
    bridge, which is what makes sessions on other machines show up here.
    """
    if off:
        settings = read_json(SETTINGS_JSON, {})
        settings.setdefault("zones", {})["halo"] = False
        write_json(SETTINGS_JSON, settings)
        orca = read_json(ORCA_CONFIG, {})
        orca["enabled"] = False
        write_json(ORCA_CONFIG, orca)
        print("  ring: off (handed back to the firmware)")
        print("  orca bridge: off (local sessions only)")
        nudge_daemon()
        return 0

    describe_scan(run_scan())
    present = ring_firmware_present()
    if present is None:
        print("  ! halo75_ledctl not built; run 'install' first")
        return 1

    settings = read_json(SETTINGS_JSON, {})
    settings.setdefault("zones", {})["halo"] = bool(present)
    settings["zones"].setdefault("matrix", True)
    write_json(SETTINGS_JSON, settings)
    if present:
        print("  ring: firmware answers on VIA channel 0x10 -- enabled")
    else:
        print("  ring: NOT enabled -- the keyboard does not answer on channel 0x10.")
        print("        Either it is on 2.4G/Bluetooth (only USB exposes VIA), VIA or")
        print("        the NuPhy app is holding the raw HID interface, or the firmware")
        print("        is stock.")
        print(FLASH_RECIPE)

    orca = read_json(ORCA_CONFIG, {})
    orca["enabled"] = True
    write_json(ORCA_CONFIG, orca)
    print("  orca bridge: on -- sessions on Orca SSH hosts and paired remote")
    print("               runtimes now feed the same lights")

    nudge_daemon()
    return 0


def nudge_daemon():
    """Make the daemon pick the config up now rather than on the next event."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2.0)
    try:
        client.connect(str(APP_DIR / "status.sock"))
        client.sendall(json.dumps({"command": "reload"}).encode() + b"\n")
        client.recv(4096)
    except OSError:
        print("  (daemon not answering; it will read the change when it next starts)")
    finally:
        client.close()


def codex_hooks_install(remove=False):
    """Point Codex CLI's hooks at the same binary Claude Code uses.

    Codex reads ~/.codex/hooks.json and delivers the same two fields this
    project cares about -- session_id and hook_event_name -- so the hook binary
    and the daemon need no changes at all. What is missing is the failure path:
    Codex has no PostToolUseFailure or StopFailure, so a Codex session drives
    every state except the red one.
    """
    config = read_json(CODEX_HOOKS, {}) if CODEX_HOOKS.exists() else {}
    if CODEX_HOOKS.exists():
        backup = CODEX_HOOKS.with_name(
            f"hooks.json.halo75-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(CODEX_HOOKS, backup)
        print(f"  backed up existing hooks to {backup.name}")

    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        print("  ! ~/.codex/hooks.json has a non-object 'hooks'; refusing to touch it")
        return 1

    touched = 0
    for event in CODEX_HOOK_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = []
        kept = [g for g in groups if HOOK_MARKER not in json.dumps(g)]
        if not remove:
            # Codex reads stdout as a decision exactly as Claude Code does, and
            # the hook writes none, so nothing here can block a turn. async
            # keeps it off the critical path anyway.
            kept.append({"hooks": [{"type": "command",
                                    "command": hook_command(),
                                    "async": True}]})
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
        touched += 1

    if not hooks:
        config.pop("hooks", None)
    write_json(CODEX_HOOKS, config, mode=0o644)
    print(f"  codex hooks: {'removed from' if remove else 'installed into'} {CODEX_HOOKS} "
          f"({touched} events)")
    if not remove:
        print("  note: Codex has no PostToolUseFailure/StopFailure, so Codex sessions")
        print("        never turn the lights red. Everything else behaves as it does")
        print("        for Claude Code, and the two agents share one aggregator.")
    return 0


def daemon_status():
    """Ask the running daemon for its live status, or None if it is not there."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2.0)
    try:
        client.connect(str(APP_DIR / "status.sock"))
        client.sendall(json.dumps({"command": "status"}).encode() + b"\n")
        chunks = []
        while b"\n" not in b"".join(chunks):
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        client.close()


def status():
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    running = any(LABEL in line for line in out.splitlines())
    print(f"service:  {'running' if running else 'not running'}")

    sock = APP_DIR / "status.sock"
    print(f"socket:   {'present' if sock.exists() else 'missing'} ({sock})")

    installed = 0
    if CLAUDE_SETTINGS.exists():
        try:
            with open(CLAUDE_SETTINGS, encoding="utf-8") as handle:
                hooks = json.load(handle).get("hooks", {})
            installed = sum(
                1 for event in HOOK_EVENTS
                if any(HOOK_MARKER in json.dumps(e) for e in hooks.get(event, [])))
        except json.JSONDecodeError:
            installed = -1
    print(f"hooks:    {installed}/{len(HOOK_EVENTS)} installed in {CLAUDE_SETTINGS}")

    # The two opt-in pieces, read from disk so they are visible with the daemon
    # down as well.
    zones = read_json(SETTINGS_JSON, {}).get("zones") or {}
    ring_on = zones.get("halo", False)
    firmware = ring_firmware_present()
    print(f"ring:     {'on' if ring_on else 'off (default)'}, firmware "
          f"{'present' if firmware else 'not answering on VIA channel 0x10'}"
          + ("" if ring_on or not firmware else " -- run 'reconnect' to switch it on"))

    codex = 0
    if CODEX_HOOKS.exists():
        try:
            with open(CODEX_HOOKS, encoding="utf-8") as handle:
                ch = json.load(handle).get("hooks", {})
            codex = sum(1 for event in CODEX_HOOK_EVENTS
                        if any(HOOK_MARKER in json.dumps(e) for e in ch.get(event, [])))
        except (OSError, json.JSONDecodeError):
            codex = -1
    print(f"codex:    {codex}/{len(CODEX_HOOK_EVENTS)} hooks in {CODEX_HOOKS}")

    # Hooks cover this machine; the Orca bridge is what covers everywhere else.
    live = daemon_status()
    if live is None:
        print("orca:     daemon not answering")
    elif live.get("orca") is None:
        print("orca:     bridge off (no orca_bridge module, or disabled in orca.json)")
    else:
        health = live["orca"]
        when = health.get("last_poll")
        age = f"{int(time.time() - when)}s ago" if when else "never"
        print(f"orca:     last poll {age}, {live.get('remote_sessions', 0)} remote session(s)"
              + (f", error: {health['error']}" if health.get("error") else ""))

    ledctl = APP_DIR / "halo75_ledctl"
    if ledctl.exists():
        probe = subprocess.run([str(ledctl), "get"], capture_output=True, text=True)
        print(f"keyboard: {probe.stdout.strip() or probe.stderr.strip() or 'no response'}")
    else:
        print("keyboard: ledctl not installed")

    state = APP_DIR / "state.json"
    if state.exists():
        try:
            with open(state, encoding="utf-8") as handle:
                saved = json.load(handle).get("saved")
            print(f"saved:    {saved if saved else 'none (keyboard not taken over)'}")
        except json.JSONDecodeError:
            print("saved:    unreadable")
    print(f"settings: {APP_DIR / 'settings.json'}")


def test_hid():
    ledctl = APP_DIR / "halo75_ledctl" if (APP_DIR / "halo75_ledctl").exists() \
        else BUILD_DIR / "halo75_ledctl"
    if not ledctl.exists():
        print("run 'build' first")
        return 1
    probe = subprocess.run([str(ledctl), "get"], capture_output=True, text=True)
    if probe.returncode != 0:
        print("failed:", probe.stderr.strip())
        return 1
    original = probe.stdout.strip()
    kv = dict(p.split("=", 1) for p in original.split())
    print("current:", original)
    print("turning the typing area green for 5 seconds (Halo strip is not touched)...")
    subprocess.run([str(ledctl), "restore", "1", "85", "255", "254", "128"])
    time.sleep(5)
    subprocess.run([str(ledctl), "restore", kv["EFFECT"], kv["HUE"],
                    kv["SAT"], kv["VAL"], kv["SPEED"]])
    print("restored:", subprocess.run([str(ledctl), "get"],
                                      capture_output=True, text=True).stdout.strip())
    return 0


def send_test_event(event, session):
    sock = APP_DIR / "status.sock"
    payload = json.dumps({"hook_event_name": event, "session_id": session}).encode() + b"\n"
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    try:
        client.connect(str(sock))
        client.sendall(payload)
        print(f"sent {event} for session {session}")
    except OSError as exc:
        print(f"could not reach daemon: {exc}")
        return 1
    finally:
        client.close()
    return 0


def uninstall():
    bootout()
    if LAUNCH_AGENT.exists():
        LAUNCH_AGENT.unlink()
        print(f"  removed {LAUNCH_AGENT}")
    remove_hooks()
    ledctl = APP_DIR / "halo75_ledctl"
    state = APP_DIR / "state.json"
    if ledctl.exists() and state.exists():
        try:
            with open(state, encoding="utf-8") as handle:
                saved = json.load(handle).get("saved")
            if saved:
                subprocess.run([str(ledctl), "restore", str(saved["effect"]), str(saved["hue"]),
                                str(saved["sat"]), str(saved["val"]), str(saved["speed"])])
                print("  restored original light state")
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
        print(f"  removed {APP_DIR}")
    app = USER_APPS / APP_NAME
    if app.exists():
        shutil.rmtree(app)
        print(f"  removed {app}")
    print("\nRestart Claude Code to drop the hooks.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "install", "status", "test-hid", "uninstall",
                 "build-app", "install-app", "hooks-install", "hooks-uninstall",
                 "icon", "codex-hooks-install", "codex-hooks-uninstall"):
        sub.add_parser(name)
    sc = sub.add_parser("scan")
    sc.add_argument("--deep", action="store_true",
                    help="sweep all 256 custom channels, not just the standard ones")
    sc.add_argument("--json", action="store_true", help="raw JSON instead of a summary")
    rc = sub.add_parser("reconnect")
    rc.add_argument("--off", action="store_true",
                    help="back out: hand the ring back and stop polling Orca")
    event = sub.add_parser("send-event")
    event.add_argument("event", choices=sorted(HOOK_EVENTS))
    event.add_argument("--session", default="manual-test")

    args = parser.parse_args()
    if args.command == "build":
        build()
        return 0
    if args.command == "install":
        return install()
    if args.command == "status":
        status()
        return 0
    if args.command == "test-hid":
        return test_hid()
    if args.command == "send-event":
        return send_test_event(args.event, args.session)
    if args.command == "build-app":
        return 0 if build_app() else 1
    if args.command == "install-app":
        return install_app()
    if args.command == "hooks-install":
        return hooks_install()
    if args.command == "hooks-uninstall":
        return hooks_uninstall()
    if args.command == "scan":
        if args.json:
            report = run_scan(deep=args.deep)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if (report or {}).get("devices") else 1
        return scan(deep=args.deep)
    if args.command == "reconnect":
        return reconnect(off=args.off)
    if args.command == "icon":
        return 0 if make_icns() else 1
    if args.command == "codex-hooks-install":
        if not verify_hook_command():
            print("  aborting: refusing to install a hook that does not run")
            return 1
        return codex_hooks_install()
    if args.command == "codex-hooks-uninstall":
        return codex_hooks_install(remove=True)
    if args.command == "uninstall":
        return uninstall()
    return 1


if __name__ == "__main__":
    sys.exit(main())
