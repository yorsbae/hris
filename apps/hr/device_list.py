"""Daftar mesin (menu Mesin): filter, urutan, dan pengelompokan bertingkat (multi group by) — murni logika, tanpa akses DB (menerima baris dari device_status()).

Parameter GET (semua opsional, nilai tak dikenal diabaikan):
  q          cari teks di nama/lokasi/kode/SN/IP/No Mesin/catatan
  protokol   zk | fk | easylink          port   angka            koneksi  Ethernet | WiFi | USB | GPRS
  status     aktif | diam | nonaktif     otomatis  ya | tidak     hasil    ok | gagal | belum  (hasil tarikan terakhir)
  urut       nama | lokasi | ip | port | protokol | no | scan | scan_terakhir | tarikan     arah  naik | turun
  g1 g2 g3   kelompokkan bertingkat menurut: port | protokol | koneksi | lokasi | status | segmen | otomatis   (dimensi yang sama tidak dihitung dua kali)
Mesin tanpa nilai pada kolom urut (mis. belum pernah ada scan) selalu di akhir, baik naik maupun turun."""
from collections import Counter

PROTO_SHORT = {"zk": "ZK (TCP)", "fk": "FK (FKAttend.dll)", "easylink": "EasyLink (HTTP)"}
STATUSES = (("aktif", "Aktif"), ("diam", "Tidak ada scan"), ("nonaktif", "Nonaktif"))
SORTS = (("nama", "Nama"), ("lokasi", "Lokasi"), ("ip", "IP Address"), ("port", "Port"), ("protokol", "Protokol"), ("no", "No Mesin"),
         ("scan", "Jumlah scan (30 hr)"), ("scan_terakhir", "Scan terakhir"), ("tarikan", "Tarikan terakhir"))
GROUPS = (("port", "Port"), ("protokol", "Protokol"), ("koneksi", "Koneksi"), ("lokasi", "Lokasi"), ("status", "Status"), ("segmen", "Segmen IP"), ("otomatis", "Tarik otomatis"))
RESULTS = (("ok", "Berhasil"), ("gagal", "Gagal"), ("belum", "Belum pernah"))
YESNO = (("ya", "Ya"), ("tidak", "Tidak"))


def _ip(host):
    p = (host or "").strip().split(".")
    return tuple(int(x) for x in p) if len(p) == 4 and all(x.isdigit() and int(x) < 256 for x in p) else None


def _status(r): return "nonaktif" if not r["d"].active else ("diam" if r.get("idle") else "aktif")


def _num(v):
    v = (v or "").strip()
    return int(v) if v.isdigit() else None


# --------------------------------------------------------------------------------------------- filter
def parse(g, ports=()):
    """GET → dict nilai yang sudah divalidasi (kosong = tidak aktif)."""
    pick = lambda name, allowed: (g.get(name) or "").strip() if (g.get(name) or "").strip() in allowed else ""   # noqa: E731
    f = {"q": (g.get("q") or "").strip()[:80], "protokol": pick("protokol", PROTO_SHORT), "koneksi": pick("koneksi", ("Ethernet", "WiFi", "USB", "GPRS")),
         "status": pick("status", dict(STATUSES)), "otomatis": pick("otomatis", dict(YESNO)), "hasil": pick("hasil", dict(RESULTS)),
         "urut": pick("urut", dict(SORTS)), "arah": "turun" if g.get("arah") == "turun" else "naik", "port": _num(g.get("port"))}
    seen, grp = set(), []
    for k in ("g1", "g2", "g3"):
        v = pick(k, dict(GROUPS))
        if v and v not in seen: seen.add(v); grp.append(v)
    f["group"] = grp
    return f


def is_filtered(f): return bool(f["q"] or f["protokol"] or f["koneksi"] or f["status"] or f["otomatis"] or f["hasil"] or f["port"] is not None)


def _match(r, f):
    d = r["d"]
    if f["q"]:
        hay = " ".join(str(x or "") for x in (d.name, d.location, d.code, d.serial, d.host, d.machine_no, d.note)).lower()
        if any(t not in hay for t in f["q"].lower().split()): return False       # semua kata harus ada (urutan bebas)
    if f["protokol"] and d.protocol != f["protokol"]: return False
    if f["port"] is not None and d.port != f["port"]: return False
    if f["koneksi"] and d.connection != f["koneksi"]: return False
    if f["status"] and _status(r) != f["status"]: return False
    if f["otomatis"] and d.sync_enabled != (f["otomatis"] == "ya"): return False
    if f["hasil"]:
        res = "belum" if not d.last_sync_at else ("ok" if d.last_sync_ok else "gagal")
        if res != f["hasil"]: return False
    return True


# --------------------------------------------------------------------------------------------- urutan
def _sort_key(name, r):
    d = r["d"]
    return {"nama": (d.name or "").casefold() or None, "lokasi": (d.location or "").casefold() or None, "ip": _ip(d.host), "port": d.port, "protokol": d.protocol,
            "no": _num(d.machine_no), "scan": r["n"] if r.get("last") else None, "scan_terakhir": r.get("last"), "tarikan": d.last_sync_at}[name]


def _sorted(rows, name, desc):
    if not name: return rows
    have = [r for r in rows if _sort_key(name, r) is not None]; none = [r for r in rows if _sort_key(name, r) is None]
    have.sort(key=lambda r: _sort_key(name, r), reverse=desc)
    return have + none


# --------------------------------------------------------------------------------------------- kelompok
def _group_val(dim, r):
    """→ (kunci urut, label). Kunci urut membuat 'tanpa nilai' di akhir."""
    d = r["d"]
    if dim == "port": return (0, d.port), f"Port {d.port}"
    if dim == "protokol": return (0, d.protocol), PROTO_SHORT.get(d.protocol, d.protocol)
    if dim == "koneksi": return ((0, d.connection), d.get_connection_display()) if d.connection else ((1, ""), "(koneksi kosong)")
    if dim == "lokasi": return ((0, d.location.casefold()), d.location) if d.location else ((1, ""), "(tanpa lokasi)")
    if dim == "status": s = _status(r); return (0, [x[0] for x in STATUSES].index(s)), dict(STATUSES)[s]
    if dim == "segmen":
        ip = _ip(d.host)
        return ((0, ip[:3]), f"{ip[0]}.{ip[1]}.{ip[2]}.x") if ip else ((1, ""), "(tanpa IP / bukan IPv4)")
    if dim == "otomatis": return (0, not d.sync_enabled), "Tarik otomatis" if d.sync_enabled else "Manual (tidak otomatis)"
    raise KeyError(dim)


def build(rows, f):
    """rows (dari device_status) + filter → (baris tersaring, item tampil, total sebelum filter).
    item = baris mesin (dict asli + 'gkey') ATAU {'hdr': True, 'level', 'label', 'n', 'key'} (judul kelompok, diikuti anak-anaknya)."""
    total = len(rows); rows = [r for r in rows if _match(r, f)]
    rows = _sorted(rows, f["urut"], f["arah"] == "turun")
    dims = f["group"]
    for dim in reversed(dims): rows = sorted(rows, key=lambda r, dim=dim: _group_val(dim, r)[0])           # sort stabil: urutan dalam kelompok tetap sesuai urutan pilihan
    if not dims:
        for r in rows: r["gkey"] = ""
        return rows, list(rows), total
    paths = []
    for r in rows:
        p = []
        for dim in dims:
            sk, label = _group_val(dim, r); p.append((dim, str(sk[1]).replace(";", ",").replace("=", ":"), label))
        paths.append(p)
    count = Counter()
    for p in paths:
        for i in range(len(p)): count[tuple(p[:i + 1])] += 1
    items, prev = [], []
    for r, p in zip(rows, paths):
        for i in range(len(p)):
            if prev[:i + 1] != p[:i + 1]:
                items.append({"hdr": True, "level": i, "label": p[i][2], "dim": p[i][0], "n": count[tuple(p[:i + 1])], "key": "".join(f"{x[0]}={x[1]};" for x in p[:i + 1])})
        r["gkey"] = "".join(f"{x[0]}={x[1]};" for x in p); items.append(r); prev = p
    return rows, items, total
