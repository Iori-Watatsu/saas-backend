from datetime import timedelta
from uuid import uuid4
from django.contrib.auth.models import BaseUserManager, AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Create your models here.

# User manager
class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The email field must be set')

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    # Create superuser
    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True')

        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True')

        return self.create_user(email, password, **extra_fields)

class CustomUser(AbstractUser):
    username = None # Uses email as identifier
    id = models.UUIDField(
        default=uuid4,
        unique=True,
        primary_key=True,
        editable=False,
        verbose_name=_('ID'),
    )
    tenant = models.ForeignKey(
        'tenant.Tenant', # Created seperately
        on_delete=models.CASCADE,
        related_name='users',
        verbose_name=_('Tenant'),
        help_text=_('The tenant/organization this user belongs to')
    )
    email = models.EmailField(
        _('email address'),
        max_length=255,
        unique=False,
        blank=False,
        null=False,
        help_text=_('User email address (used for login)')
    )
    first_name = models.CharField(
        _('first name'),
        max_length=100,
        blank=False,
        null=False,
        help_text=_('User first name')
    )
    last_name = models.CharField(
        _('last name'),
        max_length=100,
        blank=False,
        null=False,
        help_text=_('User last name')
    )
    ROLE_CHOICES = [
        ('owner', _('Owner')),
        ('admin', _('Administrator')),
        ('manager', _('Manager')),
        ('member', _('Member')),
        ('viewer', _('Viewer')),
        ('guest', _('Guest')),
    ]
    role = models.CharField(
        _('role'),
        max_length=50,
        choices=ROLE_CHOICES,
        default='member',
        blank=False,
        null=False,
        help_text=_('User role within the tenant/organization')
    )
    STATUS_CHOICES = [
        ('active', _('Active')),
        ('inactive', _('Inactive')),
        ('suspended', _('Suspended')),
        ('invited', _('Invited')),
        ('pending', _('Pending Approval')),
    ]
    status = models.CharField(
        _('status'),
        max_length=50,
        choices=STATUS_CHOICES,
        default='active',
        blank=False,
        null=False,
        help_text=_('User account status')
    )
    phone = models.CharField(
        _('phone number'),
        max_length=20,
        blank=True,
        null=True,
        help_text=_('User phone number')
    )
    title = models.CharField(
        _('job title'),
        max_length=100,
        blank=True,
        null=True,
        help_text=_('User job title')
    )
    department = models.CharField(
        _('department'),
        max_length=100,
        blank=True,
        null=True,
        help_text=_('User department')
    )
    email_verified = models.BooleanField(
        _('email verified'),
        default=False,
        help_text=_('Whether the user has verified their email')
    )
    last_activity = models.DateTimeField(
        _('last activity'),
        auto_now=True,
        help_text=_('Last user activity timestamp')
    )
    login_attempts = models.IntegerField(
        _('login attempts'),
        default=0,
        help_text=_('Number of failed login attempts')
    )
    locked_until = models.DateTimeField(
        _('locked until'),
        blank=True,
        null=True,
        help_text=_('Account locked until this time')
    )
    password_history = models.JSONField(
        _('password history'),
        default=list,
        blank=True,
        help_text=_('List of previous password hashes')
    )
    password_changed_at = models.DateTimeField(
        _('password changed at'),
        null=True,
        blank=True,
        help_text=_('When the password was last changed')
    )
    password_expires_at = models.DateTimeField(
        _('password expires at'),
        null=True,
        blank=True,
        help_text=_('When the password expires')
    )
    force_password_change = models.BooleanField(
        _('force password change'),
        default=False,
        help_text=_('User must change password on next login')
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name', 'tenant']

    objects = CustomUserManager()

    class Meta:
        db_table = 'users'
        verbose_name = _('User')
        verbose_naem_plural = _('Users')

        # These contraints allow for same email in defferent tenants
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'email'],
                name='unique_email_per_tenant'
            )
        ]
        indexes = [
            models.Index(fields=['tenant', 'email']),
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'role']),
            models.Index(fields=['email']),
            models.Index(fields=['date_joined']),
        ]

    def __str__(self):
        return self.email # Unique identifier

    def add_to_password_history(self, password_hash):
        if not self.tenant:
            return

        history_size = self.tenant.password_history_size

        if history_size <= 0:
            self.password_history = []
            return

        history = list(self.password_history or [])

        if password_hash in history:
            history.remove(password_hash)

        history.insert(0, password_hash)
        self.password_history = history[:history_size]

    def is_password_in_history(self, raw_password):
        if not self.password_history or not self.tenant:
            return False

        if self.tenant.password_history_size <= 0:
            return False

        from .password_router import password_router
        password_router.set_tenant(self.tenant)

        for old_hash in self.password_history:
            try:
                if password_router.verify_password(raw_password, old_hash, self.tenant):
                    return True

            except (ValueError, TypeError):
                continue
        return False

    def set_password(self, raw_password):
        from .password_router import password_router

        if self.pk and self.is_password_in_history(raw_password):
            history_size = self.tenant.password_history_size
            raise ValueError(
                f"Password was used recently. Choose a different password "
                f"(last {history_size} passwords are blocked)."
            )

        password_router.set_tenant(self.tenant)
        new_password_hash = password_router.make_password(raw_password, self.tenant)

        if self.pk and self.password:
            self.add_to_password_history(self.password)

        self.password = new_password_hash

        self.password_changed_at = timezone.now()
        self.force_password_change = False

        if self.tenant.password_expiry_days > 0:
            self.password_expires_at = (
                self.password_changed_at +
                timedelta(days=self.tenant.password_expiry_days)
            )
        else:
            self.password_expires_at = None

    # Check password expiration
    def is_password_expired(self):
        if not self.password_changed_at:
            return False

        return timezone.now() > self.password_expires_at

    # Calculate password strength
    def get_password_strength_score(self, password=None):
        if password is None:
            password = self.password

        score = 0

        # Length score
        length = len(password)
        if length >= 8:
            score += 10
        if length >= 12:
            score += 10
        if length >= 16:
            score += 10
        if length >= 20:
            score += 10

        # Character variety score
        if any(c.isupper() for c in password):
            score += 10
        if any(c.islower() for c in password):
            score += 10
        if any(c.isdigit() for c in password):
            score += 10
        if any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?`~' for c in password):
            score += 10

        # Entripy score
        charset_size = 0
        if any(c.islower() for c in password):
            charset_size += 26
        if any(c.isupper() for c in password):
            charset_size += 26
        if any(c.isdigit() for c in password):
            charset_size += 10
        if any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?`~' for c in password):
            charset_size += 32

        import math
        if charset_size > 0:
            entropy = length * math.log2(charset_size)
            if entropy >= 64:  # Very strong
                score += 20
            elif entropy >= 48:  # Strong
                score += 15
            elif entropy >= 32:  # Moderate
                score += 10
            elif entropy >= 28:  # Weak
                score += 5

        return min(100, score)

    # Human readable password strength
    def get_password_strength_label(self, password=None):
        score = self.get_password_strength_score(password)

        if score >= 80:
            return 'Very Strong'
        elif score >= 60:
            return 'Strong'
        elif score >= 40:
            return 'Moderate'
        elif score >= 20:
            return 'Weak'
        else:
            return 'Very Weak'

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() # Return suser full name

    @property
    def is_tenant_owner(self):
        return self.role == 'owner' # Checks if tenant is owner

    @property
    def is_tenant_admin(self):
        return self.role in ['owner', 'admin'] # Checks admin privileges

    def can_access_tenant(self, tenant):
        return self.tenant_id == tenant.id # Checks user acces to specif tenants

    # Reset failed login attempts
    def reset_login_attemtps(self):
        self.login_attempts = 0
        self.locked_until = None
        self.save(update_fields=['login_attempts', 'locked_until'])

    # Incerement failed attemps
    def increment_login_attempts(self):
        self.login_attempts += 1

        # Locked after 5 failed attempts for 30 mins
        if self.login_attempts >= 5:
            from django.utils import timezone
            self.locked_until = timezone.now() + timezone.timedelta(minutes=30)

            self.save(update_fields=['login_attempts', 'locked_until'])

    # Check is account is locked
    def is_account_locked(self):
        if self.locked_until:
            from django.utils import timezone
            return timezone.now() < self.locked_until
        return False

    # Role based user permmisions:
    def get_permissions(self):
        permissions = {
            'owner': {
                'can_manage_users': True,
                'can_manage_tenant': True,
                'can_manage_projects': True,
                'can_manage_tasks': True,
                'can_view_analytics': True,
                'can_export_data': True,
                'can_manage_billing': True,
            },
            'admin': {
                'can_manage_users': True,
                'can_manage_tenant': False,
                'can_manage_projects': True,
                'can_manage_tasks': True,
                'can_view_analytics': True,
                'can_export_data': True,
                'can_manage_billing': False,
            },
            'manager': {
                'can_manage_users': False,
                'can_manage_tenant': False,
                'can_manage_projects': True,
                'can_manage_tasks': True,
                'can_view_analytics': True,
                'can_export_data': False,
                'can_manage_billing': False,
            },
            'member': {
                'can_manage_users': False,
                'can_manage_tenant': False,
                'can_manage_projects': False,
                'can_manage_tasks': True,
                'can_view_analytics': False,
                'can_export_data': False,
                'can_manage_billing': False,
            },
            'viewer': {
                'can_manage_users': False,
                'can_manage_tenant': False,
                'can_manage_projects': False,
                'can_manage_tasks': False,
                'can_view_analytics': False,
                'can_export_data': False,
                'can_manage_billing': False,
            },
            'guest': {
                'can_manage_users': False,
                'can_manage_tenant': False,
                'can_manage_projects': False,
                'can_manage_tasks': False,
                'can_view_analytics': False,
                'can_export_data': False,
                'can_manage_billing': False,
            },
        }

        return permissions.get(self.role, {})

class TwoFactorAuth(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='two_factor_auth')
    secret = models.CharField(max_length=32)
    backup_codes = models.JSONField(default=list)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        db_table = 'two_factor_auth'