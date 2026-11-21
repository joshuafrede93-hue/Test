# Asisten PC

Agen AI lokal untuk Windows. Ia membaca jendela yang sedang aktif lewat UI Automation, memutuskan langkah berikutnya memakai model AI yang berjalan di laptop kamu sendiri, lalu menggerakkan mouse dan keyboard. Gratis, tanpa kuota, dan tanpa mengirim data ke internet.

## Pasang (sekali saja)

1. Pasang **Python 3.11 atau 3.12** dari python.org. Centang "Add Python to PATH".
2. Klik dua kali **install.bat**.
3. Pasang **LM Studio** (lmstudio.ai). Di dalamnya, unduh satu model dan muat.
   - Untuk laptop kamu (VRAM 4 GB, RAM 16 GB), mulai dari model instruksi berukuran 3B sampai 7B dengan kuantisasi Q4 (misalnya keluarga Qwen2.5 Instruct). Model 3B muat penuh di GPU dan lebih cepat; 7B lebih pintar tapi lebih lambat.
   - Di LM Studio buka tab **Developer**, nyalakan **Local Server** (alamat bawaan http://localhost:1234).
   - Di pengaturan GPU LM Studio pilih backend **Vulkan** kalau GPU AMD tidak terdeteksi.
4. Klik dua kali **run.bat**, lalu tekan **Tes koneksi model**. Harus muncul nama model.

Kalau memakai Ollama: ubah di `config.json` menjadi `"base_url": "http://localhost:11434/v1"` dan isi `"model"` dengan nama model, misalnya `"qwen2.5:3b"`.

## Cara pakai

Tulis tugas dalam bahasa biasa, misalnya: `buka notepad lalu tulis "rapat jam 10" dan simpan di Desktop sebagai rapat.txt`. Tekan **Jalankan**. Jendela Asisten PC akan mengecil dan muncul kotak status kecil di pojok kanan bawah.

- **F12** atau tombol Stop (ikon kotak) menghentikan agen kapan saja.
- **Perintah suara**: klik ikon mikrofon atau tekan **F9**, ucapkan tugas, lalu berhenti bicara. Teks dikenali lewat layanan pengenalan suara Google (butuh internet) dan langsung dijalankan (ubah `voice.auto_run` ke `false` di config.json bila ingin dicek dulu).
- Saat AI butuh persetujuan atau jawaban, jendela konfirmasi muncul sendiri di paling depan, disertai bunyi, notifikasi Windows, dan kedipan di taskbar.
- Menggerakkan mouse ke pojok kiri atas layar juga menghentikan paksa (pengaman pyautogui).
- Untuk aksi berisiko (hapus, kirim, bayar, install, kolom sandi, tutup paksa, Enter di aplikasi pesan), muncul dialog konfirmasi. Aksi biasa jalan tanpa bertanya.
- Kalau AI butuh informasi, ia bertanya lewat kotak dialog.

## Cara AI belajar dan mengingat

Model lokal tidak dilatih ulang. Yang disimpan adalah memori di folder `data/` (file JSON biasa, bisa kamu buka dan edit):

| Jenis | Isi | Cara terisi |
|---|---|---|
| Pelajaran (`lessons.json`) | Catatan singkat, misalnya letak menu di suatu aplikasi | Otomatis setelah tugas selesai (AI merangkum 1-2 tips), atau manual lewat tombol **Ajari AI** |
| Skill (`skills.json`) | Urutan aksi yang pernah berhasil, memakai nama tombol (bukan koordinat) | Setelah tugas berhasil, kamu ditanya apakah mau menyimpan sebagai skill |
| Fakta (`facts.json`) | Info tentang kamu, misalnya `folder_kerja=D:\Kerja` | Tombol **Fakta tentang saya** |

Pelajaran yang relevan dan fakta otomatis dimasukkan ke prompt setiap langkah. Kalau tugas baru mirip skill yang tersimpan, kamu ditawari menjalankan skill itu dulu: berjalan tanpa AI, jadi cepat. Kalau satu langkah gagal (tombol tidak ditemukan), AI melanjutkan sendiri dari kondisi layar saat itu.

Memori menolak menyimpan teks yang mengandung sandi, PIN, OTP, token, atau deretan angka panjang. Tetap jangan menulis data rahasia di dialog.

## Pengaturan (`config.json`)

- `llm.use_vision`: `true` mengirim screenshot yang diberi nomor pada tiap elemen. Hanya untuk model vision; lebih lambat dan lebih berat di VRAM 4 GB.
- `agent.max_steps`: batas langkah per tugas (bawaan 25).
- `agent.max_elements`: jumlah elemen layar yang dikirim ke model. Turunkan kalau model kecil kebingungan.
- `safety.extra_risky_words`: tambahkan kata yang selalu perlu konfirmasi, misalnya nama tombol khusus di aplikasi kamu.
- `safety.confirm_every_action`: `true` meminta konfirmasi untuk setiap aksi (bagus saat baru mencoba).

## Batasan yang perlu diketahui

- Jendela yang berjalan sebagai Administrator (dan layar UAC) tidak bisa dikendalikan kecuali Asisten PC juga dijalankan sebagai Administrator.
- Aplikasi yang tidak membuka struktur UI-nya (game, kanvas, desktop jarak jauh) tidak terbaca di mode teks. Coba `use_vision: true` dengan model vision.
- Model kecil sering salah di tugas panjang atau bercabang. Mulai dari tugas pendek, simpan yang berhasil sebagai skill.
- Teks di layar bisa berisi instruksi jahat (misalnya di email atau halaman web). Prompt sudah menyuruh AI mengabaikannya dan aksi berisiko tetap minta konfirmasi, tapi tetap awasi saat bekerja dengan konten dari luar.
- Versi ini diuji logikanya (18 tes otomatis), tetapi lapisan Windows (`wa/env_windows.py`) belum pernah dijalankan di Windows sungguhan. Kalau ada error, salin pesan dari jendela hitam (konsol) dan kirim ke Claude untuk diperbaiki.

## Membuat file .exe

Klik dua kali **build_exe.bat** (butuh internet sekali untuk mengunduh PyInstaller). Hasilnya ada di `dist\AsistenPC\AsistenPC.exe`.

- Salin **seluruh folder** `dist\AsistenPC`, bukan hanya file .exe-nya. Di dalamnya ada `config.json` dan folder `data\` (memori) yang harus berada di samping .exe.
- Windows SmartScreen kemungkinan menampilkan "Windows protected your PC" karena program belum ditandatangani. Klik **More info**, lalu **Run anyway**.
- Antivirus kadang menandai program yang menggerakkan mouse dan keyboard. Kalau terblokir, tambahkan folder itu ke pengecualian.
- Program .exe tidak punya jendela konsol. Kalau ada error, lihat `data\log.txt` di folder .exe.
- LM Studio (atau Ollama) tetap harus terpasang dan menyala; model AI tidak ikut terbungkus di dalam .exe.

### Alternatif tanpa menjalankan file .bat: bangun .exe lewat GitHub

1. Buat akun di github.com, lalu buat repositori baru (pilih Private).
2. Unggah semua isi folder AsistenPC ke repositori itu (tombol Add file, lalu Upload files).
3. Di repositori, pilih Add file, Create new file. Ketik nama `.github/workflows/build-exe.yml`, tempel isi file `build-exe.yml` dari folder ini, lalu Commit.
4. Buka tab Actions, pilih "Build Windows exe", lalu Run workflow. Tunggu sekitar 5 menit sampai hijau.
5. Buka hasil run itu dan unduh artifact `AsistenPC-windows` (berisi AsistenPC.exe). Ekstrak, lalu jalankan.

## Mode konsol (untuk mencari penyebab error)

```
.venv\Scripts\activate
python main.py --cli "buka notepad"
```

## Struktur

- `wa/core.py`: loop agen, prompt, rekam dan putar ulang skill
- `wa/env_windows.py`: baca elemen UI (UI Automation) dan jalankan aksi
- `wa/safety.py`: aturan konfirmasi aksi berisiko
- `wa/memory.py`: pelajaran, skill, fakta
- `wa/llm.py`: klien server model lokal
- `wa/gui.py`: jendela aplikasi
- `tests/`: tes otomatis (`python -m unittest discover -s tests`)

## Setup otomatis LM Studio (sejak 0.2.0, penuh otomatis sejak 0.2.2)

Saat aplikasi dibuka dan model AI belum siap, aplikasi menyiapkannya sendiri tanpa bertanya lagi:

1. memasang LM Studio lewat winget (cadangan: installer resmi) bila belum ada, lalu membukanya sekali agar perintah `lms` siap;
2. memilih model sesuai RAM (16 GB: Qwen3 14B Q4, 12 GB: Qwen3 8B, 8 GB: Qwen3 4B) dan mengunduhnya langsung dari Hugging Face ke folder model LM Studio, dengan persentase di log dan bisa dilanjutkan bila putus;
3. memuat model (konteks 8192), menyalakan server di `http://localhost:1234`;
4. mengetes jawaban model, lalu menulis `base_url`, `max_tokens` (min 1500) dan `timeout` (min 600) ke `config.json`.

Tombol **Setup otomatis** menjalankan hal yang sama kapan saja. Butuh ruang disk bebas sekitar 11 GB dan internet.
