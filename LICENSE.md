# Lisensi

## Lisensi proyek
Hak Cipta © 2026 pemilik proyek HRIS & Company Profile Wadimor. **Seluruh hak dilindungi (proprietary).**
Kode, templat, basis data contoh, dan dokumen di repositori ini hanya boleh dipakai, disalin, atau didistribusikan oleh pemilik proyek dan pihak yang diberi izin tertulis olehnya.
Ganti nama pemegang hak di atas dengan nama badan hukum perusahaan Anda sebelum dipublikasikan; bila ingin membuka sumber, ganti seluruh bagian ini dengan lisensi pilihan Anda (mis. MIT/Apache-2.0).
Merek, logo, dan slogan **Wadimor** adalah milik pemegang mereknya dan tidak termasuk dalam lisensi ini (lihat `docs/VISION.md`, Audit HAKI).

# Pemberitahuan pihak ketiga

## Font
Aplikasi dan situs **tidak menyertakan berkas font** dan tidak memuat font dari layanan daring (tanpa Google Fonts/CDN). CSS hanya menyebut nama font yang sudah ada di perangkat pengunjung:

| Pemakaian | Tumpukan font (urutan cadangan) | Lisensi |
|-----------|---------------------------------|---------|
| Judul situs publik | Palatino Linotype, Palatino, Book Antiqua, Georgia, serif | Font sistem Microsoft/Apple; lisensinya menyertai sistem operasi pengguna (tidak didistribusikan oleh proyek ini) |
| Isi teks | system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif | Font sistem; Roboto berlisensi Apache-2.0 bila terpasang di perangkat |
| PDF (slip, surat, kop) | Helvetica dan Times-Roman bawaan ReportLab (font standar PDF) | Tidak ada berkas font yang disertakan |
| Logo Wadimor | Berupa gambar (PNG); huruf pada logo adalah bagian dari merek | Hak milik pemegang merek, lihat `docs/VISION.md` (Audit HAKI) |

Bila kelak font web disertakan, pakai font berlisensi bebas (mis. SIL OFL), simpan berkas lisensinya di repo, dan catat di tabel ini.

## Feather Icons (ikon antarmuka internal)
Sebagian ikon sprite di `apps/core/templates/_icons.html` (mis. home, users, user, clipboard, calendar, briefcase, bell, clock, search, file, chat) berbentuk sama dengan set **Feather Icons** (https://feathericons.com).
Lisensi MIT mewajibkan pemberitahuan di bawah ini disertakan:

```
The MIT License (MIT)

Copyright (c) 2013-2023 Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Pustaka Python
Django (BSD-3-Clause), psycopg (LGPL-3.0; dipakai sebagai pustaka terpasang, tidak dimodifikasi), django-environ, argon2-cffi, gunicorn, whitenoise, openpyxl, redis (MIT), reportlab (BSD), xlrd (BSD), cryptography (Apache-2.0 / BSD-3-Clause), pyzk (lihat `docs/VISION.md` bagian HAKI).
Daftar lengkap versi: `requirements.txt`.
