# =============================================================================
# M5Cardputer v1.1 — Audio Record & Playback (Flash Storage Variant)
# Platform : MicroPython / UIFlow2 (AIFlow by M5Stack)
#
# Cara kerja:
#   - BtnA Click  → Rekam audio dari mic built-in, simpan ke /flash/rec.wav
#   - BtnA Hold   → Putar file WAV yang terakhir direkam via speaker built-in
#
# Catatan:
#   - Rekaman disimpan langsung ke flash internal device (tanpa SD card)
#   - Gain software 8x diterapkan per-sample untuk memperkuat suara mic
#   - Durasi rekaman default 15 detik
# =============================================================================

import os, sys, io
import gc
import time
import machine
import M5
from M5 import *
from hardware import MatrixKeyboard

gc.collect()

# --- KONSTANTA GLOBAL ---
kb = None
RATE = 8000                    # Sample rate mic: 8000 Hz (8 kHz)
DURATION = 15.0                # Durasi rekaman dalam detik
RAW_FILE = "/flash/rec.raw"    # File mentah (tanpa header WAV)
WAV_FILE = "/flash/rec.wav"    # File WAV akhir (header 44 byte + data)

CHUNK_SIZE = 512               # Ukuran buffer per iterasi rekam (bytes)
chunk_buf = bytearray(CHUNK_SIZE)
has_audio = False              # Flag: apakah sudah ada data audio yang valid

# Faktor pengali gain software: mengalikan amplitudo setiap sample 8x
# Digunakan karena mic M5Cardputer cukup kecil sehingga suara raw-nya kecil.
# Nilai > 16x berisiko clipping (distorsi), 8x adalah nilai aman maksimum.
GAIN_MULTIPLIER = 8.0

# Menampilkan teks di layar 240x135 Cardputer.
# font size 2, posisi cursor di (20,20), warna putih, latar hitam.
def display(data):
    global kb
    M5.Display.setTextSize(2)
    M5.Display.fillScreen(0x000000)
    M5.Display.setCursor(20, 20)
    M5.Display.print(data, 0xffffff)


# Melepaskan speaker agar pin I2S data (pin 46) bebas dari mode output
# speaker dan bisa di-park-kan sebelum mic di-inisialisasi.
def speaker_release():
    try:
        Speaker.stop()
    except Exception:
        pass
    try:
        Speaker.end()
    except Exception:
        pass


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
    except Exception:
        pass
    try:
        p = machine.Pin(46, machine.Pin.IN, machine.Pin.PULL_DOWN)
        time.sleep_ms(20)
        p.init(machine.Pin.IN)
        time.sleep_ms(20)
    except Exception:
        pass


# Inisialisasi hardware microphone I2S pada M5Cardputer.
# Langkah:
#   1. End mic (jika sebelumnya masih aktif)
#   2. Rilis speaker (pakai pin yang sama)
#   3. Park-kan pin 46
#   4. Set mic config: pin_data_in=46, sample_rate 8000, mono, magnification=1
#      (gain ditangani secara software, bukan hardware)
#   5. Mulai mic. Return True jika sukses.
def init_mic_hardware():
    try:
        Mic.end()
    except Exception:
        pass
    speaker_release()
    park_mic_pin()
    try:
        Mic.config(pin_data_in=46, sample_rate=RATE, stereo=False,
                   over_sampling=1, magnification=1,
                   noise_filter_level=0, use_adc=False)
    except Exception:
        pass
    try:
        return Mic.begin()
    except Exception:
        return False


# Membuat file WAV (header RIFF 44 byte + data PCM) dari file raw.
# Dipakai karena flash menyimpan data mentah terlebih dahulu,
# baru di-wrap menjadi WAV yang valid agar Speaker.playWavFile() bisa membacanya.
def create_wav_from_raw(raw_path, wav_path, sample_rate):
    # Ambil ukuran file raw
    try:
        file_size = os.stat(raw_path)[6]
    except Exception:
        file_size = 0

    if file_size == 0:
        return

    # Parameter WAV: mono, 16-bit, sample_rate
    channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8   # bytes per detik
    block_align = channels * bits_per_sample // 8               # bytes per frame
    data_size = file_size
    riff_size = 36 + data_size                                   # ukuran RIFF (tanpa 4 byte 'RIFF' dan 4 byte size)

    # Bangun 44-byte WAV header secara manual (little-endian)
    header = bytearray(44)
    header[0:4]  = b'RIFF'
    header[4:8]  = riff_size.to_bytes(4, 'little')
    header[8:12] = b'WAVE'
    header[12:16]= b'fmt '
    header[16:20]= (16).to_bytes(4, 'little')           # ukuran sub-chunk fmt (16 byte)
    header[20:22]= (1).to_bytes(2, 'little')            # audio format: PCM
    header[22:24]= channels.to_bytes(2, 'little')
    header[24:28]= sample_rate.to_bytes(4, 'little')
    header[28:32]= byte_rate.to_bytes(4, 'little')
    header[32:34]= block_align.to_bytes(2, 'little')
    header[34:36]= bits_per_sample.to_bytes(2, 'little')
    header[36:40]= b'data'
    header[40:44]= data_size.to_bytes(4, 'little')

    # Salin header + isi raw file ke WAV file
    with open(wav_path, 'wb') as wf, open(raw_path, 'rb') as rf:
        wf.write(header)
        while True:
            chunk = rf.read(512)
            if not chunk:
                break
            wf.write(chunk)

# =============================================================================
# EVENT: BtnA DITEKAN (CLICKED) → Mulai proses rekaman audio
#
# Alur:
#   1. Tampilkan "Recording..."
#   2. Inisialisasi mic hardware
#   3. Rekam sebanyak DURATION detik ke /flash/rec.raw (chunk 512 byte)
#   4. Terapkan gain software 8x pada setiap sample
#   5. Buat /flash/rec.wav dari file raw (tambah WAV header)
#   6. Tampilkan status selesai
# =============================================================================
def btnA_wasClicked_event(state):
    global has_audio
    display('Recording...')
    try:
        if not init_mic_hardware():
            display('Mic Init Fail')
            time.sleep(1)
            display('Click Rec, Hold Play')
            return

        # Delay kecil agar mic buffer stabil sebelum mulai merekam
        time.sleep_ms(100)

        # Total byte yang perlu direkam: sample_rate * 2 byte (16-bit) * durasi
        total_target_bytes = RATE * 2 * int(DURATION)
        written_bytes = 0

        # Buka file raw untuk write mode
        with open(RAW_FILE, 'wb') as f:
            while written_bytes < total_target_bytes:
                # Ambil satu chunk dari mic (512 byte = 256 sample)
                Mic.record(chunk_buf, RATE, False)

                # --- TERAPKAN GAIN SOFTWARE ---
                # Setiap 2 byte adalah satu sample 16-bit signed little-endian.
                # Loop per-sampel: convert unsigned→signed, kalikan GAIN,
                # clamp ke rentang -32768..32767, convert kembali ke unsigned.
                for i in range(0, len(chunk_buf), 2):
                    # Read sample 16-bit little-endian → signed int
                    v = chunk_buf[i] | (chunk_buf[i+1] << 8)
                    if v >= 32768:
                        v -= 65536  # convert ke signed

                    # Kalikan dengan gain
                    v = int(v * GAIN_MULTIPLIER)

                    # Clamp ke range 16-bit signed
                    if v > 32767:
                        v = 32767
                    elif v < -32768:
                        v = -32768

                    # Convert signed → unsigned (untuk penyimpanan ke bytearray)
                    if v < 0:
                        v += 65536

                    # Write 2 byte little-endian
                    chunk_buf[i] = v & 0xFF
                    chunk_buf[i+1] = (v >> 8) & 0xFF

                # --- SELESAI GAIN, tulis chunk ke file raw ---
                f.write(chunk_buf)
                written_bytes += CHUNK_SIZE

        # Matikan mic setelah rekaman selesai
        Mic.end()

        # Buat file WAV valid dari file raw
        create_wav_from_raw(RAW_FILE, WAV_FILE, RATE)

        has_audio = True
        display('Done! Hold to Play')
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
        # Mulai speaker, set volume 100%, putar file WAV
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

    # Register callback untuk BtnA:
    #   - CLICKED  → rekam audio
    #   - HOLD     → putar audio
    BtnA.setCallback(type=BtnA.CB_TYPE.WAS_CLICKED, cb=btnA_wasClicked_event)
    BtnA.setCallback(type=BtnA.CB_TYPE.WAS_HOLD, cb=btnA_wasHold_event)

    # Inisialisasi keyboard matriks (meskipun tidak dipakai,
    # diperlukan agar keyboard driver tidak conflict dengan display)
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