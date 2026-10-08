# Aturan Pengaturan Jadwal Shift 2026

## 1. Jam Kerja

| Kode Shift | Jam Kerja | Keterangan |
|---|---|---|
| Pagi | 06:00–14:00 | Shift pagi |
| Siang | 14:00–22:00 | Shift siang |
| Malam | 22:00–06:00 | Shift malam |
| GS | 08:00–12:00 / 08:00–14:00 / 08:00–16:00 | General Shift |

> **Catatan GS:** pembagian GS dapat menggunakan beberapa variasi jam kerja sesuai kebutuhan operasional.

---

## 2. Dua Pola Shift

Berdasarkan jadwal pada gambar, terdapat **dua pola shift yang berbeda**:

1. **Pola 2 shift**: Pagi dan Siang.
2. **Pola 3 shift**: Pagi, Siang, dan Malam.

Kedua pola menggunakan rotasi kelompok yang berbeda secara sistem, walaupun nama kelompok pada tabel dasarnya sama. Agar tidak terjadi benturan data, kelompok pada pola 3 shift diberi akhiran `_pack`.

### 2.1 Pola 2 Shift

Pola 2 shift menggunakan kelompok asli:

```text
A, B, C, D, E, F, G
```

Setiap hari:

- Pagi = 3 kelompok
- Siang = 3 kelompok
- Libur = 1 kelompok
- Tidak ada shift Malam

### 2.2 Pola 3 Shift / PACK

Pola 3 shift menggunakan kelompok yang sama secara pola, tetapi diberi suffix `_pack`:

| Kelompok dasar | Kode 3 shift/PACK |
|---|---|
| A | `A_pack` |
| B | `B_pack` |
| C | `C_pack` |
| D | `D_pack` |
| E | `E_pack` |
| F | `F_pack` |
| G | `G_pack` |

Contoh:

```text
A       = kelompok pola 2 shift
A_pack  = kelompok pola 3 shift/PACK
```

Keduanya **bukan kelompok yang sama**.

> Jika ditemukan kelompok `A`, `B`, atau `C` pada jadwal 3 shift, kode yang disimpan di sistem harus menjadi `A_pack`, `B_pack`, dan `C_pack`. Aturan yang sama berlaku untuk `D–G` karena tabel 3 shift pada gambar juga menggunakan kelompok tersebut.

### 2.3 Ringkasan

| Pola | Pagi | Siang | Malam | Libur | Kode kelompok |
|---|---:|---:|---:|---:|---|
| 2 Shift | 3 kelompok | 3 kelompok | - | 1 kelompok | `A–G` |
| 3 Shift / PACK | 2 kelompok | 2 kelompok | 2 kelompok | 1 kelompok | `A_pack–G_pack` |

---

## 3. Jadwal 3 Shift / PACK

Jadwal ini mengikuti baris **3 shift** pada gambar. Karena merupakan pola 3 shift, semua kode kelompok menggunakan suffix `_pack`.

| Hari | Pagi | Siang | Malam | Libur |
|---|---|---|---|---|
| Senin | C_pack + D_pack | E_pack + F_pack | G_pack + A_pack | B_pack |
| Selasa | B_pack + C_pack | D_pack + E_pack | F_pack + G_pack | A_pack |
| Rabu | A_pack + B_pack | C_pack + D_pack | E_pack + F_pack | G_pack |
| Kamis | G_pack + A_pack | B_pack + C_pack | D_pack + E_pack | F_pack |
| Jumat | F_pack + G_pack | A_pack + B_pack | C_pack + D_pack | E_pack |
| Sabtu | E_pack + F_pack | G_pack + A_pack | B_pack + C_pack | D_pack |
| Minggu | D_pack + E_pack | F_pack + G_pack | A_pack + B_pack | C_pack |

### Pola 3 Shift

```text
Pagi  : 2 kelompok
Siang : 2 kelompok
Malam : 2 kelompok
Libur : 1 kelompok
```

---

## 4. Jadwal 2 Shift

Jadwal ini mengikuti baris **2 shift** pada gambar. Kode kelompok tetap menggunakan `A–G`, tanpa suffix `_pack`.

| Hari | Pagi | Siang | Libur |
|---|---|---|---|
| Senin | C + D + E | F + G + A | B |
| Selasa | B + C + D | E + F + G | A |
| Rabu | A + B + C | D + E + F | G |
| Kamis | G + A + B | C + D + E | F |
| Jumat | F + G + A | B + C + D | E |
| Sabtu | E + F + G | A + B + C | D |
| Minggu | D + E + F | G + A + B | C |

### Pola 2 Shift

```text
Pagi  : 3 kelompok
Siang : 3 kelompok
Malam : tidak digunakan
Libur : 1 kelompok
```

---

## 5. Aturan Rotasi

1. Pola 2 shift dan pola 3 shift/PACK harus diperlakukan sebagai **dua pola jadwal yang berbeda**.
2. Pola 2 shift menggunakan kelompok `A–G`.
3. Pola 3 shift menggunakan kelompok `A_pack–G_pack`.
4. `A` berbeda dengan `A_pack`, `B` berbeda dengan `B_pack`, dan seterusnya.
5. Pada pola 2 shift, setiap hari terdapat 3 kelompok Pagi, 3 kelompok Siang, dan 1 kelompok Libur.
6. Pada pola 3 shift, setiap hari terdapat 2 kelompok Pagi, 2 kelompok Siang, 2 kelompok Malam, dan 1 kelompok Libur.
7. Tidak boleh ada kelompok yang mendapatkan dua shift yang waktunya bertabrakan.
8. Pergantian shift harus memperhatikan urutan waktu:
   - Pagi → Siang → Malam
   - Hindari perpindahan langsung **Malam → Pagi** jika masih dapat dihindari.
9. Rotasi harus mengikuti tabel jadwal yang telah ditetapkan.
10. Jika anggota kelompok berubah, perubahan anggota tidak boleh mengubah kode kelompok atau pola rotasi.
11. GS merupakan pengaturan tambahan dan tidak menggantikan pola 2 shift maupun 3 shift.

---

## 6. Aturan GS

### Variasi Jam GS

| Kode GS | Masuk | Pulang | Keterangan |
|---|---:|---:|---|
| GS-12 | 08:00 | 12:00 | GS pulang pukul 12:00 |
| GS-14 | 08:00 | 14:00 | GS pulang pukul 14:00 |
| GS-16 | 08:00 | 16:00 | GS normal sampai pukul 16:00 |

Sistem harus menyimpan jam aktual setiap personel karena GS dapat memiliki variasi jam pulang.

Contoh:

| Personel | Shift | Masuk | Pulang |
|---|---|---:|---:|
| GS-01 | GS | 08:00 | 12:00 |
| GS-02 | GS | 08:00 | 14:00 |
| GS-03 | GS | 08:00 | 16:00 |

---

## 7. Struktur Data Jadwal

Setiap jadwal minimal memiliki:

```text
Tanggal
Hari
Pola Shift
Kelompok
Shift
Jam Masuk
Jam Pulang
Status
Keterangan
```

### Contoh 2 Shift

```text
Tanggal       : 2026-10-08
Hari          : Kamis
Pola Shift    : 2_SHIFT
Kelompok      : G
Shift         : Pagi
Jam Masuk     : 06:00
Jam Pulang    : 14:00
Status        : KERJA
```

### Contoh 3 Shift / PACK

```text
Tanggal       : 2026-10-08
Hari          : Kamis
Pola Shift    : 3_SHIFT
Kelompok      : G_pack
Shift         : Pagi
Jam Masuk     : 06:00
Jam Pulang    : 14:00
Status        : KERJA
```

---

## 8. Status Jadwal

Gunakan status:

- `KERJA`
- `LIBUR`
- `GS`
- `CUTI`
- `IZIN`
- `SAKIT`
- `OFF`
- `TUKAR SHIFT`

---

## 9. Aturan Tukar Shift

Jika terdapat pertukaran shift:

1. Sistem mencatat personel asal.
2. Sistem mencatat personel pengganti.
3. Jadwal asli tetap tersimpan sebagai histori.
4. Jadwal aktif menggunakan hasil pertukaran.
5. Tidak boleh terjadi satu personel mendapatkan dua shift yang waktunya bertabrakan.
6. Pertukaran harus mencatat:
   - tanggal
   - pola shift
   - shift
   - personel asal
   - personel pengganti
   - alasan
   - waktu pengajuan
   - status persetujuan

---

## 10. Validasi Jadwal

Sistem harus memberikan peringatan apabila:

- Personel mendapat dua shift pada waktu yang sama.
- Personel mendapat shift malam kemudian langsung shift pagi tanpa jeda yang memadai.
- Jumlah personel pada suatu shift kurang dari kebutuhan.
- Kelompok yang seharusnya libur justru mendapat shift.
- Jadwal GS bertabrakan dengan shift utama.
- Jam masuk/pulang tidak sesuai konfigurasi shift.
- Kelompok 3 shift menggunakan kode tanpa suffix `_pack`.
- Kelompok 2 shift justru menggunakan kode `_pack`.
- Ada personel yang belum memiliki jadwal.

---

## 11. Prinsip Utama Sistem

Jadwal harus dibuat berdasarkan **pola shift → kelompok → tanggal → personel**, bukan berdasarkan nama individu terlebih dahulu.

Alurnya:

```text
Pola Shift
   ↓
Kelompok
   ↓
Pola Rotasi
   ↓
Tanggal
   ↓
Personel
   ↓
Jam Masuk / Pulang
   ↓
Validasi Konflik
   ↓
Jadwal Final
```

Dengan struktur tersebut, kelompok 2 shift dan 3 shift/PACK tetap terpisah walaupun menggunakan pola huruf yang sama.
