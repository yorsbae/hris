"""Tabel rotasi RESMI — salinan dari 'Aturan Pengaturan Jadwal Shift 2026' (docs/jadwal_shift_2026.md).

Dua pola yang berbeda (kelompok bukan orang yang sama walau hurufnya sama):
  • 2 shift  (Pagi + Siang)          → kode kelompok  A … G         (3 Pagi, 3 Siang, 1 Libur per hari)
  • 3 shift / PACK (Pagi+Siang+Malam) → kode kelompok  A_pack … G_pack (2 Pagi, 2 Siang, 2 Malam, 1 Libur per hari)
Kode shift master: PAGI 06–14, SIANG 14–22, MALAM 22–06; GS 08–16 (GS-14 / GS-12 hanya hari kerja sebelum libur GS).
"""
DAY_NAMES = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
LETTERS = "ABCDEFG"
P2, P3 = "2_SHIFT", "3_SHIFT"

# per hari (Senin…Minggu): (Pagi, Siang, [Malam], Libur) → huruf kelompok
_P3 = [("CD", "EF", "GA", "B"), ("BC", "DE", "FG", "A"), ("AB", "CD", "EF", "G"), ("GA", "BC", "DE", "F"),
       ("FG", "AB", "CD", "E"), ("EF", "GA", "BC", "D"), ("DE", "FG", "AB", "C")]
_P2 = [("CDE", "FGA", "B"), ("BCD", "EFG", "A"), ("ABC", "DEF", "G"), ("GAB", "CDE", "F"),
       ("FGA", "BCD", "E"), ("EFG", "ABC", "D"), ("DEF", "GAB", "C")]
SHIFT_ORDER = {P2: ["PAGI", "SIANG"], P3: ["PAGI", "SIANG", "MALAM"]}


def group_code(letter, pattern):
    """A + 2_SHIFT → 'A'; A + 3_SHIFT → 'A_pack'."""
    return letter if pattern == P2 else f"{letter}_pack"


def pattern_of(code):
    return P3 if str(code).endswith("_pack") else P2


def letter_of(code):
    return str(code)[0]


def official(pattern):
    """{kode_kelompok: [kode_shift atau None (=libur)] × 7 hari}."""
    days = _P2 if pattern == P2 else _P3
    out = {group_code(l, pattern): [None] * 7 for l in LETTERS}
    for wd, parts in enumerate(days):
        *work, off = parts
        for shift_code, letters in zip(SHIFT_ORDER[pattern], work):
            for l in letters: out[group_code(l, pattern)][wd] = shift_code
        assert out[group_code(off, pattern)][wd] is None
    return out


def check(table, pattern):
    """Validasi tabel {kode: [7 sel]} terhadap aturan pola; kembalikan daftar kalimat masalah (kosong = sesuai)."""
    need = {P2: {"PAGI": 3, "SIANG": 3, None: 1}, P3: {"PAGI": 2, "SIANG": 2, "MALAM": 2, None: 1}}[pattern]
    issues = []
    for wd in range(7):
        got = {}
        for cells in table.values(): got[cells[wd]] = got.get(cells[wd], 0) + 1
        for key, n in need.items():
            if got.get(key, 0) != n: issues.append(f"{DAY_NAMES[wd]}: {key or 'Libur'} = {got.get(key, 0)} kelompok (seharusnya {n})")
        extra = set(got) - set(need)
        if extra: issues.append(f"{DAY_NAMES[wd]}: shift {', '.join(sorted(str(x) for x in extra))} tidak dipakai di pola ini")
    return issues
