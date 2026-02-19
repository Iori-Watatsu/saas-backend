from uuid import uuid4
from wsgiref.validate import validator

from django.db import models
from django_tenants.models import TenantMixin, DomainMixin
import json
from django.core.validators import MinValueValidator, MaxLengthValidator, MaxValueValidator
from django.core.exceptions import ValidationError
import logging

logger = logging.getLogger(__name__)

# Create your models here.
class Tenant(TenantMixin):
    name: str
    password_expiry_days: int
    password_history_size: int
    lockout_duration_minutes: int

    # Use Universally Unique Identifiers for individual tenant's global uniqueness, ehanced security and data merging without id collisions.
    id = models.UUIDField(default=uuid4, unique=True, primary_key=True, editable=False)
    name = models.CharField(max_length=100, help_text="Company/organization name")

    # A subset of company_name for tenant routing, constrain tenants from using same subdomain by adding the unique att
    subdomain = models.CharField(max_length=100, unique= True, help_text="e.g., 'company.example.com")

    # Tenant type for database isolation levels
    TENANT_TYPES = [
        ('standard', 'Standard - Schema Isolation'),
        ('premium', 'Premium - Database Isolation'),
        ('enterprise', 'Enterprise - Database Isolation'),
    ]
    tenant_type = models.CharField(
        max_length=20,
        choices=TENANT_TYPES,
        default='standard',
        help_text="Database isolation level and resources"
    )

    # Database-per-tent = stores seperate database for premium tenants
    database_name = models.CharField(max_length=100, blank=True, null=True, help_text="Seperate database name for premium tenants")

    # Subscription Plans
    PLANS = [
        ('free', 'Free (Schema)'),
        ('starter', 'Starter (Schema)'),
        ('professional', 'Professional (Database)'),
        ('enterprise', 'Enterprise (Database)'),
    ]
    plan = models.CharField(
        max_length=20,
        choices=PLANS,
        default='free',
        help_text="Subscription plan determines features and limits"
        )

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('suspended', 'Suspended'),
        ('trial', 'Trial'),
        ('expired', 'Expired'),
    ] # Later research on and explore using django-tenants library
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='trial',
        help_text="Current status of the tenant"
    )

    max_users = models.IntegerField(default=5, validators=[MinValueValidator(1)],  help_text="Maximum number of users for this tenant") # Enforces plan limits
    max_projects = models.IntegerField(default=10, validators=[MinValueValidator(1)], help_text="Maximum number of projects per tenant") # Limits number of projects per tenant
    custom_domain = models.CharField(max_length=255, blank=True, null=True, help_text="Custom domain (instead of subdomain)") # Allows use of domain instead of subdomain

    # Automatically set the timestamp when the object is first created
    created_at = models.DateTimeField(auto_now_add=True)
    # Automatically update the timestamp every time the object is saved
    updated_at = models.DateTimeField(auto_now=True)

    # Tenant config to auto create schema for tenants
    auto_create_schema = models.BooleanField(default=True, help_text="Automatically create schema for this tenant")
    auto_drop_schema = models.BooleanField(default=False, help_text="Automatically drop schema when tenant is deleted")

    # Password hashing config
    PASSWORD_HASHING_ALGORITHMS = [
        ('argon2', 'Argon2 (Recommended)'),
        ('bcrypt', 'Bycrypt'),
        ('scrypt', 'Scrypt'),
        ('pbkdf2_sha256', 'PBKDF2 SHA256'),
        ('pbkdf_sha256_tenant', 'PBKDF2 SHA256 (Tenant-tuned)')
    ]
    password_hashing_algorithm = models.CharField(
        max_lenght = 50,
        choices=PASSWORD_HASHING_ALGORITHMS,
        default='argon2',
        help_text='Password hashing algorithm for this tenant'
    )
    password_hashing_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Algorithm-specific configuration parameters (JSON)"
    )

    # Policy Config
    min_password_length = models.IntegerField(
        default=12,
        validators=[MinValueValidator(16), MaxLengthValidator(128)],
        help_text="Minimum password length (16 Charactors)"
    )
    require_uppercase = models.BooleanField(
        default=True,
        help_text="Require at least one uppercase letter"
    )
    require_lowercase = models.BooleanField(
        default=True,
        help_text="Require at least one lowercase letter"
    )
    require_digits = models.BooleanField(
        default=True,
        help_text="require at least one digit"
    )
    require_special_chars = models.BooleanField(
        default=True,
        help_text="Require at least on special chararcter (!@#$%^&*)"
    )
    password_history_size = models.IntegerField(
        default=5,
        help_text="Number of previous passwords to remember (0 to disable)"
    )
    password_expiry_days = models.IntegerField(
        default=90,
        validators=[MinValueValidator(0), MaxLengthValidator(365)],
        help_text="Days before password expires (0 to disable)"
    )
    max_login_attemps = models.IntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Maximum failed login attemps before lockout"
    )
    lockout_duration_minutes = models.IntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
        help_text="Account lockout duration in minutes"
    )

    objects = models.Manager()

    class Meta:
        db_table = 'tenants'

        indexes = [
            models.Index(fields=['tenant_type', 'status']),
            models.Index(fields=['plan', 'status']),
            models.Index(fields=['subdomain'])
        ]

    def __str__(self):
        return f"{self.name} ({self.subdomain}) - {self.tenant_type}"

    # Password hash validation cofig
    def clean(self):
        from django.core.exceptions import ValidationError
        import logging

        logger = logging.getLogger(__name__)

        super().clean()

        config = self.password_hashing_config or {}

        # Validate based on selected algorithm
        if config:
            valid_keys = {
                'argon2': ['argon2_time_cost', 'argon2_memory_cost', 'argon2_parallelism',
                           'argon2_hash_length', 'argon2_salt_length', 'argon2_type'],
                'bcrypt': ['bcrypt_rounds'],
                'scrypt': ['scrypt_N', 'scrypt_r', 'scrypt_p', 'scrypt_key_length',
                           'scrypt_salt_length'],
                'pbkdf2_sha256_tenant': ['pbkdf2_iterations', 'pbkdf2_digest'],
            }

            allowed_keys = valid_keys.get(self.password_hashing_algorithm, [])
            invalid_keys = [k for k in config.keys() if k not in allowed_keys]

            if invalid_keys:
                raise ValidationError({
                    'password_hashing_config': f"Invalid configuration keys for {self.password_hashing_algorithm}: {', '.join(invalid_keys)}"
                })

        # Password policy consistency validation
        if self.password_expiry_days > 0 and self.password_history_size == 0:
            logger.warning(
                f"Tenant {self.name}: Password expiry is enabled but history is disabled. "
                "Users can reuse the same password."
            )

    def get_default_hashing_config(self):
        defaults = {
            'argon2': {
                'argon2_time_cost': 2,
                'argon2_memory_cost': 512,
                'argon2_parallelism': 2,
                'argon2_hash_length': 16,
                'argon2_salt_length': 16,
            },
            'bcrypt': {
                'bcrypt_rounds': 12,
            },
            'scrypt': {
                'scrypt_N': 16384,  # 2^14
                'scrypt_r': 8,
                'scrypt_p': 1,
                'scrypt_key_length': 32,
                'scrypt_salt_length': 16,
            },
            'pbkdf2_sha256_tenant': {
                'pbkdf2_iterations': 260000,
            },
        }
        return defaults.get(self.password_hashing_algorithm, {})

    @property
    def lockout_duration_display(self):
        minutes = self.lockout_duration_minutes

        if minutes >= 60:
            hours = minutes // 60
            remaining_minutes = minutes % 60

            if remaining_minutes:
                return f"{hours}h {remaining_minutes}m"
            return f"{hours}h"
        return f"{minutes}m"

    def save(self, *args, **kwargs):
        # Determine tenant type based on plan
        if self.plan in ['professional', 'enterprise']:
            self.tenant_type = 'premium'
            self.auto_create_schema = False # Doesn't create schema for DB tenants
        else:
            self.tenant_type = 'standard'
            self.auto_create_schema = True

        # Generate database for premium tenants & creates predictable database name from subdomain
        if self.tenant_type == 'premium' and not self.database_name:
            self.database_name = f"tenant_{self.subdomain}"

        super().save(*args, **kwargs)

    def get_database_config(self):
        # Return databse configuration for standard tenants
        from django.conf import settings

        if self.tenant_type == 'standard':
            return settings.DATABASES['default']
        else:
            return {
                'ENGINE':  'django_tenants.postgresql_backend',
                'NAME': self.database_name,
                'USER': settings.DATABASES['default']['USER'],
                'PASSWORD': settings.DATABASES['default']['PASSWORD'],
                'HOST': settings.DATABASES['default']['HOST'],
                'PORT': settings.DATABASES['default']['PORT'],
                'CONN_MAX_AGE': 600, # Cache database connections
            }

class Domain(DomainMixin):
    # Domains model for tenant URLs
    objects = None
    is_custom = models.BooleanField(default=False, help_text="Whether this is a custom domain vs subdomain")

    class Meta:
        db_table = 'tenant_domains'

    def __str__(self):
        domain_type = 'custom' if self.is_custom else 'subdomain'
        return f"{self.domain} ({domain_type})"