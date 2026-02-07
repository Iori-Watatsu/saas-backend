from uuid import uuid4
from django.db import models
from django_tenants.models import TenantMixin, DomainMixin

# Create your models here.
class Tenant(TenantMixin):
    objects = models.Manager
    # Use Universally Unique Identifiers for individual tenant's global uniqueness, ehanced security and data merging without id collisions.
    id = models.UUIDField(default=uuid4, unique=True, primary_key=True, editable=False)
    name = models.CharField(max_length=100)

    # A subset of company_name for tenant routing, constrain tenants from using same subdomain by adding the unique att
    subdomain = models.CharField(max_length=100, unique= True)

    # Tenant type for database isolation levels
    TENANT_TYPES = [
        ('standard', 'Standard - Schema Isolation'),
        ('premium', 'Premium - Database Isolation'),
        ('enterprise', 'Enterprise - Database Isolation'),
    ]
    tenant_type = models.CharField(
        max_length=20,
        choices=TENANT_TYPES,
        default='standard'
    )

    # Database-per-tent = stores seperate database for premium tenants
    database_name = models.CharField(max_length=100, blank=True, null=True)

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
        default='free'
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
        default='trial'
    )

    max_users = models.IntegerField(default=5) # Enforces plan limits
    max_projects = models.IntegerField(default=10) # Limits number of projects per tenant
    custom_domain = models.CharField(max_length=255, blank=True, null=True) # Allows use of domain instead of subdomain

    # Automatically set the timestamp when the object is first created
    created_at = models.DateTimeField(auto_now_add=True)
    # Automatically update the timestamp every time the object is saved
    updated_at = models.DateTimeField(auto_now=True)

    # Tenant config to auto create schema for tenants
    auto_create_schema = models.BooleanField(default=True)
    auto_drop_schema = models.BooleanField(default=False)

    class Meta:
        db_table = 'tenants'

        indexes = [
            models.Index(fields=['tenant_type', 'status']),
            models.Index(fields=['plan', 'status'])
        ]

    def __str__(self):
        return f"{self.name} ({self.subdomain}) - {self.tenant_type}"

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
    is_custom = models.BooleanField(default=False)

    class Meta:
        db_table = 'tenat_domains'

    def __str__(self):
        return f"{self.domain} ({'custom' if self.is_custom else  'subdomain'})"