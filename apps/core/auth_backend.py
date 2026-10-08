from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from . import lockout

UserModel = get_user_model()


class LockoutBackend(ModelBackend):
    """ModelBackend + penguncian per username (lihat apps/core/lockout.py). Berlaku juga untuk login /admin/ karena lewat authenticate()."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None: username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None: return None
        try: user = UserModel._default_manager.get_by_natural_key(username)
        except UserModel.DoesNotExist:
            UserModel().set_password(password)  # samakan waktu respons dengan username yang ada
            return None
        if lockout.is_locked(user):
            UserModel().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            lockout.clear(user)
            return user
        if user.is_active and not user.check_password(password): lockout.record_failure(user, request)
        return None
