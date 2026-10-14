"""Mesin impor massal CSV/XLSX generik (putaran 18): tambah-atau-perbarui menurut kunci, divalidasi dengan form yang SAMA dengan input manual.
Semua-atau-tidak-sama-sekali: satu baris salah → tidak ada yang tersimpan. Mode \"periksa saja\" tidak menyimpan apa pun. Hanya ringkasan masuk audit."""
import hashlib
import inspect
from dataclasses import dataclass, field
from typing import Callable
from django.contrib.auth.decorators import login_required
from django.db import connection, transaction
from django.http import Http404, HttpResponse
from django.shortcuts import render
from . import tabular
from .audit import log


@dataclass
class Spec:
    slug: str; module: str; title: str; back: str
    model: type; key: str; form: type
    columns: list; required: list; example: list
    to_data: Callable            # (row, ctx) -> dict untuk form; boleh raise ValueError(pesan)
    hint: str = ""
    after_save: Callable = None  # (obj, row, ctx) setelah disimpan
    case_insensitive_key: bool = False
    notes: list = field(default_factory=list)
    key_fn: Callable = None      # kunci gabungan (mis. kelompok|hari) bila satu kolom tidak cukup; model=None → selalu "tambah/perbarui" lewat form.save()


def _lookup(spec, key):
    if spec.model is None: return None
    return spec.model.objects.filter(**{f"{spec.key}__iexact" if spec.case_insensitive_key else spec.key: key}).first()


def _form_kwargs(spec, obj, user):
    """Putaran 26: teruskan pengguna ke form yang menerimanya (mis. created_by hasil impor); form lain tak berubah."""
    kw = {"instance": obj}
    if user is not None and "user" in inspect.signature(spec.form.__init__).parameters: kw["user"] = user
    return kw


def _plan(spec, rows, user):
    """Validasi semua baris (tanpa menyimpan). Return (plan, errors). Dipanggil dua kali saat 'Impor': sekali untuk pratinjau, sekali lagi DI DALAM transaksi
    setelah kunci — supaya dua impor bersamaan (atau pengguna yang mengetik manual di saat yang sama) tidak lolos validasi 'sudah ada' lalu sama-sama menyimpan."""
    ctx = {"before": set()}; seen, plan, errors = set(), [], []
    for i, r in enumerate(rows, start=2):  # baris 1 = header
        errs = [f"Kolom {k}: tidak boleh diawali '{v[:1]}' (risiko formula spreadsheet)." for k, v in r.items() if tabular.formula_like(v)]
        key = spec.key_fn(r) if spec.key_fn else r.get(spec.key, ""); norm = key.upper() if spec.key == "code" else key.lower()
        if norm in seen: errs.append("Kunci dobel di dalam file.")
        seen.add(norm)
        obj = _lookup(spec, key) if key else None
        if not errs:
            try: data = spec.to_data(r, ctx)
            except ValueError as e: errs.append(str(e)); data = None
            if data is not None:
                form = spec.form(data, **_form_kwargs(spec, obj, user))
                if form.is_valid(): plan.append((form, r, obj is None, key))
                else: errs += [f"{f}: {m}" for f, ms in form.errors.items() for m in ms]
        ctx["before"].add(key.upper())
        if errs: errors.append((i, key, errs))
    return plan, errors


def _lock(spec):
    """Kunci per-spesifikasi selama transaksi impor (PostgreSQL: advisory lock; SQLite menulis serial sehingga tak perlu)."""
    if connection.vendor != "postgresql": return
    h = int(hashlib.sha1(f"bulk:{spec.module}:{spec.slug}".encode()).hexdigest()[:15], 16)
    with connection.cursor() as c: c.execute("SELECT pg_advisory_xact_lock(%s)", [h])


def run(spec: Spec, raw, filename, commit, user=None):
    res = {"ok": False, "fatal": "", "errors": [], "total": 0, "created": 0, "updated": 0, "unchanged": 0, "commit": commit}
    try: rows = tabular.read_table(raw, filename, spec.required)
    except ValueError as e: res["fatal"] = str(e); return res
    res["total"] = len(rows)
    plan, errors = _plan(spec, rows, user)
    res["errors"] = errors; res["ok"] = not errors; res["errors_shown"] = errors[:100]
    if errors or not commit: return res
    with transaction.atomic():
        _lock(spec)
        plan, errors = _plan(spec, rows, user)  # validasi ulang setelah kunci: data bisa berubah sejak pratinjau
        if errors:
            res["errors"] = errors; res["ok"] = False; res["errors_shown"] = errors[:100]; return res
        ctx = {"before": set()}
        for form, r, is_new, key in plan:
            o = form.save(); ctx["before"].add(key.upper())
            if spec.after_save: spec.after_save(o, r, ctx)
            # Nilai identik dengan data aktif → tidak ada baris baru (impor ulang file yang sama tidak menambah apa pun)
            res["unchanged" if getattr(form, "unchanged", False) else "updated" if (not is_new or getattr(form, "replaced", False)) else "created"] += 1
    return res


def import_view(spec: Spec, decorator):
    @login_required
    @decorator
    def view(request):
        res = None
        if request.method == "POST":
            f = request.FILES.get("file")
            if not f: res = {"ok": False, "fatal": "Pilih file CSV atau XLSX.", "errors": [], "errors_shown": [], "total": 0, "created": 0, "updated": 0, "unchanged": 0, "commit": False}
            else:
                commit = request.POST.get("mode") == "import"
                res = run(spec, f.read(tabular.MAX_BYTES + 1), f.name, commit, user=request.user)
                if commit: log(request, spec.module, f"{spec.slug}_import" + ("" if res["ok"] else "_rejected"), None, None,
                               {"file": f.name[:100], "rows": res["total"], "created": res["created"], "updated": res["updated"], "errors": len(res["errors"]), "unchanged": res.get("unchanged", 0)})
        return render(request, "bulk_import.html", {"spec": spec, "res": res})
    return view


def template_view(spec: Spec, decorator):
    @login_required
    @decorator
    def view(request):
        return tabular.export_response(f"template-{spec.slug}", spec.columns, [spec.example], request, sheet=spec.title)
    return view
