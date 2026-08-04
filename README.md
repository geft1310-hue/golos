# Golos

**Hold a key. Talk. Let go. The text is in your clipboard — with the commas already in it.**

Offline Russian speech-to-text for Linux. Your voice never leaves the machine.
After the one-time model download you can unplug the network cable and it keeps working.

🇷🇺 [Читать по-русски](README.ru.md)

[![Checks](https://github.com/geft1310-hue/golos/actions/workflows/ci.yml/badge.svg)](https://github.com/geft1310-hue/golos/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Scope, up front:** Golos recognises **Russian only**, and runs on **Linux/X11** today.
> Windows and Android are on the roadmap. If you need another language, this is not your tool —
> and no hard feelings.

---

## Why this exists

Dictation that actually feels good is a solved problem — if you are willing to accept all three of these:

| The usual deal | |
|---|---|
| 💸 **Paid, forever** | Wispr Flow, Superwhisper and friends are subscriptions |
| 🍎 **macOS only** | on Linux there is essentially nothing comparable |
| ☁️ **Cloud** | every word you dictate is processed on someone else's computer |

The obvious escape is Whisper, running locally. For Russian, that escape has a catch that
nobody mentions until you try it:

> **Whisper cannot keep up with real time on a CPU.**
> An independent benchmark measured `whisper-large-v3-turbo` at **RTF 1.02** on CPU —
> ten seconds of speech takes *more than ten seconds* to transcribe. That is not dictation.
> That is waiting. ([source](https://habr.com/ru/articles/1042574/))

So you buy a GPU, or you go back to the cloud.

**Golos takes the third option: a model that is actually good at Russian.**
[GigaAM v3](https://github.com/salute-developers/GigaAM) from SberDevices is Russian-specialised
rather than multilingual — which makes it small, fast and unusually accurate on its one language.
In the same independent benchmark it ran at **RTF 0.047 on CPU — over 20× faster than Whisper**,
at comparable accuracy on Russian. The author's conclusion for CPU-only deployments was
GigaAM, not Whisper.

And it punctuates. That matters more than it sounds.

---

## Why Russian speakers should care

If you have ever used the microphone button on an Android keyboard, you know the output:

```
привет как дела я сегодня буду в офисе часов в пять давай встретимся
```

One long breathless line. No commas, no full stops, no capitals, numbers spelled out as words.
You then spend as long fixing it as you would have spent typing it.

Golos gives you this instead:

```
Привет, как дела? Я сегодня буду в офисе часов в 5. Давай встретимся.
```

Punctuation, capitalisation and digits are not a post-processing hack bolted on afterwards —
the model emits them natively, as part of recognition. The vendor's own evaluation puts comma
F1 at **84.5** and full-stop F1 at **86.7**.

---

## Numbers

### Ours — measured, on deliberately bad hardware

Everything below was measured on an **Intel i7-2640M — a 2-core laptop CPU from 2011** with 8 GB
of RAM. Not cherry-picked hardware; the oldest thing we had. The scripts that produced these
numbers are in [`bench/`](bench/) and you can re-run every one of them.

| | |
|---|---|
| Recognition speed | **RTF 0.31** — 10 seconds of speech transcribed in 3.1 s |
| RAM, idle (waiting for the key) | ~65 MB |
| RAM, model loaded | ~304 MB |
| RAM returned to the OS on unload | 238 of 273 MB |
| Cold model load from disk | 1.7–1.9 s |
| Disk | 217 MB, the model |
| Network traffic while running | **zero** |

Two design decisions make the memory numbers real rather than cosmetic. The microphone is
**not open** until you press the key, and the model is **unloaded** after a few idle minutes.
When it needs to come back, loading starts *at the same moment as your recording* — so it
happens while you are still talking, and you never see it.

### Theirs — accuracy, measured by other people

These are **not our measurements.** Two independent sources, both linked:

**Vendor's own evaluation** ([GigaAM, 10 Russian test sets](https://github.com/salute-developers/GigaAM/blob/main/evaluation.md)) — WER %, lower is better:

| | GigaAM v3 e2e-rnnt *(what Golos ships)* | Whisper-large-v3 |
|---|---|---|
| Average over 10 sets | **11.2** | 21.0 |
| Golos Farfield | **5.5** | 16.4 |
| Callcenter | **12.6** | 23.1 |

**Third-party benchmark** ([Habr, 6 public Russian test sets](https://habr.com/ru/articles/1042574/)) — WER %:

| | |
|---|---|
| GigaAM v3-e2e-rnnt | **10.38** |
| `openai/whisper-large-v3-turbo` | 13.25 |

### Where Whisper is better — because it is

The same third-party benchmark is worth reading in full, including the parts that do not
flatter us:

- On **clean, close-mic speech the two models are tied** (6.98 % vs 7.06 % — the gap is noise).
- On **noisy, reverberant recordings Whisper wins, by 6–7 points.** Golos is built for a
  microphone in front of your face, not for a phone on a table in a loud room.
- That author had earlier published a **3.3 %** WER figure for GigaAM, re-measured it on real
  recordings, and publicly revised it. We are citing the corrected numbers, not the flattering ones.

If your audio is bad, use Whisper. If you are dictating into your own microphone on a CPU,
nothing else is close.

---

## Install

Debian, Ubuntu, Mint and relatives:

```bash
git clone https://github.com/geft1310-hue/golos.git
cd golos
./install.sh
```

That is the whole thing. The installer checks what is missing rather than blindly installing,
so it usually does not even need your password. It downloads the 217 MB model **once**, and
offers to start Golos with your session.

```bash
./run.sh
```

A microphone icon appears in your tray. You're done.

> `install.sh` uses `apt`. On other distributions install the equivalents of
> `python3-venv python3-gi python3-gi-cairo gir1.2-ayatanaappindicator3-0.1 python3-xlib libportaudio2`
> with your own package manager, then run `./install.sh` — it will skip straight to the Python parts.

### Using it

1. Put the cursor where you want the text.
2. **Hold Right <kbd>Ctrl</kbd>.** A small window appears: `● Запись`, a live equaliser, a timer.
3. Talk.
4. Let go. `Распознаю…` → `✓ Скопировано`.
5. <kbd>Ctrl</kbd>+<kbd>V</kbd>.

Talk for as long as you like — long speech is split on natural pauses and stitched back
together. The hotkey is configurable, as is the microphone, the idle-unload timeout, and an
optional clipboard auto-wipe.

Settings also offer a **faster, slightly less accurate** variant (`e2e-ctc`, RTF 0.26 against
0.31) and a **fastest one with no punctuation at all**. Picking one downloads it once; the
default is the accurate one and most people should leave it alone.

---

## Privacy: what it sees, and what it never does

Every dictation tool claims to respect your privacy. Here is ours, stated as testable
behaviour — including the uncomfortable part, which is the part nobody else prints.

### What it never does

| | Verified by |
|---|---|
| **Never goes online.** Zero network connections while running; only local sockets to screen, audio and tray | `ss -tunp` against the live process |
| **Never writes your speech to disk.** Audio lives in RAM and the buffer is zeroed right after recognition | [`bench/test_privacy.py`](bench/test_privacy.py) |
| **Never stores recognised text.** The last ~20 phrases stay in RAM and are wiped on exit | disk-wide search for a marker phrase — not found anywhere |
| **Never hands text to the clipboard manager for safekeeping.** X11 lets an app ask the clipboard manager to persist a copy so it survives exit — which would put your dictation on disk. Golos deliberately does not make that request | `Gtk.Clipboard.set_text()` with no `store()` |
| **Never logs your text.** The log records the *length* (`распознано символов: 43`), never the content | — |
| **Never runs downloaded code.** The model ships as ONNX — a description of a computation, not a program. GigaAM's official loader would execute downloaded Python on your machine; this does not | — |

Model files are pinned to an exact commit and checksummed on every start.
`~/.config/golos` and `~/.local/share/golos` are created `0700`.

**The one exception, stated plainly:** the model has to be downloaded once. That download is
performed by a *separate* program — [`tools/fetch_models.py`](tools/fetch_models.py) — run by
the installer, or launched by Golos if you deliberately switch to another recognition variant
in the settings. The dictation process itself contains no networking code and opens no socket,
which is what makes the first row of that table checkable rather than merely promised.

### What it does see — and you should know this

A global hotkey has to be taken from the whole system. Golos takes it the narrowest way X11
offers: it asks for **one specific keycode**, not the keystroke stream. This matters, because
the common shortcut — a `pynput` global listener, used by a lot of similar tools — sees
*everything you type, including passwords.*

But narrow is not the same as isolated, and we measured what actually happens:

> **While the hotkey is held down, other keystrokes are delivered to Golos and do not reach
> the focused window.** In the test, three synthetic keypresses arrived at the grab and
> zero reached the text field.

In practice:

- **While you hold Right Ctrl, you cannot type.** Letters will not appear in your editor.
  This is not a bug, it is how an X11 active grab works. Let go and everything is normal.
- Golos does technically receive those events, so the very first line of the handler
  **discards anything that isn't the configured keycode** — not stored, not parsed, not logged
  at any level, debug included.
- This is true of *every* X11 program with a global hotkey. It is simply not usually written down.

Reproduce it yourself: [`bench/test_grab_exposure.py`](bench/test_grab_exposure.py).

---

## Requirements, and one real limitation

- **Linux with X11.** Python 3.11+, GTK 3, a microphone.
- **Wayland will not work**, and this cannot be fixed from our side.

Wayland deliberately does not let an application listen for a key it does not own — that is a
security feature, not an oversight, and it is the reason global-hotkey tools across the whole
Linux desktop are in the same position. Golos detects Wayland **at startup** and tells you
plainly, instead of launching and then silently never responding to the key.

Ubuntu 22.04+, Fedora and most current GNOME/KDE spins default to Wayland. Log out, click the
gear icon at the login screen, choose the **Xorg** session, log back in. Nothing else changes.

---

## Roadmap

| | |
|---|---|
| 🐧 **Linux (X11)** | working, this repo |
| 🪟 **Windows, with an installer** | in progress |
| 🤖 **Android — as a voice keyboard** | in progress. Replaces the Google mic button in *any* text field, with punctuation |
| 🌊 Wayland | blocked upstream; needs a portal that does not exist yet |

Issues and pull requests welcome. If you tried it and it did not work, that is the most
useful bug report there is — please open it.

---

## Credits

Golos is a thin, careful wrapper around other people's excellent work:

- **[GigaAM v3](https://github.com/salute-developers/GigaAM)** — SberDevices, MIT. The model.
- **[onnx-asr](https://github.com/istupakov/onnx-asr)** — istupakov, MIT. Runs it with numpy and
  onnxruntime alone, no PyTorch — which is why the whole thing fits in 304 MB.

Golos itself is MIT. Take it, fork it, ship it.
