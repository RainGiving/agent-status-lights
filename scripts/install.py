#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Build, install, inspect and remove the Halo65 V2 status-light service."""

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
SCRIPTS = REPO / "scripts"
APP_DIR = Path.home() / "Library" / "Application Support" / "ClaudeHalo65"
BUILD_DIR = REPO / "build"
APP_SRC = REPO / "macos-app"
ASSETS = REPO / "assets"
ICON_SRC = ASSETS / "icon.png"
# The Liquid Glass icon. actool ships with Xcode, not with the command line
# tools, so icon.png stays as the fallback for a machine that only has the CLT.
GLASS_ICON_SRC = ASSETS / "HALO.icon"
GLASS_ICON_NAME = "HALO"
APP_NAME = "HALO.app"
# Bundles this project installed under its earlier names. Left behind they would
# show up twice in Spotlight and in the Dock's recents.
LEGACY_APP_NAMES = ("Claude Halo65.app", "Claude Halo75.app")
APP_BUNDLE_ID = "com.claudehalo65.settings"
USER_APPS = Path.home() / "Applications"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.claudehalo65.daemon.plist"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
CODEX_HOOKS = Path.home() / ".codex" / "hooks.json"
ORCA_CONFIG = APP_DIR / "orca.json"
SETTINGS_JSON = APP_DIR / "settings.json"
LABEL = "com.claudehalo65.daemon"
# The voice watcher is a second agent on purpose: Input Monitoring is granted to
# the process that asks for it, and a helper spawned by the Python daemon would
# put that grant on /usr/bin/python3 instead of on this project's own binary.
VOICE_LABEL = "com.claudehalo65.voice"
VOICE_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.claudehalo65.voice.plist"

# The LED-bit sender is a third agent for the same reason as the voice watcher:
# writing an LED on a keyboard means opening the keyboard, which macOS gates
# behind Input Monitoring, and that permission has to belong to a binary rather
# than to /usr/bin/python3.
LEDS_LABEL = "com.claudehalo65.leds"
LEDS_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.claudehalo65.leds.plist"

# The marker that identifies our entries inside a settings.json that other
# tools also write to, so uninstall removes ours and nothing else.
HOOK_MARKER = "ClaudeHalo65"

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
# this project reads. So halo65_hook is used verbatim; only the config file
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
         "-o", BUILD_DIR / "halo65_ledctl", SRC / "halo65_ledctl.c",
         "-framework", "CoreFoundation", "-framework", "IOKit"])
    run(["clang", "-Wall", "-Wextra", "-Werror", "-O2",
         "-o", BUILD_DIR / "halo65_hook", SRC / "halo65_hook.c"])
    run(["clang", "-Wall", "-Wextra", "-Werror", "-O2",
         "-o", BUILD_DIR / "via_scan", SRC / "via_scan.c",
         "-framework", "CoreFoundation", "-framework", "IOKit"])
    run(["clang", "-Wall", "-Wextra", "-Werror", "-O2",
         "-o", BUILD_DIR / "halo65_voice", SRC / "halo65_voice.c",
         "-framework", "CoreFoundation", "-framework", "ApplicationServices",
         "-framework", "CoreAudio", "-framework", "IOKit"])
    run(["clang", "-Wall", "-Wextra", "-Werror", "-O2",
         "-o", BUILD_DIR / "halo65_leds", SRC / "halo65_leds.c",
         "-framework", "CoreFoundation", "-framework", "IOKit"])
    # Only the firmware path uses these, but build/ is not checked in, so a
    # fresh clone that skipped them would reach "back up the keymap before
    # DFU" with no way to do it -- the one step that must not be skipped.
    for tool in ("via_backup", "via_restore"):
        run(["clang", "-Wall", "-Wextra", "-Werror", "-O2",
             "-o", BUILD_DIR / tool, SCRIPTS / f"{tool}.c",
             "-framework", "CoreFoundation", "-framework", "IOKit"])
    print("built:", ", ".join(("halo65_ledctl", "halo65_hook", "via_scan",
                               "halo65_voice", "halo65_leds", "via_backup",
                               "via_restore")))


def hook_command():
    """Claude Code runs hook commands through /bin/sh, and APP_DIR contains a
    space ("Application Support"), so the path must be quoted. Unquoted it
    splits at the space and every hook dies with exit 127."""
    return shlex.quote(str(APP_DIR / "halo65_hook"))


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
            f"settings.json.halo65-backup-{time.strftime('%Y%m%d-%H%M%S')}")
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

    tmp = CLAUDE_SETTINGS.with_suffix(".json.halo65-tmp")
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
        f"settings.json.halo65-backup-{time.strftime('%Y%m%d-%H%M%S')}")
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
    tmp = CLAUDE_SETTINGS.with_suffix(".json.halo65-tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, CLAUDE_SETTINGS)
    print(f"  removed {removed} hook entries (backup: {backup.name})")


def write_launch_agent():
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/python3", str(APP_DIR / "claude_halo65_daemon.py")],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardErrorPath": str(APP_DIR / "launchd.err.log"),
    }
    with open(LAUNCH_AGENT, "wb") as handle:
        plistlib.dump(plist, handle)


def write_voice_agent():
    VOICE_AGENT.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": VOICE_LABEL,
        "ProgramArguments": [str(APP_DIR / "halo65_voice")],
        "RunAtLoad": True,
        "KeepAlive": True,
        # It exits when Input Monitoring is missing, so that granting the
        # permission is picked up by the next restart rather than needing one by
        # hand. The throttle is what keeps that from becoming a spin.
        "ThrottleInterval": 300,
        "StandardOutPath": str(APP_DIR / "voice.log"),
        "StandardErrorPath": str(APP_DIR / "voice.log"),
    }
    with open(VOICE_AGENT, "wb") as handle:
        plistlib.dump(plist, handle)


def bootout():
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                   capture_output=True)


def voice_bootout():
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{VOICE_LABEL}"],
                   capture_output=True)


def voice_bootstrap():
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(VOICE_AGENT)],
                   capture_output=True)


def write_leds_agent():
    LEDS_AGENT.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LEDS_LABEL,
        "ProgramArguments": [str(APP_DIR / "halo65_leds")],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 300,
        "StandardOutPath": str(APP_DIR / "leds.log"),
        "StandardErrorPath": str(APP_DIR / "leds.log"),
    }
    with open(LEDS_AGENT, "wb") as handle:
        plistlib.dump(plist, handle)


def leds_bootout():
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LEDS_LABEL}"],
                   capture_output=True)


def leds_bootstrap():
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(LEDS_AGENT)],
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
    for name in ("halo65_ledctl", "halo65_hook", "via_scan", "halo65_voice",
                 "halo65_leds"):
        shutil.copy2(BUILD_DIR / name, APP_DIR / name)
        os.chmod(APP_DIR / name, 0o755)
    # TCC records Input Monitoring against the binary's code identity, and an
    # unsigned binary has none it can hold on to. With ad-hoc signing the
    # identity pins the binary's hash, so every rebuild loses the grant; a
    # certificate identity is identifier + leaf, which rebuilds keep. The
    # "ClaudeHalo65 Signing" self-signed certificate is created once by hand
    # (Keychain Access or openssl + security import/add-trusted-cert); when
    # it is absent this falls back to ad-hoc and the re-grant dance returns.
    identity = "-"
    found = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"],
                           capture_output=True, text=True)
    if "ClaudeHalo65 Signing" in found.stdout:
        identity = "ClaudeHalo65 Signing"
    for binary, identifier in (("halo65_voice", "com.claudehalo65.voice"),
                               ("halo65_leds", "com.claudehalo65.leds")):
        subprocess.run(["codesign", "--force", "--sign", identity,
                        "--identifier", identifier, str(APP_DIR / binary)],
                       capture_output=True)
    for module in ("claude_halo65_daemon.py", "orca_bridge.py"):
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
    voice_bootout()
    write_voice_agent()
    voice_bootstrap()
    print(f"  voice watcher -> {VOICE_AGENT}")
    leds_bootout()
    write_leds_agent()
    leds_bootstrap()
    print(f"  led sender    -> {LEDS_AGENT}")
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
    if "hasAlpha: yes" not in probe.stdout and "hasAlpha: yes" not in subprocess.run(
            ["sips", "-g", "hasAlpha", str(ICON_SRC)], capture_output=True, text=True).stdout:
        # macOS does not round or mask an app icon for you. An opaque source
        # becomes a hard-edged square in the Dock, next to neighbours that are
        # all rounded.
        print(f"  ! {ICON_SRC.name} has no transparency; it will show up as a square "
              f"icon. Give it a transparent background outside the icon shape.")

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


def make_glass_icon():
    """assets/HALO.icon -> (icns, Assets.car), or None when actool is missing.

    Liquid Glass is rendered by the system from the layers in the .icon bundle,
    so the app has to ship the compiled asset catalogue rather than a flat
    picture of it: Assets.car is what carries the glass, and the .icns beside it
    is the still image every pre-26 surface falls back to.
    """
    if not GLASS_ICON_SRC.exists():
        return None
    probe = subprocess.run(["xcrun", "--find", "actool"], capture_output=True, text=True)
    if probe.returncode != 0:
        print("  ! actool not found (needs full Xcode); falling back to icon.png")
        return None

    out = BUILD_DIR / "icon"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    result = subprocess.run(
        ["xcrun", "actool", "--output-format", "human-readable-text", "--errors", "--warnings",
         "--app-icon", GLASS_ICON_NAME, "--compile", str(out), "--platform", "macosx",
         "--minimum-deployment-target", "26.0",
         "--output-partial-info-plist", str(out / "partial.plist"), str(GLASS_ICON_SRC)],
        capture_output=True, text=True)
    icns = out / f"{GLASS_ICON_NAME}.icns"
    car = out / "Assets.car"
    if result.returncode != 0 or not icns.exists() or not car.exists():
        print(f"  ! actool failed: {(result.stderr or result.stdout).strip()[:300]}")
        return None
    print(f"  icon: {GLASS_ICON_SRC.name} -> {icns.name} + Assets.car (liquid glass)")
    return icns, car


def build_app():
    """Assemble the menu bar app by hand: a SwiftPM executable plus an Info.plist
    with LSUIElement, so it lives in the menu bar and never in the Dock."""
    if not APP_SRC.exists():
        print(f"  ! {APP_SRC} not found")
        return None
    run(["swift", "build", "-c", "release", "--package-path", APP_SRC])
    binary = APP_SRC / ".build" / "release" / "ClaudeHalo65"
    if not binary.exists():
        print("  ! swift build produced no binary")
        return None

    bundle = BUILD_DIR / APP_NAME
    if bundle.exists():
        shutil.rmtree(bundle)
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (bundle / "Contents" / "Resources").mkdir()
    shutil.copy2(binary, macos / "ClaudeHalo65")
    os.chmod(macos / "ClaudeHalo65", 0o755)

    resources = bundle / "Contents" / "Resources"
    glass = make_glass_icon()
    icon_name = None
    if glass is not None:
        icns, car = glass
        shutil.copy2(icns, resources / f"{GLASS_ICON_NAME}.icns")
        shutil.copy2(car, resources / "Assets.car")
        icon_name = GLASS_ICON_NAME
    else:
        icns = make_icns()
        if icns is not None:
            shutil.copy2(icns, resources / "AppIcon.icns")
            icon_name = "AppIcon"

    info = {
        "CFBundleName": "HALO",
        "CFBundleDisplayName": "HALO",
        "CFBundleIdentifier": APP_BUNDLE_ID,
        "CFBundleExecutable": "ClaudeHalo65",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "0.2.0",
        "LSMinimumSystemVersion": "13.0",
        # It owns a real settings window now, so it behaves like a normal app:
        # Dock icon, Cmd-Tab, and a menu bar item for quick access.
        "LSUIElement": False,
        "NSHumanReadableCopyright": "MIT",
    }
    if icon_name is not None:
        # CFBundleIconFile is what the Finder and the Dock read for a bundle
        # that was not built by Xcode; CFBundleIconName is what newer AppKit
        # prefers, and on macOS 26 it is also what points at the glass icon
        # inside Assets.car. Setting only one leaves the icon missing in
        # whichever surface reads the other.
        info["CFBundleIconFile"] = icon_name
        info["CFBundleIconName"] = icon_name
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
    for legacy in LEGACY_APP_NAMES:
        stale = USER_APPS / legacy
        if stale.exists():
            shutil.rmtree(stale)
            print(f"  removed the old {legacy}")
    # The icon is cached per bundle path, so a rename shows the previous icon in
    # the Dock until Launch Services is told the bundle changed.
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks"
                    "/LaunchServices.framework/Support/lsregister", "-f", str(target)],
                   capture_output=True)
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
    ("0x19f5", "0x3315"): "NuPhy Halo65 V2",
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
    tmp = path.with_suffix(path.suffix + ".halo65-tmp")
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
    ledctl = APP_DIR / "halo65_ledctl"
    if not ledctl.exists():
        ledctl = BUILD_DIR / "halo65_ledctl"
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
             -D ~/qmk_nuphy/nuphy_halo65_v2_ansi_via.bin
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
        print("  ! halo65_ledctl not built; run 'install' first")
        return 1

    settings = read_json(SETTINGS_JSON, {})
    already_on = bool((settings.get("zones") or {}).get("halo"))
    if present:
        settings.setdefault("zones", {})["halo"] = True
    elif already_on:
        # No VIA answer proves nothing over Bluetooth -- the interface simply
        # is not there. A ring that was verified and enabled earlier keeps
        # working through the LED-bit channel, so never switch it off here.
        print("  ring: no VIA answer on USB (Bluetooth does not expose VIA);")
        print("        keeping the existing on switch unchanged")
    else:
        settings.setdefault("zones", {})["halo"] = False
    settings.setdefault("zones", {}).setdefault("matrix", True)
    write_json(SETTINGS_JSON, settings)
    if present:
        print("  ring: firmware answers on VIA channel 0x10 -- enabled")
    elif not already_on:
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
            f"hooks.json.halo65-backup-{time.strftime('%Y%m%d-%H%M%S')}")
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

    print(f"voice:    {describe_voice(live)}")

    # The wireless path: what the LED-bit sender says about itself.
    leds = {}
    try:
        with open(APP_DIR / "leds.status", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.strip().partition("=")
                leds[key] = value
    except OSError:
        pass
    if not leds:
        print("leds:     sender never started (wireless path unavailable)")
    elif leds.get("sender") != "running":
        print(f"leds:     {leds.get('sender')} (input monitoring: "
              f"{leds.get('input_monitoring', 'unknown')}) -- run 'leds' to grant")
    else:
        reach = leds.get("transports") or "no keyboard interface"
        sync = f", syncing {leds.get('sync_progress')}%" if leds.get("mode") == "sync" else ""
        print(f"leds:     running, {leds.get('devices', '0')} interface(s) [{reach}]"
              f", state {leds.get('state', '?')}{sync}")

    ledctl = APP_DIR / "halo65_ledctl"
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


# Only the keys anyone would sensibly bind a dictation shortcut to. Anything
# else prints as its raw virtual keycode, which is what the settings app and
# voice.conf both speak anyway.
KEY_NAMES = {49: "Space", 36: "Return", 48: "Tab", 53: "Esc", 51: "Delete",
             96: "F5", 97: "F6", 98: "F7", 99: "F3", 100: "F8", 101: "F9",
             109: "F10", 103: "F11", 111: "F12", 122: "F1", 120: "F2", 118: "F4"}
MODIFIER_SYMBOLS = {"control": "⌃", "option": "⌥", "shift": "⇧", "command": "⌘"}


def describe_shortcut(voice):
    keys = "".join(MODIFIER_SYMBOLS.get(m, m) for m in voice.get("modifiers", []))
    code = voice.get("keycode", 49)
    return keys + KEY_NAMES.get(code, f"key {code}")


def describe_voice(live):
    """One line: what turns the voice light on, and whether that can work."""
    if live is None or not isinstance(live.get("voice"), dict):
        return "daemon not answering"
    voice = live["voice"]
    if not voice.get("enabled"):
        return "off (turn it on in the settings app, 语音输入 page)"

    trigger = voice.get("trigger", "hotkey")
    parts = []
    if trigger in ("hotkey", "both"):
        parts.append(f"{describe_shortcut(voice)} ({voice.get('mode', 'hold')})")
    if trigger in ("microphone", "both"):
        parts.append("microphone in use")
    watcher = voice.get("watcher", "unknown")
    line = " + ".join(parts) + f", watcher {watcher}"
    if watcher == "needs input monitoring":
        line += (f"\n          add {APP_DIR / 'halo65_voice'}\n"
                 "          in System Settings > Privacy & Security > Input Monitoring, "
                 "then run 'voice'")
    return line


def voice_setup():
    """Print what the watcher needs, and open the pane where it is granted."""
    live = daemon_status()
    print(f"voice:    {describe_voice(live)}")
    watcher = (live or {}).get("voice", {}).get("watcher")
    if watcher == "running":
        print("  nothing to do")
        return 0
    # Asking again is the point of running this by hand.
    (APP_DIR / "voice.asked").unlink(missing_ok=True)
    print(f"\n  the binary to allow is:\n    {APP_DIR / 'halo65_voice'}")
    print("  opening System Settings > Privacy & Security > Input Monitoring")
    print("  drag that path in with + , or turn its switch on if it is listed already")
    subprocess.run(["open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"])
    # The grant only reaches a running process on restart, and launchd is what
    # restarts it.
    voice_bootout()
    voice_bootstrap()
    print("\n  the watcher was restarted; check 'status' once the switch is on")
    return 0


def leds_setup():
    """Print what the LED sender needs, and open the pane where it is granted."""
    status_path = APP_DIR / "leds.status"
    state = {}
    try:
        with open(status_path, encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.strip().partition("=")
                state[key] = value
    except OSError:
        pass
    print(f"led sender: {state.get('sender', 'never started')}"
          f" (input monitoring: {state.get('input_monitoring', 'unknown')})")
    if state.get("sender") == "running":
        print("  nothing to do")
        return 0
    print(f"\n  the binary to allow is:\n    {APP_DIR / 'halo65_leds'}")
    print("  opening System Settings > Privacy & Security > Input Monitoring")
    print("  drag that path in with + , or turn its switch on if it is listed already")
    subprocess.run(["open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"])
    leds_bootout()
    leds_bootstrap()
    print("\n  the sender was restarted; check 'status' once the switch is on")
    return 0


def test_hid():
    ledctl = APP_DIR / "halo65_ledctl" if (APP_DIR / "halo65_ledctl").exists() \
        else BUILD_DIR / "halo65_ledctl"
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
    leds_bootout()
    if LEDS_AGENT.exists():
        LEDS_AGENT.unlink()
        print(f"  removed {LEDS_AGENT}")
    voice_bootout()
    for agent in (LAUNCH_AGENT, VOICE_AGENT):
        if agent.exists():
            agent.unlink()
            print(f"  removed {agent}")
    remove_hooks()
    ledctl = APP_DIR / "halo65_ledctl"
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
    for name in (APP_NAME,) + LEGACY_APP_NAMES:
        app = USER_APPS / name
        if app.exists():
            shutil.rmtree(app)
            print(f"  removed {app}")
    print("\nRestart Claude Code to drop the hooks.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "install", "status", "test-hid", "uninstall", "leds",
                 "build-app", "install-app", "hooks-install", "hooks-uninstall",
                 "icon", "codex-hooks-install", "codex-hooks-uninstall", "voice"):
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
    if args.command == "leds":
        return leds_setup()
    if args.command == "test-hid":
        return test_hid()
    if args.command == "voice":
        return voice_setup()
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
