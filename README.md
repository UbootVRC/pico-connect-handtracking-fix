# PICO Hand Tracking Fix

Fixes hand tracking on the **PICO 4 Enterprise** with **PICO Connect 10.6.6** on Windows.

If your hand tracking used to work but silently stopped working when streaming to PC — controllers work fine, but putting them down never shows your hands in SteamVR — this tool fixes it, without modifying PICO Connect or the headset in any way.

## The root cause

On startup, PICO Connect decides whether hand tracking is enabled based on **two events racing each other**:

1. **The headset connecting** to the PC — this pushes `handTracking: false`.
2. **A remote-config HTTPS fetch** to `setting-global-api.picovr.com` returning — this pushes `handTracking: true`.

Whichever lands **last** wins for the whole session. On a fast internet connection the config fetch almost always returns *before* the headset connects, so `false` is applied last and hand tracking stays off — every session, no matter what the in-app toggle says.

## What this tool does

It guarantees the correct order by briefly holding the config fetch until the headset has connected:

1. Resolves the real IP of `setting-global-api.picovr.com` and remembers it.
2. Temporarily points that hostname at `127.0.0.1` via the Windows hosts file.
3. Runs a local TCP relay on port 443 that **accepts** PICO Connect's HTTPS connection but **stalls** it (no bytes forwarded yet).
4. Tails the PICO Connect logs. The moment the headset connects, it **releases** the held connection, which then completes normally against the real server.

Result: the headset connect (`false`) always lands first, the config fetch (`true`) lands last → **hand tracking ON**.

Notes on safety:

- It is a **raw byte relay** — TLS is *not* intercepted or decrypted. The real PICO server terminates TLS and the certificate validates normally. No CA certificate is installed.
- The hosts-file entry is removed automatically when the tool exits (including on Ctrl+C), and stale entries from a crashed run are cleaned up on the next start.
- Pure Python standard library. No third-party packages, nothing installed system-wide.

## Requirements

- Windows 10/11
- PICO 4 Enterprise + PICO Connect 10.6.6
- Administrator rights (needed to edit the hosts file — the script asks via UAC automatically)
- Internet access for the one-time environment install

## Install

1. Download/clone this repository anywhere.
2. Double-click **`Install Environment.bat`**. It downloads Miniconda, installs it **locally into this folder** (nothing is added to your PATH or registry, and any Python/Anaconda you already have is left untouched), then creates a local environment in `env\`.

If the folder's path contains characters the Miniconda installer can't handle (a space, parentheses like `PicoHandFix (1)`, etc.), the installer says so and automatically puts the environment in `%LOCALAPPDATA%\PicoHandFix` instead — the run script checks both locations, so everything still works.

That's it — everything lives inside this folder. To uninstall, just delete the folder.

## Usage

Order matters. Do this exactly:

1. Make sure PICO Connect is **fully closed** (check the system tray).
2. Double-click **`Run PICO Hand Tracking Fix.bat`**. Accept the UAC prompt.
3. Wait until it prints **`ARMED`**.
4. Put the headset on its *"connecting to PC"* screen **first**, and leave it actively searching.
5. **Then** launch PICO Connect on the PC. The headset should latch on within a couple of seconds.
6. The tool confirms the win (`handTracking:true` pushed after connect), cleans up the hosts file, and exits.
7. Put your controllers down — your hands appear in SteamVR.

The fix applies **per session**: run it each time you start PICO Connect. If a run reports it "lost the race", just close PICO Connect and run it again, making sure the headset is already actively trying to connect *before* you launch PICO Connect.

### Options

Pass arguments straight through the .bat:

```bat
"Run PICO Hand Tracking Fix.bat" --restore      &rem emergency: remove the hosts entry and exit
"Run PICO Hand Tracking Fix.bat" --hold 20      &rem hold the fetch up to 20s (default 15)
```

Keep `--hold` short — PICO Connect's own HTTP client times out a fetch that's stalled too long. Pre-arming the headset (step 4) works better than raising the hold time.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ERROR binding 127.0.0.1:443` | Something else is using port 443 (another proxy, IIS, etc.). Close it and retry. |
| `still resolves to 127.x` | A stale hosts entry survived. Run the .bat once with `--restore`, then retry. |
| "lost the race" | The headset connected too slowly. Get it actively searching *before* launching PICO Connect, then rerun. |
| Fetch ERRORED in the log | The tool must be armed *before* PICO Connect launches. Close Connect, rerun the tool, then launch Connect. |
| Hosts file left dirty after a crash | The next run cleans it automatically, or run with `--restore`, or manually delete the `# PICO-HT-FIX` line from `C:\Windows\System32\drivers\etc\hosts`. |

A timestamped log of each run is written to `fix_run.log` next to the script.

## Files

| File | Purpose |
|---|---|
| `pico_handtracking_fix.py` | The fix itself (Python 3.8+, stdlib only) |
| `Install Environment.bat` | One-time setup: installs Miniconda locally into `miniconda\` and creates a local conda env in `env\` |
| `Run PICO Hand Tracking Fix.bat` | Runs the fix using the local environment |

## Credits

Thanks to **LordChaotix (Crocodile)** for supplying the PICO Connect log files that made it possible to find the startup race and build this fix.

## Disclaimer

This tool edits your Windows hosts file (temporarily, self-cleaning) and briefly delays one HTTPS request made by PICO Connect. It does not decrypt traffic, patch binaries, or touch the headset. Use at your own risk. Not affiliated with PICO/ByteDance. Tested with PICO Connect **10.6.6** and the **PICO 4 Enterprise** — a future PICO Connect update may change the startup logic and make this fix unnecessary (or ineffective).
