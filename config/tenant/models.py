from django.db import models
from uuid import uuid4

# Create your models here.
class Tenant(models.Model):
    # Use Universally Unique Identifiers for individual tenant's global uniqueness, ehanced security and data merging without id collisions.
    id = models.UUIDField(default=uuid4, unique=True, primary_key=True, editable=False)
    company_name = models.CharField(max_length=100)

    # A subset of company_name for tenant routing, constrain tenants from using same subdomain by adding the unique att
    subdomain = models.CharField(max_length=100, unique= True)

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('suspended', 'Suspended'),
    ] # Later research on and explore using django-tenants library
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='active'
    )

    # Automatically set the timestamp when the object is first created
    created_at = models.DateTimeField(auto_now_add=True)
    # Automatically update the timestamp every time the object is saved
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.subdomain



