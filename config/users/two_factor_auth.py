import pyotp
import qrcode
import secrets
import hashlib
import logging
from io import BytesIO
from django.core.cache import cache
from django.utils import timezone

from .authentication import TenantAuthenticationBackend

logger = logging.getLogger(__name__)

class TwoFactorAuthenticationBackend(TenantAuthenticationBackend):
    def authenticate_2fa(self, request, username, password, token):
        user = super().authenticate(request, username=username, password=password)

        if not user:
            logger.debug(f'Primary authentication failed for {username}')
            return None

        if not getattr(user, 'two_factor_enabled', False):
            logger.debug(f'2FA not enabled for user {user.email}, skipping')
            return user

        if self._validate_2fa_token(user, token):
            logger.info(f'2FA authentication successful for user {user.email}')
            return user

        logger.warning(f"Invalid 2FA token for user {user.email}")
        return None

    def _validate_2fa_token(self, user, token):
        if not hasattr(user, 'two_factor_secret') or not user.two_factor_secret:
            logger.warning(f'No 2FA secret found for user {user.email}')
            return False

        if self._verify_backup_code(user, token):
            logger.info(f'User {user.email} authenticated with backup code')
            return True

        try:
            totp = pyotp.TOTP(user.two_factor_secret)
            # valid_window=1 allows tokens from previous and next 30-second window
            is_valid = totp.verify(token, valid_window=1)

            if not is_valid:
                logger.debug(f'Invalid TOTP token for user {user.email}')

            return is_valid

        except Exception as e:
            logger.error(f'Error validating 2FA token for user {user.email}: {str(e)}')
            return False

    def setup_2fa(self, user, issuer_name=None):
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)

        try:
            if issuer_name is None:
                tenant = getattr(user, 'tenant', None)
                issuer_name = (tenant.name if tenant else "Saas Platform")

            provisioning_uri = totp.provisioning_uri(name=user.email, issuer_name=issuer_name)

            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4
            )
            qr.add_data(provisioning_uri)
            qr.make(fit=True)

            img = qr.make_image(fill_color='black', back_color='white')
            buffer = BytesIO()
            img.save(buffer, 'PNG')
            qr_code = buffer.getvalue()

            cache_key = f'2fa_setup_{user.id}_{timezone.now().timestamp()}'
            cache.set(cache_key, secret, timeout=300) # 5min

            logger.info(f'2FA setup initiated for user {user.email}')

            return {
                'secret': secret,
                'provisioning_uri': provisioning_uri,
                'qr_code': qr_code,
                'cache_key': cache_key
            }

        except Exception as e:
            logger.error(f"Error setting up 2FA for {user.email}: {str(e)}")
            return None

    def verify_2fa_setup(self, user, token, cache_key):
        secret = cache.get(cache_key)

        if not secret:
            logger.warning(f"2FA setup cache expired or invalid for user {user.email}")
            return False

        try:
            totp = pyotp.TOTP(secret)
            if totp.verify(token, valid_window=1):
                # Save secret to user
                user.two_factor_secret = secret
                user.two_factor_enabled = True
                user.two_factor_enabled_at = timezone.now()
                user.save(update_fields=[
                    'two_factor_secret',
                    'two_factor_enabled',
                    'two_factor_enabled_at'
                ])

                #clear cache
                cache.delete(cache_key)

                logger.info(f'2FA successfully enabled for user {user.email}')
                return True
            else:
                logger.warning(f"Invalid token during 2FA setup for {user.email}")

        except Exception as e:
            logger.error(f"Error verifying 2FA setup for {user.email}: {str(e)}")

        return False

    def disable_2fa(self, user, password=None, backup_code=None):
        verified = False

        if password:
            from django.contrib.auth.hashers import check_password
            verified = check_password(password, user.password)
        elif backup_code:
            verified = self._verify_backup_code(user, backup_code)

        if not verified:
            logger.warning(f"Failed to verify identity for 2FA disable for {user.email}")
            return False

        try:
            user.two_factor_enabled = False
            user.two_factor_secret = None
            user.backup_codes = None
            user.save(update_fields=[
                'two_factor_enabled',
                'two_factor_secret',
                'backup_codes'
            ])

            logger.info(f"2FA disabled for user {user.email}")
            return True

        except Exception as e:
            logger.error(f"Error disabling 2FA for {user.email}: {str(e)}")
            return False

    def _verify_backup_code(self, user, backup_code):
        if not hasattr(user, 'backup_code') or not user.backup_codes:
            return False

        try:
            code_hash = self._hash_backup_code(backup_code)
            stored_codes = user.backup_codes

            if isinstance(stored_codes, str):
                import json
                stored_codes = json.loads(stored_codes)

            if code_hash in stored_codes:
                stored_codes.remove(code_hash)
                user.backup_codes = stored_codes
                user.save(update_fields=['backup_codes'])

                logger.info(f'Backup code used for user {user.email}')
                return True

        except Exception as e:
            logger.error(f'Error verifying backup code for {user.email}: {str(e)}')

        return False

    def generate_backup_codes(self, user, count=10):
        try:
            backup_codes = []
            hashed_codes = []

            for _ in range(count):
                # generate 8 char code
                code = secrets.token_hex(4).upper()
                backup_codes.append(code)
                hashed_codes.append(self._hash_backup_code(code))

            user.backup_codes = hashed_codes
            user.save(update_fields=['backup_codes'])

            logger.info(f'Generated {count} backup codes for user {user.email}')

            return backup_codes

        except Exception as e:
            logger.error(f'Error generating backup codes for user {user.email}: {str(e)}')
            return None

    def _hash_backup_code(self, code):
        return hashlib.sha256(code.encode()).hexdigest()

    def get_backup_codes_count(self, user):
        if not hasattr(user, 'backup_codes') or not user.backup_codes:
            return 0

        try:
            stored_codes = user.backup_codes

            if isinstance(stored_codes, str):
                import json
                stored_codes = json.loads(stored_codes)
            return len(stored_codes)

        except Exception:
            return 0