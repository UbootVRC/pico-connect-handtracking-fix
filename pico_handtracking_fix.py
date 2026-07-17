#!/usr/bin/env python3
r"""
pico_handtracking_fix.py
========================
Force PICO Connect (10.6.6) to enable hand tracking on the PICO 4 Enterprise
by winning the startup race described in RootCause.txt.

WHAT IT DOES
------------
PICO Connect enables hand tracking ONLY if the headset connects BEFORE the
remote-config HTTPS fetch to setting-global-api.picovr.com returns. On a fast
network the fetch wins and hand tracking stays off for the whole session.

This tool guarantees the correct order:

  1. Resolves the real IP of setting-global-api.picovr.com and caches it.
  2. Redirects that hostname to 127.0.0.1 via the Windows hosts file.
  3. Runs a local TCP relay on :443 that ACCEPTS PICO Connect's HTTPS
     connection but STALLS it (no bytes forwarded yet).
  4. Watches the PICO Connect logs. The instant it sees the headset connect
     ("hmd request connnect callback"), it RELEASES the stalled connection,
     which then completes normally against the real server.

Result: the headset connect (pushes handTracking:false) always lands before
the fetch returns (pushes handTracking:true), so TRUE is applied last ->
hand tracking ON.

It is a raw byte relay: TLS is NOT intercepted, the real server terminates
TLS, the certificate validates normally. No CA cert needed.

REQUIREMENTS
------------
- Windows, Python 3.8+ (standard library only).
- Administrator (needed to edit the hosts file). The script auto-elevates.

USAGE
-----
  python pico_handtracking_fix.py            # normal run (auto-elevates)
  python pico_handtracking_fix.py --hold 20  # fixed-delay fallback if logs
                                             #   can't be found (hold 20s)
  python pico_handtracking_fix.py --restore  # emergency: undo hosts edit only

STEPS WHEN YOU RUN IT
---------------------
  1. Make sure PICO Connect is CLOSED before starting the script.
  2. Run the script. It arms the relay and waits.
  3. When it says "ARMED", launch PICO Connect and connect your headset.
  4. It auto-releases on headset connect, confirms "handTracking true" in the
     log, then cleans up the hosts file automatically.
  5. Put your controllers down -> hands appear in SteamVR.

Ctrl+C at any time restores the hosts file and exits cleanly.
"""

import argparse
import ctypes
import glob
import os
import re
import socket
import subprocess
import sys
import threading
import time

HOST = "setting-global-api.picovr.com"
LISTEN_ADDR = "127.0.0.1"
LISTEN_PORT = 443
HOSTS_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "drivers", "etc", "hosts")
HOSTS_MARKER = "# PICO-HT-FIX (auto-added; safe to delete)"
APPDATA = os.environ.get("APPDATA", "")
LOG_GLOBS = [
    os.path.join(APPDATA, "PICO Connect", "logs", "pico_connect*.log"),
    os.path.join(APPDATA, "PICO Connect", "logs", "main.log"),
]
SETTINGS_PATH = os.path.join(APPDATA, "PICO Connect", "settings.json")

# Log line patterns (the triple-n "connnect" is verbatim from PICO's logs).
RE_CONNECT = re.compile(r"hmd request conn+ect callback", re.IGNORECASE)
# The fetch RETURNING is the real success marker: this line is written only
# after the config response has been received, and only it drives the
# handTracking:true push. (Do NOT match a bare 'handTracking:true' substring —
# the InitializePICOConnectInfoEvent at launch contains one and would fire a
# false positive.)
RE_FETCH = re.compile(r"get current streaming configs on appsettings", re.IGNORECASE)
RE_FETCH_ERR = re.compile(r"get appsettings error", re.IGNORECASE)

# Shared state
release_event = threading.Event()
success_event = threading.Event()
fail_event = threading.Event()
stop_event = threading.Event()


try:
    _LOGFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fix_run.log")
except Exception:
    _LOGFILE = "fix_run.log"


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(_LOGFILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Admin / elevation
# --------------------------------------------------------------------------
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin():
    log("Requesting administrator privileges (needed to edit the hosts file)...")
    params = " ".join(f'"{a}"' for a in sys.argv)
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1)
    if rc <= 32:
        log("ERROR: elevation was declined or failed. Run from an "
            "Administrator terminal instead.")
        sys.exit(1)
    sys.exit(0)


# --------------------------------------------------------------------------
# Hosts file management
# --------------------------------------------------------------------------
def hosts_add():
    try:
        with open(HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        log(f"ERROR reading hosts file: {e}")
        raise
    # Clean any leftover entry from a previous crashed run first.
    content = _hosts_strip(content)
    if not content.endswith("\n") and content:
        content += "\n"
    content += f"{HOSTS_MARKER}\n{LISTEN_ADDR} {HOST}\n"
    with open(HOSTS_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    flush_dns()
    log(f"Hosts: {HOST} -> {LISTEN_ADDR}")


def _hosts_strip(content):
    lines = content.splitlines()
    out = []
    for line in lines:
        if HOSTS_MARKER in line:
            continue
        if HOST in line and line.strip().startswith(LISTEN_ADDR):
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if out else "")


def hosts_restore():
    try:
        with open(HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        cleaned = _hosts_strip(content)
        if cleaned != content:
            with open(HOSTS_PATH, "w", encoding="utf-8") as f:
                f.write(cleaned)
            flush_dns()
            log("Hosts file restored.")
        else:
            log("Hosts file already clean.")
    except Exception as e:
        log(f"WARNING: could not restore hosts file automatically: {e}")
        log(f"  Manually remove the line '{LISTEN_ADDR} {HOST}' from {HOSTS_PATH}")


def flush_dns():
    try:
        subprocess.run(["ipconfig", "/flushdns"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# --------------------------------------------------------------------------
# TCP relay with stall-until-release
# --------------------------------------------------------------------------
def hosts_clean_stale():
    """Remove any leftover override from a previous crashed run + flush DNS."""
    try:
        with open(HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return
    cleaned = _hosts_strip(content)
    if cleaned != content:
        try:
            with open(HOSTS_PATH, "w", encoding="utf-8") as f:
                f.write(cleaned)
            flush_dns()
            log("Removed a leftover hosts override from a previous run.")
        except Exception as e:
            log(f"WARNING: found a stale hosts override but could not remove "
                f"it: {e}")


def resolve_real_ip():
    """Resolve the real server IP BEFORE the hosts override is installed."""
    ip = socket.gethostbyname(HOST)
    if ip.startswith("127."):
        # A stale override survived cleanup (DNS cache, or a hand-added line).
        raise RuntimeError(
            f"{HOST} still resolves to {ip} after cleanup. Run "
            f"'Run PICO Hand Tracking Fix.bat --restore' once, then retry.")
    return ip


def pump(src, dst):
    try:
        while not stop_event.is_set():
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass


def handle_client(client, real_ip, max_hold):
    """Accept the client, stall until release, then relay to the real server.

    While stalling we DRAIN the client's initial bytes (the TLS ClientHello)
    into a buffer. That matters for two reasons: (1) a socket closed with
    unread data sends RST, which the app logs as ECONNRESET and treats as a
    failed fetch; draining avoids that. (2) On release we replay the buffer to
    the real server so the handshake proceeds normally.
    """
    try:
        peer = client.getpeername()
        log(f"Intercepted config fetch from {peer[0]}:{peer[1]} — HOLDING it.")
    except Exception:
        log("Intercepted config fetch — HOLDING it.")

    buf = bytearray()
    client.setblocking(False)
    deadline = time.time() + max_hold
    released = False
    while time.time() < deadline and not stop_event.is_set():
        if release_event.wait(timeout=0.1):
            released = True
            break
        try:
            chunk = client.recv(65536)
            if chunk:
                buf.extend(chunk)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            break
    client.setblocking(True)

    if released:
        log("Releasing held fetch (headset connected first — we should win).")
    elif stop_event.is_set():
        try:
            client.close()
        except Exception:
            pass
        return
    else:
        log(f"Max hold ({max_hold:.0f}s) reached; releasing fetch anyway so "
            f"hand tracking still gets enabled (may lose the race).")

    try:
        upstream = socket.create_connection((real_ip, 443), timeout=15)
    except Exception as e:
        log(f"ERROR connecting to real server {real_ip}:443 — {e}")
        try:
            client.close()
        except Exception:
            pass
        return
    # Replay the buffered ClientHello, then relay both directions. The real
    # server terminates TLS, so the certificate validates normally.
    try:
        if buf:
            upstream.sendall(bytes(buf))
    except Exception as e:
        log(f"ERROR forwarding buffered bytes: {e}")
    threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pump, args=(upstream, client), daemon=True).start()


def relay_server(real_ip, max_hold):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((LISTEN_ADDR, LISTEN_PORT))
    except Exception as e:
        log(f"ERROR binding {LISTEN_ADDR}:{LISTEN_PORT} — {e}")
        log("  Something else is using port 443 (another proxy, IIS, etc.). "
            "Close it and retry.")
        stop_event.set()
        return
    srv.listen(8)
    srv.settimeout(1.0)
    log(f"Relay listening on {LISTEN_ADDR}:{LISTEN_PORT} -> real {real_ip}:443")
    while not stop_event.is_set():
        try:
            client, _ = srv.accept()
        except socket.timeout:
            continue
        except Exception:
            break
        threading.Thread(target=handle_client,
                         args=(client, real_ip, max_hold), daemon=True).start()
    try:
        srv.close()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Log watcher
# --------------------------------------------------------------------------
def newest_logs():
    files = []
    for pattern in LOG_GLOBS:
        files.extend(glob.glob(pattern))
    return files


def log_watcher(release_delay):
    """Tail the PICO Connect logs; release the relay on headset connect."""
    offsets = {}
    # Seek to end of existing logs so we only react to NEW lines.
    for f in newest_logs():
        try:
            offsets[f] = os.path.getsize(f)
        except OSError:
            offsets[f] = 0
    connect_seen = False
    while not stop_event.is_set():
        for f in newest_logs():
            try:
                size = os.path.getsize(f)
            except OSError:
                continue
            last = offsets.get(f, 0)
            if size < last:      # log rotated/truncated
                last = 0
            if size > last:
                try:
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(last)
                        chunk = fh.read()
                    offsets[f] = size
                except OSError:
                    continue
                for line in chunk.splitlines():
                    if not connect_seen and RE_CONNECT.search(line):
                        connect_seen = True
                        log("Log: headset connected — releasing the fetch.")
                        if release_delay > 0:
                            time.sleep(release_delay)
                        release_event.set()
                    elif RE_FETCH.search(line):
                        # The fetch returned. If the headset already connected,
                        # this is the win: the true-push happens right here.
                        if connect_seen:
                            log("Log: config fetch returned AFTER connect — "
                                "WIN. handTracking:true is being pushed now.")
                            success_event.set()
                        else:
                            log("Log: config fetch returned BEFORE connect — "
                                "this session lost the race.")
                            fail_event.set()
                    elif RE_FETCH_ERR.search(line):
                        log("Log: fetch ERRORED (see fix_run notes). The relay "
                            "must be running BEFORE you launch PICO Connect.")
                        fail_event.set()
        time.sleep(0.2)


# --------------------------------------------------------------------------
# settings.json helper
# --------------------------------------------------------------------------
def connect_running():
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True)
        return "PICO Connect.exe" in out.stdout
    except Exception:
        return False


def check_settings():
    import json
    if not os.path.isfile(SETTINGS_PATH):
        log(f"NOTE: {SETTINGS_PATH} not found yet (created on first launch). "
            f"Ensure the in-app Hand Tracking toggle is ON.")
        return
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"NOTE: could not read settings.json ({e}).")
        return
    ht = data.get("game", {}).get("handTracking", None)
    if ht is True:
        log("settings.json: game.handTracking already true. Good.")
        return
    if connect_running():
        log("settings.json: handTracking is not true, and PICO Connect is "
            "running. Close Connect, then enable Hand Tracking in-app or "
            "re-run this script.")
        return
    data.setdefault("game", {})["handTracking"] = True
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        log("settings.json: set game.handTracking = true.")
    except Exception as e:
        log(f"NOTE: could not write settings.json ({e}).")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Force PICO Connect hand-tracking handshake order.")
    ap.add_argument("--hold", type=float, default=15.0,
                    help="Max seconds to stall the fetch if the headset "
                         "connect is never seen in the log (default 15). "
                         "Keep this SHORT: PICO Connect's own HTTP client "
                         "times out a stalled fetch, so a long hold fails. "
                         "Pre-arm the headset instead of raising this.")
    ap.add_argument("--release-delay", type=float, default=0.5,
                    help="Seconds to wait after seeing headset connect before "
                         "releasing the fetch (default 0.5).")
    ap.add_argument("--restore", action="store_true",
                    help="Emergency: just remove the hosts override and exit.")
    args = ap.parse_args()

    if os.name != "nt":
        log("This script is Windows-only.")
        sys.exit(1)

    if not is_admin():
        relaunch_as_admin()   # exits

    if args.restore:
        hosts_restore()
        return

    if connect_running():
        log("WARNING: PICO Connect is currently running.")
        log("  Close it fully (check the system tray) before continuing, or "
            "the fetch may already be done. Waiting 8s...")
        time.sleep(8)

    check_settings()

    # Self-heal: clear any override a previous crashed run may have left,
    # so resolving the real IP below doesn't hit our own 127.0.0.1 entry.
    hosts_clean_stale()

    try:
        real_ip = resolve_real_ip()
        log(f"Resolved real {HOST} -> {real_ip}")
    except Exception as e:
        log(f"ERROR: could not resolve {HOST}: {e}")
        sys.exit(1)

    try:
        hosts_add()
        threading.Thread(target=relay_server,
                         args=(real_ip, args.hold), daemon=True).start()
        threading.Thread(target=log_watcher,
                         args=(args.release_delay,), daemon=True).start()
        time.sleep(0.5)
        if stop_event.is_set():
            return

        print()
        log("=" * 62)
        log("ARMED.  Order matters — do this exactly:")
        log("  1. Put the HEADSET on its 'connecting to PC' screen FIRST,")
        log("     and leave it actively searching/retrying.")
        log("  2. THEN launch PICO Connect on this PC.")
        log("The headset must latch on within a couple seconds of launch.")
        log("If it takes ~10s+ to connect, the held fetch will time out —")
        log("pre-arm the headset so the connect is near-instant.")
        log("=" * 62)
        print()

        # Stay armed until we see a decisive outcome in the log. The relay
        # keeps running the whole time (it is what forwards the fetch), so we
        # do NOT tear down on a guess — only on the real fetch-return marker.
        while not stop_event.is_set():
            if success_event.is_set():
                # Give the relay a moment to finish streaming the response
                # before we tear it (and the hosts entry) down.
                time.sleep(2.0)
                log("SUCCESS: hand tracking is enabled for this session.")
                log("Set your controllers down and hands appear in SteamVR.")
                break
            if fail_event.is_set():
                log("This attempt did not win the race. To retry (run me "
                    "again, i.e. double-click the .bat), in this order:")
                log("  1. keep this tool running / start it FIRST,")
                log("  2. get the headset actively 'connecting' BEFORE launch,")
                log("  3. then launch PICO Connect.")
                break
            time.sleep(0.3)
    except KeyboardInterrupt:
        log("Interrupted.")
    finally:
        stop_event.set()
        time.sleep(0.3)
        hosts_restore()
        log("Done. Hosts file is clean.")


if __name__ == "__main__":
    main()
