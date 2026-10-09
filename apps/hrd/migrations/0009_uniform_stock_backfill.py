"""Putaran 25: isi kartu stok dari pembelian seragam yang sudah ada (keluar per pembelian; batal untuk yang sudah dibatalkan). Saldo awal 0 → bisa minus
sampai 'Barang masuk' dicatat. Idempoten (dilewati bila sudah ada baris untuk pembelian itu)."""
from django.db import migrations


def backfill(apps, schema_editor):
    P, S, M = apps.get_model("hrd", "UniformPurchase"), apps.get_model("hrd", "UniformStock"), apps.get_model("hrd", "UniformStockMovement")
    for p in P.objects.order_by("id"):
        if M.objects.filter(purchase=p).exists(): continue
        st, _ = S.objects.get_or_create(utype_id=p.utype_id, size_id=p.size_id)
        st.balance -= p.quantity; st.save(update_fields=["balance"])
        M.objects.create(utype_id=p.utype_id, size_id=p.size_id, kind="keluar", quantity=-p.quantity, balance_after=st.balance, movement_date=p.purchase_date,
                         purchase=p, note="Dibuat otomatis dari pembelian lama", created_by_id=p.created_by_id)
        if p.voided_at:
            st.balance += p.quantity; st.save(update_fields=["balance"])
            M.objects.create(utype_id=p.utype_id, size_id=p.size_id, kind="batal", quantity=p.quantity, balance_after=st.balance, movement_date=p.voided_at.date(),
                             purchase=p, note="Dibuat otomatis dari pembatalan lama", created_by_id=p.voided_by_id)


class Migration(migrations.Migration):
    dependencies = [("hrd", "0008_uniform_stock")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
