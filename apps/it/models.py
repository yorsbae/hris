"""Modul IT (putaran 66): aset, jaringan/IP (IPAM), lisensi software, domain & SSL, tiket. Tanpa data pribadi karyawan selain nama pemegang aset."""
import ipaddress
from datetime import date, timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from apps.core.crypto import EncryptedTextField


def soon(d, days=30):
    """Kedaluwarsa: None bila tanggal kosong / masih jauh; 'lewat' atau 'segera' (≤ days hari)."""
    if not d: return None
    t = date.today()
    return "lewat" if d < t else "segera" if d <= t + timedelta(days=days) else None


class Asset(models.Model):
    CATS = [("laptop", "Laptop"), ("pc", "PC / Desktop"), ("server", "Server"), ("network", "Perangkat jaringan"), ("printer", "Printer / Scanner"),
            ("phone", "Ponsel / Tablet"), ("fingerprint", "Mesin absensi / CCTV"), ("peripheral", "Periferal"), ("other", "Lainnya")]
    STATUS = [("stok", "Stok"), ("dipakai", "Dipakai"), ("perbaikan", "Perbaikan"), ("dihapus", "Dihapus / afkir")]
    tag = models.CharField("Kode aset", max_length=30, unique=True)
    name = models.CharField("Nama", max_length=120)
    category = models.CharField("Kategori", max_length=12, choices=CATS, default="laptop", db_index=True)
    brand = models.CharField("Merek / tipe", max_length=80, blank=True); serial = models.CharField("No. seri", max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="stok", db_index=True)
    department = models.ForeignKey("hr.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Departemen")
    assignee = models.CharField("Pemegang", max_length=120, blank=True); location = models.CharField("Lokasi", max_length=120, blank=True)
    purchase_date = models.DateField("Tgl beli", null=True, blank=True); warranty_until = models.DateField("Garansi s/d", null=True, blank=True, db_index=True)
    price = models.DecimalField("Harga (Rp)", max_digits=14, decimal_places=0, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["tag"]
    def __str__(self): return f"{self.tag} · {self.name}"
    @property
    def warranty_flag(self): return soon(self.warranty_until) if self.status != "dihapus" else None


class AssetEvent(models.Model):
    """Riwayat aset (append-only): dibuat, dipindah, ganti status, perbaikan."""
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=30); detail = models.CharField(max_length=300, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-at", "-id"]


class Subnet(models.Model):
    name = models.CharField("Nama", max_length=80); cidr = models.CharField("CIDR (mis. 192.168.10.0/24)", max_length=43, unique=True)
    vlan = models.PositiveSmallIntegerField("VLAN", null=True, blank=True); gateway = models.GenericIPAddressField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    class Meta: ordering = ["cidr"]
    def __str__(self): return f"{self.name} ({self.cidr})"
    def clean(self):
        try: net = ipaddress.ip_network((self.cidr or "").strip(), strict=True)
        except ValueError: raise ValidationError({"cidr": "CIDR tidak valid (alamat jaringan, mis. 192.168.10.0/24)."})
        if net.num_addresses > 65536: raise ValidationError({"cidr": "Terlalu besar (maksimal /16)."})
        self.cidr = str(net)
        for o in Subnet.objects.exclude(pk=self.pk):
            if ipaddress.ip_network(o.cidr).version == net.version and ipaddress.ip_network(o.cidr).overlaps(net):
                raise ValidationError({"cidr": f"Tumpang tindih dengan subnet {o.name} ({o.cidr})."})
        if self.gateway and ipaddress.ip_address(self.gateway) not in net: raise ValidationError({"gateway": "Gateway di luar subnet."})
    @property
    def net(self): return ipaddress.ip_network(self.cidr)
    @property
    def capacity(self): n = self.net; return max(n.num_addresses - 2, 1) if n.version == 4 and n.num_addresses > 2 else n.num_addresses
    def used_set(self): return {ipaddress.ip_address(a) for a in self.ips.values_list("address", flat=True)}
    def next_free(self):
        used = self.used_set(); gw = ipaddress.ip_address(self.gateway) if self.gateway else None
        for h in self.net.hosts():
            if h not in used and h != gw: return str(h)
        return ""


class IPAddress(models.Model):
    KINDS = [("statis", "Statis"), ("reservasi", "Reservasi DHCP"), ("dhcp", "DHCP (dinamis)")]
    subnet = models.ForeignKey(Subnet, on_delete=models.CASCADE, related_name="ips")
    address = models.GenericIPAddressField("Alamat IP")
    hostname = models.CharField(max_length=100, blank=True); mac = models.CharField("MAC", max_length=17, blank=True)
    kind = models.CharField(max_length=10, choices=KINDS, default="statis")
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.SET_NULL, related_name="ips")
    note = models.CharField(max_length=200, blank=True)
    class Meta:
        ordering = ["subnet", "id"]
        constraints = [models.UniqueConstraint(fields=["subnet", "address"], name="uniq_ip_in_subnet")]
    def __str__(self): return self.address
    def clean(self):
        if not self.subnet_id: return
        try: ip = ipaddress.ip_address(str(self.address))
        except ValueError: raise ValidationError({"address": "Alamat IP tidak valid."})
        net = self.subnet.net
        if ip not in net: raise ValidationError({"address": f"{ip} di luar subnet {net}."})
        if ip in (net.network_address, getattr(net, "broadcast_address", None)) and net.version == 4 and net.num_addresses > 2: raise ValidationError({"address": "Alamat jaringan/broadcast tidak boleh dipakai."})
        self.address = str(ip)
        if self.mac:
            m = self.mac.strip().lower().replace("-", ":")
            if len(m) != 17 or any(len(x) != 2 or any(c not in "0123456789abcdef" for c in x) for x in m.split(":")): raise ValidationError({"mac": "Format MAC aa:bb:cc:dd:ee:ff."})
            self.mac = m
            dup = IPAddress.objects.filter(mac=m).exclude(pk=self.pk).first()
            if dup: raise ValidationError({"mac": f"MAC sudah dipakai {dup.address} ({dup.hostname or '-'})."})


class License(models.Model):
    name = models.CharField("Software", max_length=120); vendor = models.CharField(max_length=80, blank=True)
    seats = models.PositiveIntegerField("Jumlah seat", default=1); used = models.PositiveIntegerField("Seat terpakai", default=0)
    expires = models.DateField("Berlaku s/d", null=True, blank=True, db_index=True)
    key = EncryptedTextField("Kunci / serial", max_length=300, blank=True)   # terenkripsi di DB; tidak ditampilkan di daftar
    cost = models.DecimalField("Biaya (Rp)", max_digits=14, decimal_places=0, null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    class Meta: ordering = ["name"]
    def __str__(self): return self.name
    def clean(self):
        if self.used > self.seats: raise ValidationError({"used": "Seat terpakai melebihi jumlah seat."})
    @property
    def flag(self): return soon(self.expires)


class Domain(models.Model):
    KINDS = [("domain", "Domain"), ("ssl", "Sertifikat SSL"), ("hosting", "Hosting / VPS"), ("cloud", "Layanan cloud / langganan")]
    name = models.CharField("Nama / host", max_length=160); kind = models.CharField(max_length=8, choices=KINDS, default="domain")
    provider = models.CharField("Penyedia", max_length=80, blank=True); expires = models.DateField("Berlaku s/d", db_index=True)
    auto_renew = models.BooleanField("Perpanjang otomatis", default=False); note = models.CharField(max_length=300, blank=True)
    class Meta: ordering = ["expires", "name"]
    def __str__(self): return self.name
    @property
    def flag(self): return soon(self.expires)


class Ticket(models.Model):
    PRIO = [("rendah", "Rendah"), ("sedang", "Sedang"), ("tinggi", "Tinggi"), ("kritis", "Kritis")]
    STATUS = [("baru", "Baru"), ("proses", "Diproses"), ("selesai", "Selesai"), ("ditolak", "Ditolak")]
    # Alur keputusan IT atas permintaan JASA/bantuan: Baru → (Terima) Diproses → Selesai; Baru/Diproses → Ditolak (alasan wajib). Selesai & Ditolak final.
    FLOW = {"baru": ("proses", "ditolak"), "proses": ("selesai", "ditolak"), "selesai": (), "ditolak": ()}
    ACTION = {"proses": "Terima & proses", "selesai": "Tandai selesai", "ditolak": "Tolak"}
    title = models.CharField("Judul", max_length=160); detail = models.TextField("Uraian", blank=True)
    CATS = [("perbaikan", "Perbaikan perangkat"), ("instalasi", "Instalasi / pemasangan"), ("akun", "Akun, email & akses"), ("jaringan", "Jaringan / internet / Wi-Fi"),
            ("aplikasi", "Aplikasi / HRIS"), ("cetak", "Printer & cetak"), ("lain", "Lainnya")]
    requester = models.CharField("Pelapor", max_length=120, blank=True)
    category = models.CharField("Jenis bantuan", max_length=10, choices=CATS, default="lain")
    location = models.CharField("Lokasi / ruangan", max_length=120, blank=True); contact = models.CharField("Kontak (telp/ext)", max_length=40, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    department = models.ForeignKey("hr.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Departemen")
    priority = models.CharField(max_length=8, choices=PRIO, default="sedang", db_index=True)
    status = models.CharField(max_length=8, choices=STATUS, default="baru", db_index=True)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets")
    due = models.DateField("Target selesai", null=True, blank=True)
    decision_note = models.CharField("Catatan IT / alasan", max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True); resolved_at = models.DateTimeField(null=True, blank=True)
    class Meta: ordering = ["-id"]
    def __str__(self): return self.title


# ---------- Inventori stok IT, wishlist/order, perangkat jaringan (putaran 67)
class StockItem(models.Model):
    CATS = [("kabel", "Kabel & konektor"), ("storage", "RAM / SSD / HDD"), ("periferal", "Mouse, keyboard, headset"), ("toner", "Tinta / toner / kertas"), ("jaringan", "Perangkat jaringan kecil"),
            ("daya", "Baterai / charger / UPS"), ("cctv", "Kamera / fingerprint cadangan"), ("lain", "Lainnya")]
    sku = models.CharField("Kode barang", max_length=30, unique=True); name = models.CharField("Nama barang", max_length=120)
    category = models.CharField("Kategori", max_length=10, choices=CATS, default="lain", db_index=True)
    unit = models.CharField("Satuan", max_length=15, default="pcs"); min_stock = models.PositiveIntegerField("Batas minimum", default=0, help_text="Di bawah/sama dengan ini = stok menipis.")
    location = models.CharField("Lokasi simpan", max_length=100, blank=True); note = models.CharField(max_length=300, blank=True)
    qty = models.IntegerField("Stok saat ini", default=0, editable=False)   # cache dari StockMovement (diubah hanya lewat StockMovement.record)
    class Meta: ordering = ["name"]
    def __str__(self): return f"{self.sku} · {self.name}"
    @property
    def low(self): return self.qty <= self.min_stock


class StockMovement(models.Model):
    """Barang masuk / keluar (append-only). Masuk: `party` = pemasok/asal. Keluar: `party` = penerima (orang) + departemen."""
    DIR = [("masuk", "Masuk"), ("keluar", "Keluar")]
    item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="moves")
    direction = models.CharField("Arah", max_length=6, choices=DIR, db_index=True); qty = models.PositiveIntegerField("Jumlah")
    date = models.DateField("Tanggal", db_index=True, default=date.today)
    party = models.CharField("Pemasok / penerima", max_length=120)
    department = models.ForeignKey("hr.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Departemen penerima")
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Dipasang ke aset")
    note = models.CharField("Keperluan / catatan", max_length=300, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"); created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(qty__gt=0), name="stockmove_qty_pos")]
    def __str__(self): return f"{self.direction} {self.qty} {self.item.name}"

    @classmethod
    def record(cls, item_id, direction, qty, party, by=None, **kw):
        """Catat mutasi + perbarui stok atomik; keluar tidak boleh melebihi stok (stok tidak pernah negatif)."""
        from django.db import transaction
        with transaction.atomic():
            it = StockItem.objects.select_for_update().get(pk=item_id)
            if direction == "keluar" and qty > it.qty: raise ValidationError(f"Stok {it.name} hanya {it.qty} {it.unit}; tidak cukup untuk {qty}.")
            if qty <= 0: raise ValidationError("Jumlah harus lebih dari 0.")
            mv = cls.objects.create(item=it, direction=direction, qty=qty, party=party, by=by, **kw)
            it.qty += qty if direction == "masuk" else -qty; it.save(update_fields=["qty"]); return mv


class Wish(models.Model):
    """Wishlist / permintaan barang atau layanan IT dari departemen (beserta alasannya)."""
    STATUS = [("diajukan", "Diajukan"), ("disetujui", "Disetujui"), ("dipesan", "Dipesan"), ("diterima", "Diterima"), ("ditolak", "Ditolak")]
    PRIO = Ticket.PRIO
    title = models.CharField("Barang / kebutuhan", max_length=160); qty = models.PositiveIntegerField("Jumlah", default=1)
    item = models.ForeignKey(StockItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="wishes", verbose_name="Barang stok terkait")
    department = models.ForeignKey("hr.Department", on_delete=models.PROTECT, related_name="+", verbose_name="Departemen")
    requester = models.CharField("Pemohon", max_length=120, blank=True); reason = models.TextField("Alasan")
    priority = models.CharField("Prioritas", max_length=8, choices=PRIO, default="sedang")
    status = models.CharField(max_length=10, choices=STATUS, default="diajukan", db_index=True)
    est_price = models.DecimalField("Perkiraan harga (Rp)", max_digits=14, decimal_places=0, null=True, blank=True)
    decision_note = models.CharField("Catatan IT", max_length=300, blank=True); received_logged = models.BooleanField(default=False, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True); updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ["-id"]
    def __str__(self): return self.title
    FLOW = {"diajukan": ("disetujui", "ditolak"), "disetujui": ("dipesan", "ditolak"), "dipesan": ("diterima",), "diterima": (), "ditolak": ()}


class NetDevice(models.Model):
    GROUPS = {"router": ("Router, Switch & Firewall", ["router", "switch", "firewall"]), "wifi": ("Access Point / Wi-Fi", ["ap"]), "fingerprint": ("Fingerprint / Mesin absensi", ["fingerprint"]),
              "cctv": ("CCTV & NVR", ["cctv", "nvr"]), "lain": ("Perangkat lain", ["server", "printer", "other"])}
    KINDS = [("router", "Router"), ("switch", "Switch"), ("firewall", "Firewall"), ("ap", "Access Point"), ("fingerprint", "Fingerprint"), ("cctv", "Kamera CCTV"), ("nvr", "NVR / DVR"),
             ("server", "Server / NAS"), ("printer", "Printer jaringan"), ("other", "Lainnya")]
    STATUS = [("aktif", "Aktif"), ("mati", "Mati / tidak merespons"), ("perbaikan", "Perbaikan"), ("cadangan", "Cadangan")]
    name = models.CharField("Nama", max_length=100); kind = models.CharField("Jenis", max_length=12, choices=KINDS, default="router", db_index=True)
    brand = models.CharField("Merek / tipe", max_length=80, blank=True); serial = models.CharField("No. seri", max_length=80, blank=True)
    ip = models.ForeignKey(IPAddress, null=True, blank=True, on_delete=models.SET_NULL, related_name="devices", verbose_name="Alamat IP")
    location = models.CharField("Lokasi pemasangan", max_length=120, blank=True); status = models.CharField(max_length=10, choices=STATUS, default="aktif", db_index=True)
    firmware = models.CharField("Firmware", max_length=40, blank=True); last_checked = models.DateField("Terakhir dicek", null=True, blank=True)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Aset terkait")
    note = models.CharField(max_length=300, blank=True)
    class Meta: ordering = ["kind", "name"]
    def __str__(self): return self.name
    @classmethod
    def group_of(cls, kind): return next((g for g, (_, ks) in cls.GROUPS.items() if kind in ks), "lain")
