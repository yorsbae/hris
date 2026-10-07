"""Form manajemen user (Superadmin). Sandi tidak pernah masuk audit/log; semua sandi lewat validator Django (min 10 karakter, bukan sandi umum)."""
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from apps.hr.models import Department
from .models import Role, User


class _RoleDeptMixin:
    """Aturan role↔departemen: Admin Departemen wajib punya departemen; role lain tidak boleh (agar tidak ada data menyesatkan)."""
    def clean(self):
        d = super().clean()
        role, dept = d.get("role"), d.get("department")
        if role == Role.DEPT_ADMIN and not dept: self.add_error("department", "Admin Departemen wajib punya departemen.")
        elif role and role != Role.DEPT_ADMIN and dept: self.add_error("department", "Departemen hanya untuk Admin Departemen; kosongkan untuk role ini.")
        return d


class UserEditForm(_RoleDeptMixin, forms.ModelForm):
    department = forms.ModelChoiceField(Department.objects.order_by("name"), required=False, label="Departemen", empty_label="—")
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "role", "department")
        labels = {"first_name": "Nama depan", "last_name": "Nama belakang", "email": "Email", "role": "Role"}

    def __init__(self, *a, lock_role=False, **k):
        super().__init__(*a, **k)
        self.fields["role"].choices = [(v, l) for v, l in Role.choices]  # tanpa opsi kosong
        self.fields["role"].required = True
        self.fields["first_name"].required = True
        self.lock_role = lock_role
        if lock_role:  # mengubah role/departemen diri sendiri ditolak (cegah terkunci/menaikkan hak sendiri)
            for f in ("role", "department"): self.fields[f].disabled = True


class UserCreateForm(UserEditForm):
    username = forms.CharField(max_length=150, label="Username")
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Sandi awal",
                                help_text="Min. 10 karakter. User wajib menggantinya saat login pertama.")
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Ulangi sandi awal")

    class Meta(UserEditForm.Meta):
        fields = ("username", "first_name", "last_name", "email", "role", "department")

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        if not all(c.isalnum() or c in "._-" for c in u): raise ValidationError("Hanya huruf, angka, titik, garis bawah, dan strip.")
        if User.objects.filter(username__iexact=u).exists(): raise ValidationError("Username sudah dipakai.")  # tanpa membedakan huruf besar/kecil
        return u

    def clean(self):
        d = super().clean()
        p1, p2 = d.get("password1"), d.get("password2")
        if p1 and p2 and p1 != p2: self.add_error("password2", "Sandi tidak sama.")
        elif p1:
            try: password_validation.validate_password(p1, User(username=d.get("username", ""), first_name=d.get("first_name", "")))
            except ValidationError as e: self.add_error("password1", e)
        return d


class ResetPasswordForm(forms.Form):
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Sandi sementara baru",
                                help_text="Min. 10 karakter. User wajib menggantinya saat login berikutnya; semua sesi lamanya diputus.")
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Ulangi sandi")

    def __init__(self, *a, user, **k): super().__init__(*a, **k); self.target = user

    def clean(self):
        d = super().clean()
        p1, p2 = d.get("password1"), d.get("password2")
        if p1 and p2 and p1 != p2: self.add_error("password2", "Sandi tidak sama.")
        elif p1:
            try: password_validation.validate_password(p1, self.target)
            except ValidationError as e: self.add_error("password1", e)
            if self.target.check_password(p1): self.add_error("password1", "Sandi baru harus berbeda dari sandi lama.")
        return d


class DeactivateForm(forms.Form):
    reason = forms.CharField(label="Alasan menonaktifkan", max_length=300, widget=forms.TextInput)  # wajib & dipangkas otomatis oleh CharField


class SelfPasswordChangeForm(PasswordChangeForm):
    """Ganti sandi sendiri. Sandi baru wajib berbeda dari yang lama (penting setelah sandi awal dibuatkan Superadmin)."""
    def clean_new_password1(self):
        p = self.cleaned_data["new_password1"]
        if self.user.check_password(p): raise ValidationError("Sandi baru harus berbeda dari sandi lama.")
        return p
