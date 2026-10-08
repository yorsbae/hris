import re
from django.core.exceptions import ValidationError


class LetterAndDigitValidator:
    """Kebijakan sandi (putaran 21, P1): wajib memuat huruf DAN angka (selain panjang minimum, sandi umum, mirip username, serba angka dari Django)."""
    def validate(self, password, user=None):
        if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
            raise ValidationError("Sandi harus memuat huruf dan angka.", code="password_no_letter_digit")

    def get_help_text(self): return "Sandi harus memuat huruf dan angka."
