"""Konversi data sebelum bentuk lama dihapus (putaran 20). TIDAK bisa dibalik tanpa kehilangan data.
- Bantuan berstatus 'ditolak' dihapus (rekapan hanya mencatat bantuan yang benar-benar ada).
- Katering: pesanan 'batal' dihapus; waktu makan lama dipetakan sarapan→09:00, siang→12:00, malam→18:00, snack→02:00;
  baris dengan tanggal+jam sama (dulu beda departemen) digabung (jumlah dijumlahkan; diterima hanya bila semuanya sudah diterima).
- Catatan harian proyek lama (pekerja = karyawan) dikonversi menjadi satu baris pekerja-hari per karyawan, upah 0 (isi ulang upahnya bila perlu);
  catatan tanpa daftar karyawan menjadi satu baris '(N orang, tidak dicatat per nama)'.
BACKUP database sebelum migrate."""
from django.db import migrations

MEALS = {"sarapan": "0900", "siang": "1200", "malam": "1800", "snack": "0200"}


def forward(apps, schema_editor):
    Aid, Cat = apps.get_model("hrd", "Aid"), apps.get_model("hrd", "CateringOrder")
    Log, Work = apps.get_model("hrd", "ProjectDailyLog"), apps.get_model("hrd", "ProjectWork")
    Aid.objects.filter(status="ditolak").delete()
    Cat.objects.filter(status="batal").delete()
    merged = {}
    for o in Cat.objects.order_by("id"):
        key = (o.date, MEALS.get(o.meal, o.meal))
        m = merged.get(key)
        if m is None:
            merged[key] = {"keep": o, "dup": [], "ql": o.qty_large, "qs": o.qty_small, "rl": o.received_large if o.status == "diterima" else None,
                           "rs": o.received_small if o.status == "diterima" else None, "all_recv": o.status == "diterima"}
        else:
            m["dup"].append(o); m["ql"] += o.qty_large; m["qs"] += o.qty_small; m["all_recv"] = m["all_recv"] and o.status == "diterima"
            if o.status == "diterima": m["rl"] = (m["rl"] or 0) + (o.received_large or 0); m["rs"] = (m["rs"] or 0) + (o.received_small or 0)
    for (d, meal), m in merged.items():
        for x in m["dup"]: x.delete()
        k = m["keep"]; k.meal = meal; k.qty_large, k.qty_small = m["ql"], m["qs"]
        k.received_large, k.received_small = (m["rl"], m["rs"]) if m["all_recv"] else (None, None); k.save()
    for lg in Log.objects.prefetch_related("workers").order_by("id"):
        workers = list(lg.workers.all())
        if workers:
            for w in workers:
                Work.objects.create(project_id=lg.project_id, work_date=lg.work_date, worker_name=w.name[:100], activity=lg.activity[:300], wage=0, note=lg.note[:300], created_by_id=lg.created_by_id)
        else:
            Work.objects.create(project_id=lg.project_id, work_date=lg.work_date, worker_name=f"({lg.headcount} orang, tidak dicatat per nama)", activity=lg.activity[:300], wage=0, note=lg.note[:300], created_by_id=lg.created_by_id)


class Migration(migrations.Migration):
    dependencies = [("hrd", "0002_recap_sp_projectwork")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
