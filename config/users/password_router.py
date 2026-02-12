from django.conf import settings
from django.contrib.auth.hashers import get_hasher, identify_hasher

# Routes tenant based password hashing
def validate_password_strength(password, tenant):
    errors = []

    if len(password) < tenant.min_password_length:
        errors.append(f"Password must be at least {tenant.min_password_length} characters")

    if tenant.require_uppercase and not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter")

    if tenant.require_lowercase and not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter")

    if tenant.require_digits and not any(c.islower() for c in password):
        errors.append("Password must contain at least one digit")

    if tenant.require_special_chars:
        special_chars = "!@#$%^&*()_+-=[]{}|;:,.<>?/"
        if not any(c in special_chars for c in password):
            errors.append("Password must contain at least one special character")

    return errors


class TenantPasswordHasherRouter:
    def __init__(self):
        self._tenant = None

    #sets current tenant
    def set_tenant(self, tenant):
        self._tenant = tenant

    # Password hasher for specific tenant
    def get_hasher_for_tenant(self, tenant=None):
        if tenant is None:
            tenant = self._tenant

        if not tenant:
            algorithm = settings.PASSWORD_HASHERS[0].split('.')[-1].replace('PasswordHasher', '').lower()
            return get_hasher(algorithm)

        algorithm = getattr(tenant, 'password_hashing_algorithm', 'argon2')

        hasher = get_hasher(algorithm)

        # for tenant aware hasher, set tenant
        from users.hashers import TenantAwarePasswordHasher
        if isinstance(hasher, TenantAwarePasswordHasher):
            hasher.set_tenant(tenant)

        return hasher

    def make_password(self, password, tenant=None, salt=None):
        hasher = self.get_hasher_for_tenant(tenant)

        if tenant:
            errors = validate_password_strength(password, tenant)
            if errors:
                raise ValueError(f"Password does not meet policy: {', '.join(errors)}")

        return hasher.encode(password, salt=salt)

    # Verify hasher against password
    def verify_password(self, password, encoded, tenant=None):
        try:
            hasher = identify_hasher(encoded)

            from users.hashers import TenantAwarePasswordHasher
            if isinstance(hasher, TenantAwarePasswordHasher) and tenant:
                hasher.set_tenant(tenant)

            # Verify password
            if hasher.verify(password, encoded):
                return True

        except ValueError:
            hasher = self.get_hasher_for_tenant(tenant)

            try:
                if hasher.verify(password, encoded):
                    return True
            except (ValueError, TypeError):
                pass
        return False

    # Check if rehash needed
    def needs_rehash(self, encoded, tenant=None):
        if not tenant:
            return False

        hasher = self.get_hasher_for_tenant(tenant)

        if hasattr(hasher, 'must_update'):
            return hasher.must_update(encoded)

        return False

    def upgrade_hash(self, password, encoded, tenant=None):
        if not self.verify_password(password, encoded, tenant):
            raise ValueError('Invalid password')

        if self.needs_rehash(encoded, tenant):
            return self.make_password(password, tenant)

        return encoded


password_router = TenantPasswordHasherRouter()