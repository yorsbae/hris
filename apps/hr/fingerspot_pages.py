"""Halaman "Kelola User Fingerspot" (/absensi/pin/) — pengganti Pemetaan PIN. Hanya HRD/Superadmin. Aturan bisnis ada di fingerspot_users."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.core import tabular
from apps.core.audit import log
from . import attendance_import as imp, fingerspot_users as fu
from .attendance_pages import _reprocess_scans, hrd
from .attendance_reports import _user_add
from .models import AttendancePin, Department, Employee

PER_PAGE = 50


def _back(request):
    """Kembali ke halaman yang sama dengan filter yang sedang aktif (field tersembunyi `back` = query string, hanya relatif ke halaman ini)."""
    qs = request.POST.get("back", "")
    return redirect(f"{reverse('att_pins')}?{qs}" if qs and not qs.startswith(("http", "/", "\\")) else "att_pins")


def _emp(nik, what="NIK"):
    e = Employee.objects.filter(nik=(nik or "").strip()).first()
    if not e: raise ValueError(f"{what} tidak ditemukan.")
    return e


def _act(request):
    """Satu aksi POST. Melempar ValueError / AttendancePin.DoesNotExist untuk pesan galat; mengembalikan hasil impor CSV bila ada."""
    p, act, actor = request.POST, request.POST.get("action"), request.user
    pins = p.getlist("pins")
    if act == "link":
        e = _emp(p.get("nik"), "NIK"); pin = p.get("pin", "")
        n = imp.link_pin(actor, pin, e, "manual"); log(request, "hr", "attendance_pin_link", e, None, {"pin": pin[:40], "scans": n})
        if n: _reprocess_scans(e, pin)
        messages.success(request, f"PIN {pin} dipetakan ke {e.nik} {e.name}; {n} scan lama ikut terhubung.")
    elif act == "auto_name":
        n, scans, pairs = imp.apply_name_matches(actor)
        for pin, e in pairs: _reprocess_scans(e, pin)
        log(request, "hr", "attendance_pin_auto_name", None, None, {"pins": n, "scans": scans})
        messages.success(request, f"{n} PIN dipetakan otomatis berdasarkan nama ({scans} scan terhubung)." if n else "Tidak ada PIN yang namanya cocok tepat satu karyawan (dan karyawannya belum punya PIN). Jalankan 'Download user' dari mesin dulu agar nama tersedia.")
    elif act == "move":
        e = _emp(p.get("nik"), "NIK tujuan"); pin = p.get("pin", "")
        old, n = imp.move_pin(actor, pin, e, p.get("reason", ""))
        log(request, "hr", "attendance_pin_move", e, {"from": old.nik}, {"pin": pin[:40], "scans": n})
        _reprocess_scans(e, pin, also=old); messages.success(request, f"PIN dipindah dari {old.nik} ke {e.nik} ({n} scan).")
    elif act == "unlink":
        pin = p.get("pin", ""); e = fu.unlink_pin(pin, p.get("reason", ""))
        log(request, "hr", "attendance_pin_unlink", e, {"pin": pin[:40]}, {"reason": p.get("reason", "")[:150]})
        messages.success(request, f"Pemetaan PIN {pin} dari {e.nik} {e.name} dilepas; PIN kembali ke antrean belum dipetakan.")
    elif act in ("user_add", "user_edit"):
        _user_add(request)                                                  # tambah/ubah user mesin (validasi + log + opsi petakan ke NIK) — satu jalur dengan halaman Mesin
    elif act in ("deactivate", "activate"):
        on = act == "activate"; n = fu.set_active(pins, on, p.get("reason", ""), actor)
        log(request, "hr", "attendance_device_user_" + act, None, None, {"pins": pins[:50], "count": n, "reason": p.get("reason", "")[:150]})
        messages.success(request, (f"{n} user diaktifkan kembali." if on else f"{n} user dinonaktifkan: tidak ikut Upload user ke mesin dan tidak muncul di antrean belum dipetakan.") if n else "Tidak ada perubahan (status sudah sesuai).")
    elif act == "deactivate_resigned":
        n = fu.deactivate_resigned(actor); log(request, "hr", "attendance_device_user_deactivate_resigned", None, None, {"count": n})
        messages.success(request, f"{n} user milik karyawan nonaktif dinonaktifkan." if n else "Tidak ada user aktif milik karyawan nonaktif.")
    elif act == "delete":
        n, skipped = fu.delete_users(pins); log(request, "hr", "attendance_device_user_delete", None, None, {"pins": pins[:50], "deleted": n})
        if n: messages.success(request, f"{n} user dihapus dari HRIS (beserta template sidik jarinya). Pemetaan dan scan lama tidak berubah. Bila user masih ada di mesin, hapus juga di mesin agar tidak terunduh kembali.")
        if skipped: messages.warning(request, f"{len(skipped)} user aktif dilewati — nonaktifkan dulu sebelum menghapus: {', '.join(skipped[:5])}{'…' if len(skipped) > 5 else ''}.")
    elif act == "create_for_employees":
        made, skipped, nonnum = fu.create_for_employees(p.getlist("niks"), actor)
        log(request, "hr", "attendance_device_user_create_for_employees", None, None, {"created": made, "skipped": len(skipped)})
        if made: messages.success(request, f"{made} user mesin dibuat (PIN = NIK) dan dipetakan. Belum dikirim ke mesin: pakai Upload user, lalu daftarkan sidik jari di mesin." + (f" Perhatian: {nonnum} PIN bukan angka; mesin FK hanya menerima PIN angka." if nonnum else ""))
        if skipped: messages.warning(request, f"{len(skipped)} dilewati: " + "; ".join(f"{n} ({why})" for n, why in skipped[:4]) + ("…" if len(skipped) > 4 else ""))
    elif act in ("check", "import"):
        f = request.FILES.get("file")
        if not f: raise ValueError("Pilih berkas CSV/XLSX (kolom pin, nik).")
        res = imp.import_pin_table(actor, f.read(tabular.MAX_BYTES + 1), f.name, act == "import")
        if act == "import" and res["ok"]:
            log(request, "hr", "attendance_pin_import", None, None, {"rows": res["total"], "created": res["created"]}); messages.success(request, f"{res['created']} PIN dipetakan; {res['linked']} scan lama terhubung.")
        return res
    else: raise ValueError("Aksi tidak dikenal.")
    return None


@hrd
def users_page(request):
    g, res = request.GET, None
    if request.method == "POST":
        try: res = _act(request)
        except ValueError as e: messages.error(request, str(e))
        except AttendancePin.DoesNotExist: messages.error(request, "PIN tidak ada di tabel pemetaan.")
        if res is None: return _back(request)
    rows, tabs, stat, f = fu.overview(g)
    if g.get("export") == "1":
        log(request, "hr", "attendance_device_user_export", None, None, {"tab": f["tab"], "rows": len(rows)})
        if f["tab"] == "nopin":
            data = [[e.nik, e.name, e.department.name] for e in fu.nopin_employees(f)]
            return tabular.export_response("karyawan-tanpa-pin", ["nik", "nama", "departemen"], data, request, sheet="Tanpa PIN")
        return tabular.export_response("user-fingerspot", fu.EXPORT_HEADER, fu.export_rows(rows), request, sheet="User Fingerspot", num_cols=(8,))
    keep = g.copy()
    for k in ("page", "export"): keep.pop(k, None)
    notab = g.copy()
    for k in ("page", "export", "tab"): notab.pop(k, None)
    deps = list(Department.objects.order_by("name")); sel = {str(d) for d in f["dept"]}
    dept_names = ([d.name for d in deps if str(d.pk) in sel]) + (["Belum dipetakan"] if fu.NO_DEPT in sel else [])
    ctx = {"tabs_def": fu.TABS, "tabs": tabs, "stat": stat, "f": f, "nfilter": fu.count_filters(f), "sorts": fu.SORTS, "res": res, "sources": AttendancePin.SOURCES,
           "departments": deps, "qs": keep.urlencode(), "back": keep.urlencode(), "qs_notab": notab.urlencode(), "no_dept": fu.NO_DEPT, "dept_sel": sel, "dept_names": dept_names}
    if f["tab"] == "nopin": ctx["page"] = Paginator(list(fu.nopin_employees(f)), PER_PAGE).get_page(g.get("page", 1))
    else:
        page = Paginator(rows, PER_PAGE).get_page(g.get("page", 1)); fu.scan_stats(list(page)); ctx["page"] = page
    return render(request, "attendance/pins.html", ctx)
