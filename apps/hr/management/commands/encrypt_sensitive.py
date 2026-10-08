from django.core.management.base import BaseCommand
from django.db import connection, transaction
from apps.core.crypto import encrypt, is_encrypted
from apps.hr.models import Employee


class Command(BaseCommand):
    help = "Enkripsi kolom sensitif karyawan yang masih plaintext (idempoten). Backup DB dulu; pakai --dry-run untuk simulasi."

    def add_arguments(self, p): p.add_argument("--dry-run", action="store_true")

    def handle(self, *a, dry_run=False, **k):
        table, cols = Employee._meta.db_table, Employee.ENCRYPTED
        q = connection.ops.quote_name
        todo = 0
        with transaction.atomic(), connection.cursor() as cur:  # baca mentah: from_db_value akan menyembunyikan status asli
            cur.execute(f"SELECT id, {', '.join(q(c) for c in cols)} FROM {q(table)}")  # nosec B608 — nama tabel/kolom dari model & di-quote lewat connection.ops.quote_name; nilai lewat parameter
            for pk, *vals in cur.fetchall():
                new = {c: encrypt(v) for c, v in zip(cols, vals) if v and not is_encrypted(v)}
                if not new: continue
                todo += 1
                if dry_run: continue
                sets = ", ".join(f"{q(c)} = %s" for c in new)
                cur.execute(f"UPDATE {q(table)} SET {sets} WHERE id = %s", [*new.values(), pk])  # nosec B608 — nama tabel/kolom dari model & di-quote lewat connection.ops.quote_name; nilai lewat parameter
        self.stdout.write(f"{'Akan dienkripsi' if dry_run else 'Dienkripsi'}: {todo} karyawan.")
