# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment and commands

Golos is a Python 3.11+ GTK 3 desktop application for Linux/X11. It is run directly from the repository; `pyproject.toml` configures Ruff but does not define a buildable/installable Python package.

The complete end-user setup is idempotent and creates a venv with access to system GTK/Xlib packages, installs the locked Python dependencies, downloads the default model, and optionally creates an autostart entry:

```bash
./install.sh
./run.sh                       # execs .venv/bin/python -m golos
```

For a development/CI-style environment, use the same split between system and Python packages as CI:

```bash
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  python3-venv python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1 python3-xlib libportaudio2 xvfb
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install ruff==0.16.1
```

Run commands from the repository root. The CI checks are separate commands rather than one test target:

```bash
.venv/bin/ruff check .
.venv/bin/python -m compileall -q golos tools bench
.venv/bin/python bench/test_platform.py
.venv/bin/python tools/check_site.py
bash -n install.sh && bash -n run.sh

xvfb-run -a .venv/bin/python -c '
import golos.__main__, golos.asr, golos.audio, golos.clipboard
import golos.config, golos.hotkey, golos.platform_check
import golos.ui.overlay, golos.ui.settings, golos.ui.tray
print("все модули импортируются")
'
```

There is no pytest suite or test discovery. Each file in `bench/` is an executable test, diagnostic, or benchmark with its own `main()`. Running one file is the single-test workflow:

```bash
.venv/bin/python bench/test_platform.py                    # headless and CI-safe
.venv/bin/python bench/test_asr.py /path/to/speech.wav    # needs a downloaded model
.venv/bin/python bench/test_audio.py 4                    # needs a microphone
.venv/bin/python bench/benchmark.py /path/to/speech.wav --models gigaam-v3-e2e-rnnt
```

`test_audio.py`, `test_hotkey.py`, `test_grab_exposure.py`, and `test_overlay.py` exercise real hardware/X11 behavior. ASR, memory, privacy, and benchmark scripts require local model weights; speech samples are deliberately untracked. CI intentionally runs only the deterministic checks that do not download the 217 MB model.

## Architecture

- `golos/__main__.py` is the composition root and application state machine (`idle` → `recording` → `transcribing`). It wires the hotkey, microphone, recognizer, clipboard, overlay, settings, and tray, then owns the GTK main loop.
- GTK stays exclusively on the main thread. X11 hotkey handling, PortAudio callbacks, model preload, and inference run in background threads; every UI transition from those threads must be marshalled through `GLib.idle_add`. Model loading starts at the same time as recording so disk load is hidden while the user speaks.
- `hotkey.py` uses a dedicated Xlib connection and `XGrabKey` for exactly one key. It guards against X11 autorepeat and supports physically ungrabbing the key when paused. `platform_check.py` rejects Wayland/headless startup before the app is constructed.
- `audio.py` opens the PortAudio stream only while the hotkey is held. Its realtime callback only copies blocks and computes RMS; FFT/equalizer work happens later on the UI timer. `asr.py` lazily loads ONNX Runtime, splits recordings longer than the model limit at pauses, runs chunks sequentially, and unloads/`malloc_trim`s after idle time.
- `clipboard.py` owns the GTK clipboard and an in-memory history. `ui/overlay.py` is the non-focus-stealing recording/status window; `ui/tray.py` and `ui/settings.py` provide the persistent controls. User configuration is the `Config` dataclass in `config.py`, persisted atomically to `~/.config/golos/config.json`; models live under `~/.local/share/golos/models/`.
- Model download is a separate process, `tools/fetch_models.py`, not part of the dictation runtime. It selects only the needed ONNX files, pins the current Hugging Face commit, writes SHA-256 hashes to `manifest.json`, and hardens permissions. Startup verifies that manifest in a background thread.
- `docs/` is the bilingual GitHub Pages site. `tools/check_site.py` validates its HTML/JSON-LD, language links, sitemap, `llms*.txt`, Markdown overviews, local links, and social image metadata.

## Repository-specific invariants

- Preserve the privacy boundary: the running dictation process has no networking code; only the separate model downloader may use the network. Never write captured audio or recognized text to disk, never log transcript/event contents, and continue zeroing PCM buffers after use.
- Do not call `Gtk.Clipboard.store()`. Clipboard history must remain memory-only and be wiped at exit; timed clearing must first verify that the clipboard still contains Golos's own text.
- Keep the first filtering action in `HotkeyListener._handle` rejecting non-key events and foreign keycodes. During an active X11 grab, foreign keystrokes can reach this process, so they must not be parsed, retained, or logged.
- Preserve lazy imports for heavyweight/audio/network-related libraries where they keep startup light or enforce the downloader/runtime separation. Ruff permits deliberate local imports in `bench/` and `tools/`; application-local imports carry explicit `# noqa: PLC0415` markers.
- The default model is duplicated intentionally across `Config.model`, `install.sh`, and `tools/fetch_models.py`; model choices and download sizes are exposed in `golos/ui/settings.py`. Keep these locations aligned when changing defaults or variants.
- Never commit speech recordings or model weights (`*.wav`, `*.onnx`, `*.onnx_data`, and other audio formats). CI enforces this because recordings contain a real person's voice and weights are large external artifacts.
- Code, comments, logs, and UI text are predominantly Russian. Match that language and the existing explanatory comment style. Ruff targets Python 3.11 with a 100-character line length.
