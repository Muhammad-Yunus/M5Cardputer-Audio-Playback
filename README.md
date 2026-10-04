# AUDIO PLAYBACK — M5Stack Cardputer

[![Platform](https://img.shields.io/badge/Platform-MicroPython-4B8BBE.svg)](https://docs.micropython.org/)
[![UIFlow](https://img.shields.io/badge/UIFlow-2-green.svg)](https://aiflow.m5stack.com/)
[![Device](https://img.shields.io/badge/Device-M5Cardputer-00A8E8.svg)](https://docs.m5stack.com/en/device/Cardputer)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

<em>Written by M5Stack AIFlow</em>

An audio record & playback app for the **M5Stack Cardputer** running MicroPython / UIFlow2. It records audio from the built-in microphone, saves it as a WAV file, and plays it back through the built-in speaker. Two implementations are provided:

- **Flash variant** — saves the recording to the device's internal flash storage.
- **SD Card variant** — saves the recording to a microSD card (requires an SD card in slot 3).

---

## Features

- 🎙️ Record audio from the built-in I2S microphone (8 kHz, mono, 16-bit)
- 🔊 Play back the recorded WAV file via the built-in speaker
- 💾 **Flash variant** — writes directly to `/flash/`, no external storage needed
- 💾 **SD variant** — writes to `/sd/` for larger recordings and better performance
- 📈 Software gain multiplier (8.0×) in the flash variant to boost quiet recordings
- ⚙️ Hardware gain (`magnification=8`) in the SD variant
- 🖥️ Simple status display — "Click Rec, Hold Play" / "Recording..." / "Playing..."

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                     main.py                            │
│  ┌──────────────┐  ┌────────────┐  ┌───────────────┐   │
│  │    DISPLAY   │  │   MIC I2S  │  │  STORAGE      │   │
│  │  (M5.Display)│  │  (Pin 46)  │  │ Flash / SD    │   │
│  └──────┬───────┘  └─────┬──────┘  └───────┬───────┘   │
│         │                 │                │           │
│  ┌──────▼─────────────────▼────────────────▼─────────┐ │
│  │            RECORD MODE  /  PLAYBACK MODE          │ │
│  │  ┌────────────────────────┐  ┌──────────────────┐ │ │
│  │  │  BtnA Clicked → Rec    │  │ BtnA Hold → Play │ │ │
│  │  │  (WAV write + gain)    │  │ (playWavFile)    │ │ │
│  │  └────────────────────────┘  └──────────────────┘ │ │
│  └───────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────┘
```

### Two Variants

| Variant | Storage Path | Recording Duration | Memory Strategy |
|---------|--------------|-------------------|-----------------|
| **Flash** (`audio_in_flash`) | `/flash/rec.wav` | 15 seconds | Writes 512-byte chunks directly to flash; 8× software gain applied on-the-fly |
| **SD Card** (`audio_in_sd`) | `/sd/rec.wav` | 3 seconds (configurable via `DURATION`) | Allocates full buffer in RAM at start (up to ~240 KB), records to RAM, then writes to SD in one bulk operation |

Both variants share the same user interface:

| Key | Action |
|-----|--------|
| **BtnA Clicked** | Start a new recording (overwrites previous file) |
| **BtnA Hold** | Play back the last recorded WAV file |

---

## Project Structure

```
M5Cardputer-Audio-Playback/
├── src/
│   ├── audio_in_flash/
│   │   └── main.py        # Flash storage variant (~220 lines)
│   ├── audio_in_sd/
│   │   └── main.py        # SD card storage variant (~215 lines)
│   └── README.md          # This file
```

---

## Hardware Requirements

- **M5Stack Cardputer v1.1** (ESP32-S3 based, 240×135 OLED, built-in microphone & speaker)
- *(SD variant only)* microSD card inserted in **slot 3** (SCK=40, MISO=39, MOSI=14, CS=12)

---

## How to Flash

### Prerequisites

1. Install [UIFlow2 / AIFlow](https://aiflow.m5stack.com/) and connect your Cardputer via USB.
2. In UIFlow2, select **M5Stack-Carputer** as the device and create a **blank project**.

### Step 1 — Choose a variant

Pick the variant that suits your use case:

- **Flash variant** — no SD card needed. Best for short recordings (up to ~15 s) on the device's internal storage.
- **SD Card variant** — requires a microSD card. Better suited for longer recordings or repeated use.

### Step 2 — Flash the main application

1. Switch the UIFlow2 editor to **Code mode** (Python).
2. Open the corresponding `main.py`:
   - `src/audio_in_flash/main.py` for the flash variant
   - `src/audio_in_sd/main.py` for the SD variant
3. Copy the entire file contents into the UIFlow2 editor.
4. Click **Flash** to write the program to the Cardputer.

### Step 3 — First run

After flashing, the Cardputer will:
1. Initialise the display and register BtnA callbacks.
2. Show **"Click Rec, Hold Play"** on the screen.
3. On **BtnA click** → start recording (with a status message showing duration).
4. On **BtnA hold** → play back the recorded WAV file.

> **Note:** The microphone and speaker share pin 46 (I2S data line). The app parks the pin between recording and playback to avoid conflicts.

---

## Using the App

| Action | Effect |
|--------|--------|
| **BtnA Click** | Records `DURATION` seconds of audio and saves the WAV file. The display shows the recording duration and file size on completion. |
| **BtnA Hold** | Plays back the most recently recorded WAV file at 100% volume. |

### Flash variant specific notes

- The raw recording is first written to `/flash/rec.raw`, then a WAV header is prepended to create `/flash/rec.wav`.
- A **software gain multiplier of 8.0×** is applied sample-by-sample to compensate for the quiet built-in microphone.
- Recording duration is fixed at **15 seconds**.

### SD variant specific notes

- The full audio buffer (up to `DURATION` × 8000 × 2 bytes) is allocated in RAM at script start.
- During recording, audio is written to the RAM buffer (no SD activity). After recording, the entire buffer plus WAV header is written to `/sd/rec.wav` in one bulk operation.
- Recording duration is set by the `DURATION` constant (default: 3 seconds, adjustable).
- SD card is mounted at 1 MHz (`freq=1000000`). If your card is slow or cheap, lower `freq` to `400000`.

---

## Configuration

### Flash variant — key constants

```python
RATE = 8000            # Sample rate (Hz)
DURATION = 15.0        # Recording duration (seconds)
RAW_FILE = "/flash/rec.raw"
WAV_FILE = "/flash/rec.wav"
GAIN_MULTIPLIER = 8.0  # Software gain multiplier
```

### SD variant — key constants

```python
RATE = 8000            # Sample rate (Hz)
DURATION = 3           # Recording duration (seconds)
WAV_FILE = "/sd/rec.wav"
# SD card mount:
sdcard.SDCard(slot=3, width=1, sck=40, miso=39, mosi=14, cs=12, freq=1000000)
```

To record longer, increase `DURATION`. Be mindful of available free RAM for the SD variant — each second of 16-bit 8 kHz mono audio is 16 KB.

---

## Troubleshooting

| Symptom | Possible cause |
|---------|----------------|
| "SD Card Error!" | No microSD card in slot 3, or card is not FAT-formatted. Try lowering `freq` to `400000`. |
| "Mic Init Fail" | Another app or previous run may still hold the I2S mic pin. Power-cycle the device. |
| "Record Error!" | Flash storage full (flash variant) or SD card full (SD variant). Delete old WAV files. |
| "No Audio Data!" | Play was attempted before any recording was made. Click BtnA to record first. |
| Recording sounds very quiet | Increase `GAIN_MULTIPLIER` (flash) or `magnification` (SD). Values above 16× may clip. |
| Recording sounds distorted / clipping | Reduce `GAIN_MULTIPLIER` (flash) or `magnification` (SD). |
| SD write is very slow | Lower `freq` parameter from `1000000` to `400000` in `safe_mount_sd()`. |

---

## Technical Notes

- The microphone uses the ESP32-S3 I2S peripheral with `pin_data_in=46` and `use_adc=False` (DMA-based capture).
- Both variants write standard 44-byte WAV headers (RIFF/WAVE, PCM 16-bit, mono, 8 kHz).
- The flash variant's 8× software gain is applied per-sample in Python, which is CPU-intensive but keeps the flash write clean.
- The SD variant's bulk-write approach avoids the MicroPython VFS chunked-write bug that can corrupt files written in many small increments.
- Pin 46 (mic data) is briefly parked (OUT → 0 → 1 → IN) between mic and speaker modes to prevent I2S crosstalk.

---

## License

MIT License. Feel free to fork, modify, and use for your own projects.
