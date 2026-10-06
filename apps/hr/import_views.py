from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from . import importer


@login_required
@require_roles(Role.HRD)
def employee_import(request):
    res = None
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f: res = {"ok": False, "fatal": "Pilih file CSV.", "errors": [], "total": 0, "created": 0}
        else:
            commit = request.POST.get("mode") == "import"
            res = importer.run(f.read(importer.MAX_BYTES + 1), commit)
            res["commit"] = commit
            if commit and res["ok"]:  # audit: hanya ringkasan, tidak menyimpan isi baris (mengandung data sensitif)
                log(request, "hr", "employee_import", None, None, {"file": f.name[:100], "rows": res["total"], "created": res["created"]})
            elif commit: log(request, "hr", "employee_import_rejected", None, None, {"file": f.name[:100], "errors": len(res["errors"]) or 1})
    if res: res["errors_shown"] = res["errors"][:100]
    return render(request, "employee_import.html", {"res": res, "columns": importer.COLUMNS, "required": importer.REQUIRED})


@login_required
@require_roles(Role.HRD)
def import_template(request):
    r = HttpResponse(importer.template_csv(), content_type="text/csv; charset=utf-8")
    r["Content-Disposition"] = 'attachment; filename="template-karyawan.csv"'; return r
