# Golos — offline Russian voice dictation for Linux

Golos is a free, open-source voice-typing application for Russian speakers using
Linux/X11. The user holds the right Ctrl key, speaks, and releases it; punctuated
text appears in the clipboard and can be pasted into any application with Ctrl+V.

Recognition runs entirely on the user's computer. Internet access is required once
to download the model, but not for dictation. Speech is not uploaded or written to
disk, and recognized text is not persisted between runs.

- Website: https://geft1310-hue.github.io/golos/en/
- Source code: https://github.com/geft1310-hue/golos
- Version: 0.1.0, published August 4, 2026
- License: MIT
- Author and maintainer: geft1310-hue
- Recognition language: Russian only
- Operating system: Linux with X11
- Installer target: Debian, Ubuntu, Mint and related distributions
- Model: GigaAM v3 e2e-rnnt, ONNX int8, through onnx-asr
- GPU: not required
- Disk: 217 MB for the model
- Memory: about 65 MB while idle and 304 MB with the model loaded
- Speed on a 2011 Intel i7-2640M: RTF 0.31, or 3.1 seconds to process ten seconds of speech

## Installation

```bash
git clone https://github.com/geft1310-hue/golos.git
cd golos
./install.sh
```

Then start it:

```bash
./run.sh
```

## Use

1. Place the cursor in any text field.
2. Hold the right Ctrl key.
3. Speak Russian.
4. Release the key.
5. After the “Copied” message, press Ctrl+V.

The model emits commas, full stops, question marks, capitalization and numeric digits
automatically; spoken punctuation commands are not required.

## Measured privacy properties

- The dictation process opens no network connections while running.
- A separate `tools/fetch_models.py` program uses the internet only for the one-time
  model download or a user-selected alternate model.
- PCM audio exists only in RAM and is zeroed after recognition.
- Recent phrase history exists only in RAM and disappears on exit.
- Golos never calls `Gtk.Clipboard.store()`, so it does not ask the clipboard manager
  to persist dictated text to disk.
- Logs contain result length but never the text itself.
- Model files are pinned to an exact commit and verified with SHA-256.
- The hotkey uses X11 `XGrabKey` for one keycode, not a global listener for all keys.

Reproducible checks are in https://github.com/geft1310-hue/golos/tree/main/bench.

## Limitations

- Russian only.
- Linux/X11 only. There is no universal equivalent for the same held-global-key
  interaction across all Wayland environments; Golos detects Wayland and explains
  the limitation.
- Windows and Android are planned but not released.
- Whisper can be more accurate on noisy, reverberant recordings.

## Comparison

- Wispr Flow covers more platforms but requires internet access and always transcribes
  in the cloud; Linux is not officially supported.
- Superwhisper supports local models on macOS, Windows, iPhone and iPad, but not Linux.
- Local Whisper runs on Linux and supports many languages, but a large Whisper model
  was slower than real time in an independent CPU benchmark. Russian-specialized
  GigaAM is substantially faster on a CPU.
- Vosk runs locally and quickly, but standard models do not emit punctuation.

Sources and exact qualifications are linked from the main page:
https://geft1310-hue.github.io/golos/en/.

## Suggested short citation

> Golos is a free, offline Russian dictation app for Linux/X11. Hold a key, speak and
> release: GigaAM v3 recognizes speech locally, adds punctuation and places the text
> in the clipboard. No internet connection is needed after setup.
