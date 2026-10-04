# =============================================================================
# M5Cardputer v1.1 — Audio Record & Playback (SD Card Variant)
# Platform : MicroPython / UIFlow2 (AIFlow by M5Stack)
#
# Cara kerja:
#   - BtnA Click  → Rekam audio dari mic built-in, simpan ke /sd/rec.wav
#   - BtnA Hold   → Putar file WAV yang terakhir direkam via speaker built-in
#
# Catatan:
#   - Rekaman disimpan ke SD card (slot 3) — perlu SD card terpasang
#   - Seluruh audio di-buffer ke RAM terlebih dahulu, lalu di-write ke SD
#     dalam satu operasi (bulk write) untuk menghindari bug VFS MicroPython
#   - Gain hardware (magnification=8) digunakan, bukan software gain
#   - Durasi rekaman default 3 detik (bisa diubah via DURATION)
#
# Persyaratan:
#   - microSD card di slot 3 (SCK=40, MISO=39, MOSI=14, CS=12)
#   - RAM bebas cukup untuk buffer audio: DURATION * 8000 * 2 byte
# =============================================================================

import os, sys, io
import gc
import time
import machine
import struct
import M5
from M5 import *
from hardware import MatrixKeyboard
from hardware import sdcard

gc.collect()

# --- KONSTANTA GLOBAL ---
kb = None
RATE = 8000                    # Sample rate mic: 8000 Hz (8 kHz)
DURATION = 3                   # Durasi rekaman dalam detik (ubah sesuai kebutuhan)
TOTAL_TARGET_BYTES = RATE * 2 * DURATION  # Total byte audio: 8000 * 2 byte * 3 = 48.000 bytes (per detik × durasi)

WAV_FILE = "/sd/rec.wav"       # File WAV yang ditulis ke SD card

# --- ALOKASI RAM BESAR DI AWAL SCRIPT ---
# Mengalokasikan buffer audio (DURATION * 16.000 byte) saat memori ESP32-S3
# masih sangat lega. Ini menghindari fragmentasi RAM yang bisa menyebabkan
# alokasi besar gagal di tengah-tengah program.
# Contoh: DURATION=3 → 48 KB buffer, DURATION=15 → 240 KB buffer
try:
    audio_buf = bytearray(TOTAL_TARGET_BYTES)
    audio_mv = memoryview(audio_buf)
except MemoryError:
    print("MemoryError: RAM Penuh! Turunkan DURATION.")
    sys.exit()

CHUNK_SIZE = 512               # Ukuran buffer per iterasi rekam dari mic (bytes)
chunk_buf = bytearray(CHUNK_SIZE)

has_audio = False              # Flag: apakah sudah ada data audio yang valid

# Menampilkan teks di layar 240x135 Cardputer.
# font size 2, posisi cursor di (20,20), warna putih, latar hitam.
def display(data):
    global kb
    M5.Display.setTextSize(2)
    M5.Display.fillScreen(0x000000)
    M5.Display.setCursor(20, 20)
    M5.Display.print(data, 0xffffff)


# Cek apakah SD card sudah mounted. Jika belum, mount dengan slot=3.
# Parameter pin: SCK=40, MISO=39, MOSI=14, CS=12 (layout M5Cardputer v1.1).
# freq=1000000 (1 MHz) — turunkan ke 400000 jika SD card lambat/murah.
# Return True jika /sd accessible, False jika mount gagal.
def safe_mount_sd():
    try:
        os.listdir('/sd')
        return True
    except OSError:
        try:
            # Frekuensi bisa diturunkan ke 400000 jika SD Card murahan/lambat
            sdcard.SDCard(slot=3, width=1, sck=40, miso=39, mosi=14, cs=12, freq=1000000)
            time.sleep(0.5)
            os.listdir('/sd')
            return True
        except Exception as e:
            print(f"SD Mount Error: {e}")
            return False


# Melepaskan speaker agar pin I2S data (pin 46) bebas dari mode output
# speaker dan bisa di-park-kan sebelum mic di-inisialisasi.
def speaker_release():
    try: Speaker.stop()
    except: pass
    try: Speaker.end()
    except: pass


# Park-kan pin 46 (mic data / I2S) agar tidak menginterferen dengan speaker.
# Pin di-toggle OUT→0→1→IN dengan delay kecil untuk reset driver hardware.
def park_mic_pin():
    try:
        p = machine.Pin(46, machine.Pin.OUT)
        p.value(0)
        time.sleep_ms(20)
        p.value(1)
        time.sleep_ms(20)
        p.init(machine.Pin.IN)
        time.sleep_ms(20)
        return
    except: pass
    try:
        p = machine.Pin(46, machine.Pin.IN, machine.Pin.PULL_DOWN)
        time.sleep_ms(20)
        p.init(machine.Pin.IN)
        time.sleep_ms(20)
    except: pass


# Inisialisasi hardware microphone I2S pada M5Cardputer.
# Berbeda dari flash variant, di sini menggunakan magnification=8
# (gain hardware) sehingga tidak perlu loop software gain per-sample.
#
# Langkah:
#   1. End mic (jika sebelumnya masih aktif)
#   2. Rilis speaker (pakai pin yang sama)
#   3. Park-kan pin 46
#   4. Set mic config: pin_data_in=46, sample_rate 8000, mono, magnification=8
#   5. Mulai mic. Return True jika sukses.
def init_mic_hardware():
    try: Mic.end()
    except: pass
    speaker_release()
    park_mic_pin()
    try:
        # Menggunakan magnification=8 (Gain Hardware)
        Mic.config(pin_data_in=46, sample_rate=RATE, stereo=False,
                   over_sampling=1, magnification=8,
                   noise_filter_level=0, use_adc=False)
    except:
        try:
            Mic.config(sample_rate=RATE, over_sampling=1, magnification=8)
        except: pass
    try:
        return Mic.begin()
    except:
        return False


# Membuat WAV header (44 byte) menggunakan struct.pack.
# Format string:
#   '<4sI4s4sIHHIIHH4sI'
#     RIFF + size + WAVE + fmt + (format, channels, sample_rate,
#     byte_rate, block_align, bits) + data + data_size
def get_wav_header(sample_rate, data_size):
    return struct.pack('<4sI4s4sIHHIIHH4sI',
                       b'RIFF', 36 + data_size, b'WAVE', b'fmt ',
                       16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
                       b'data', data_size)

# =============================================================================
# EVENT: BtnA DITEKAN (CLICKED) → Mulai proses rekaman audio
#
# Alur (2 tahap):
#   TAHAP 1: Rekam seluruh audio ke RAM buffer (cepat, tanpa aktivitas SD)
#   TAHAP 2: Write header + seluruh data audio ke SD card dalam 1 operasi
#            (bulk write — menghindari bug VFS MicroPython pada write kecil)
# =============================================================================
def btnA_wasClicked_event(state):
    global has_audio, chunk_buf, audio_buf, audio_mv
    display(f'Recording {DURATION}s...')

    try:
        # Pastikan SD card ter-mount
        if not safe_mount_sd():
            display('SD Card Error!')
            time.sleep(2)
            display('Click Rec, Hold Play')
            return

        if not init_mic_hardware():
            display('Mic Init Fail')
            time.sleep(1)
            display('Click Rec, Hold Play')
            return

        # Delay kecil agar mic buffer stabil sebelum mulai merekam
        time.sleep_ms(100)

        written_bytes = 0

        # 1. TAHAP REKAM KE RAM (Sangat Cepat & Tanpa Gangguan SD Card)
        # Mic.record() menulis ke chunk_buf, lalu disalin ke audio_buf
        # via memoryview (copy tanpa overhead string interning).
        while written_bytes < TOTAL_TARGET_BYTES:
            Mic.record(chunk_buf, RATE, False)

            bytes_to_copy = min(CHUNK_SIZE, TOTAL_TARGET_BYTES - written_bytes)
            audio_mv[written_bytes : written_bytes+bytes_to_copy] = chunk_buf[:bytes_to_copy]
            written_bytes += bytes_to_copy

        # Matikan mic setelah rekaman selesai
        Mic.end()
        display('Saving to SD...')

        # 2. TAHAP WRITE KE SD CARD (Satu Kali Tembak)
        # Buka file, tulis header, tulis data bulk, flush, tutup.
        f = open(WAV_FILE, 'wb')

        # Tulis Header (44 byte)
        wav_header = get_wav_header(RATE, TOTAL_TARGET_BYTES)
        f.write(wav_header)

        # Tulis Data Audio (seluruh buffer dalam satu operasi) - Bypass Bug VFS
        f.write(audio_buf)

        # Paksa flush dan tutup dengan aman
        f.flush()
        f.close()

        has_audio = True

        # Verifikasi akhir: tampilkan ukuran file yang ditulis
        final_size = os.stat(WAV_FILE)[6]
        display(f'Done! {final_size//1024}KB')

    except Exception as e:
        has_audio = False
        display('Record Error!')
        print(f"Error: {e}")
        time.sleep(1)
        display('Click Rec, Hold Play')


# =============================================================================
# EVENT: BtnA DITEKAN DAN DIHOLD → Putar file WAV yang terakhir direkam
# =============================================================================
def btnA_wasHold_event(state):
    global has_audio

    # Jika belum ada rekaman, tampilkan pesan dan hentikan
    if not has_audio:
        display('No Audio Data!')
        time.sleep(1)
        display('Click Rec, Hold Play')
        return

    display('Playing...')
    try:
        # Mulai speaker, set volume 100%, putar file WAV dari SD
        Speaker.begin()
        Speaker.setVolumePercentage(100)
        Speaker.playWavFile(WAV_FILE)

        # Tunggu sampai playback selesai
        while Speaker.isPlaying():
            time.sleep(0.05)

        Speaker.end()
    except Exception as e:
        display('Play Error!')
        print(f"Error: {e}")
        time.sleep(1)

    # Kembali ke tampilan idle
    display('Click Rec, Hold Play')

# =============================================================================
# SETUP — Dipanggil satu kali saat awal program berjalan
# =============================================================================
def setup():
    global kb
    M5.begin()
    Widgets.fillScreen(0x000000)

    # Mount SD card (jika belum ter-mount)
    safe_mount_sd()

    # Register callback untuk BtnA:
    #   - CLICKED  → rekam audio
    #   - HOLD     → putar audio
    BtnA.setCallback(type=BtnA.CB_TYPE.WAS_CLICKED, cb=btnA_wasClicked_event)
    BtnA.setCallback(type=BtnA.CB_TYPE.WAS_HOLD, cb=btnA_wasHold_event)

    # Inisialisasi keyboard matriks
    kb = MatrixKeyboard()
    display('Click Rec, Hold Play')


# =============================================================================
# LOOP — Dipanggil terus-menerus di main loop
# =============================================================================
def loop():
    global kb
    M5.update()   # Update semua event M5 (tombol, sensor, dll.)
    kb.tick()     # Tick keyboard agar callback tetap berjalan


# =============================================================================
# ENTRY POINT — Main program
# =============================================================================
if __name__ == '__main__':
    try:
        setup()
        while True:
            loop()
    except (Exception, KeyboardInterrupt) as e:
        try:
            from utility import print_error_msg
            print_error_msg(e)
        except ImportError:
            print("please update to latest firmware")