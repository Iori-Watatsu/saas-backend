from django.db import models
from uuid import uuid4
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

# Create your models here.

# Automatic tenant filter queryset
class TenantAwareQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)

class Project(models.Model):
    id = models.UUIDField(
        default=uuid4,
        unique=True,
        primary_key=True,
        editable=False,
        verbose_name=_('ID')
    )

    tenant = models.ForeignKey(
        'tenant.Tenant', # Created seperately
        on_delete=models.CASCADE,
        related_name='projects',
        verbose_name='Tenant',
        help_text='The tenant/organization this user belongs to'
    )

    owner = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.SET_NULL, null=True,
        related_name='owned_projects',
        verbose_name=_('Owner'),
        help_text=_('User who created/owns created the project')
    )

    created_by = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_projects',
        verbose_name=_('Created By'),
        help_text=_('User who created this projects (for audit trail)')
    )

    members = models.ManyToManyField(
        'users.CustomUser',
        through='ProjectMember',
        blank=True,
        help_text=_('Team members with access to this project')
    )

    name = models.CharField(
        max_length=100,
        unique=False,
        null=False,
        verbose_name=_('Project Name'),
        help_text=_('Display name of the project')
    )

    slug = models.SlugField(
        max_length=100,
        unique=True,
        verbose_name=_('Project Slug'),
        help_text=_('URL-friendly projects identifier (auto-generated)')
    )

    description = models.TextField(
        max_length=500,
        blank=True,
        null=False,
        default='',
        verbose_name=_('Description'),
        help_text=_('Detailed description of the project')
    )

    OPERATIONAL_STATUS_CHOICES = [
        ('active', _('Actice')),
        ('archived', _('Archived')),
        ('deleted', _('Deleted'))
    ]
    operational_status = models.CharField(
        max_length=20,
        choices=OPERATIONAL_STATUS_CHOICES,
        default='active',
        verbose_name=_('Operational Status'),
        help_text=_('Whether the project is active or irchived')
    )

    SUBSCRIPTION_STATUS_CHOICES = [
        ('active', _('Active')),
        ('trial', _('Trial')),
        ('suspended', _('Suspended')),
        ('expired', _('Expired')),
        ('cancelled', _('Cancelled')),
    ]
    subscription_status = models.CharField(
        max_length=20,
        choices=SUBSCRIPTION_STATUS_CHOICES,
        default='trial',
        verbose_name=_('Subscription Status'),
        help_text=_('Current subscription status of the project')
    )

    max_team_members = models.IntegerField(
        default=10,
        verbose_name=_('Max Team Members'),
        help_text=_('Maximum numbers of team members allowed')
    )

    max_storage_gb = models.IntegerField(
        default=100,
        verbose_name=_('Max Storage (GB)'),
        help_text=_('Maximum storage in gigabytes')
    )

    max_api_calls_monthly = models.IntegerField(
        default=1000,
        verbose_name=_('Max Monthly API Calls'),
        help_text=_('Maximum API calls allowed per month')
    )

    trial_ends_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name=_('Trial Ends At'),
        help_text=_('When trial period expires')
    )

    # Automatically set the timestamp when the object is first created
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Created At'),
        help_text=_('When the project was created')
    )
    # Automatically update the timestamp every time the object is saved
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Updated AT'),
        help_text=_('When the project was last updated')
    )

    objects = TenantAwareQuerySet.as_manager()

    class Meta:
        db_table = 'projects'
        verbose_name = _('Project')
        verbose_name_plural = _('Projects')

        # prevent duplicate projects in the same tenant
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'slug'],
                name='unique_project_slug_per_tenant'
            )
        ]

        # Indexex for common queries
        indexes = [
            models.Index(
                fields=['tenant', 'operational_status'],
                name='idx_tenant_operational_status'
            ),
            models.Index(
                fields=['tenant', 'subscription_status'],
                name='idx_tenant_subscription_status'
            ),
            models.Index(
                fields=['tenant', 'owner'],
                name='idx_tenant_owner'
            ),
            models.Index(
                fields=['tenant', '-created_at'],
                name='idx_tenant_created_at'
            ),
            models.Index(
                fields=['slug'],
                name='idx_slug'
            ),
        ]

        permissions = [
            ('can_manage_project', 'Can manage project settings'),
            ('can_manage_members', 'Can manage project members'),
            ('can_view_analytics', 'Can view project analytics'),
            ('can_export_data', 'Can export project data'),
        ]

    def __str__(self):
        return self.name

    # Custom save with tenant isolation and slug generation
    def save(self, *args, **kwargs):
        # Prevent tenant changes (data isolation)
        if self.pk:
            original = Project.objects.get(pk=self.pk)
            if original.tenant_id != self.tenant_id:
                raise ValidationError('Cannot change project tenant')

        # Auto-generate slug if not provided
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1

            # Ensure slug uniqueness within tenant
            while Project.objects.filter(
                tenant=self.tenant,
                slug=slug
            ).exclude(pk=self.pk).exists():
                slug = f'{base_slug}-{counter}'
                counter += 1

            self.slug = slug

        super().save(*args, **kwargs)

    def get_current_team_member_count(self):
        return self.members.count()

    def can_add_team_member(self):
        return self.get_current_team_member_count() < self.max_team_members

    def get_usage_percentage(self, resource_type):
        usage = self.projectusage_set.first()

        if not usage:
            return 0

        if resource_type == 'storage':
            return min(100, int((usage.storage_used_gb / self.max_storage_gb) * 100))
        elif resource_type == 'api_calls':
            return min(100, int((usage.api_calls_used / self.max_api_calls_monthly) * 100))
        elif resource_type == 'team':
            return min(100, int((usage.team_members_count / self.max_team_members) * 100))

        return 0 

class ProjectMember(models.Model):
    id = models.UUIDField(
        default=uuid4,
        unique=True,
        primary_key=True,
        editable=False,
        verbose_name=_('ID')
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='members_relation',
        verbose_name=_('Project'),
        help_text=_('The project this member belongs to')
    )

    user = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.CASCADE,
        related_name='project_memberships',
        verbose_name=_('User'),
        help_text=_('The user who is member of the project')
    )

    ROLE_CHOICES = [
        ('owner', _('Owner')),
        ('admin', _('Admin')),
        ('editor', _('Editor')),
        ('viewer', _('Viewer')),
    ]
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='viewer',
        verbose_name=_('Role'),
        help_text=_('User role within the project')
    )

    invited_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Invited At')
    )

    joined_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name=_('Joined At'),
        help_text=_('When the user accepted the invitation')
    )

    class Meta:
        db_table = 'projects_members'
        verbose_name = _('Project Member')
        verbose_name_plural = _('Project Members')

        constraints = [
            models.UniqueConstraint(
                fields=['projects', 'user'],
                name='unique_project_user_membership'
            )
        ]

        indexes = [
            models.Index(fields=['project', 'role']),
            models.Index(fields=['user', 'role'])
        ]

    def __str__(self):
        return f'{self.user.email} - {self.project.name} ({self.role})'

class ProjectUsage(models.Model):
    id = models.UUIDField(
        default=uuid4,
        unique=True,
        primary_key=True,
        editable=False,
        verbose_name=_('ID')
    )

    api_calls_used = models.IntegerField(
        default=0,
        verbose_name=_('API Calls Used'),
        help_text=_('Number of API called used this period')
    )

    storage_used_gb = models.IntegerField(
        default=0,
        verbose_name=_('Storage Used (GB)'),
        help_text=_('Storage used in gigabytes')
    )

    team_members_count = models.IntegerField(
        default=0,
        verbose_name=_('Team Members Count'),
        help_text=_('Current number of active team members')
    )

    reset_date = models.DateField(
        verbose_name=_('Reset Date'),
        help_text=_('When the usage metrics reset (e.g., monthly)')
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Updated At')
    )

    class Meta:
        db_table = 'project_usage'
        verbose_name = _('Project Usage')
        verbose_name_plural = _('Project Usage')

        constraints =[
            models.UniqueConstraint(
                fields=['projects', 'reset_date'],
                name='unique_usage_per_reset_period'
            )
        ]

        indexes = [
            models.Index(fields=['project', 'reset_date'])
        ]

        def __str__(self):
            return f'{self.project.name} - Usage for {self.reset_date}'

class ProjectAuditLog(models.Model):
    id = models.UUIDField(
        default=uuid4,
        unique=True,
        primary_key=True,
        editable=False,
        verbose_name=_('ID')
    )

    tenant = models.ForeignKey(
        'tenant.Tenant',
        on_delete=models.CASCADE,
        related_name='project_audit_logs',
        verbose_name=_('Tenant'),
        help_text=_('The tenant this log belongs to')
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='audit_logs',
        verbose_name=_('Project'),
        help_text=_('The project that was accessed/modified')
    )

    user = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_audit_logs',
        verbose_name=_('User'),
        help_text=_('User who performed the action')
    )

    ACTION_CHOICES = [
        ('created', _('Created')),
        ('updated', _('Updated')),
        ('deleted', _('Deleted')),
        ('archived', _('Archived')),
        ('restored', _('Restored')),
        ('member_added', _('Member Added')),
        ('member_removed', _('Member Removed')),
        ('member_role_changed', _('Member Role Changed')),
        ('settings_updated', _('Settings Updated')),
        ('data_exported', _('Data Exported')),
        ('accessed', _('Accessed')),
    ]
    action = models.CharField(
        max_length=50,
        choices=ACTION_CHOICES,
        verbose_name=_('Action'),
        help_text=_('What action was performed')
    )

    details = models.JSONField(
        blank=True,
        null=True,
        default=dict,
        verbose_name=_('Details'),
        help_text=_('Addtional context (e.g., what fields changed)')
    )

    ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        verbose_name=_('IP Address'),
        help_text=_('IP address of the request')
    )

    user_agent = models.TextField(
        blank=True,
        default='',
        verbose_name=_('User Agent'),
        help_text=_('Browser/client information')
    )

    timestamp = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Timestamp'),
        help_text=_('When the action occurred')
    )

    class Meta:
        db_table = 'project_audit_logs'
        verbose_name = _('Project Audit Log')
        verbose_name_plural = _('Project Audit Logs')

        indexes = [
            models.Index(fields=['tenant', 'project', '-timestamp']),
            models.Index(fields=['tenant', 'user', '-timestamp']),
            models.Index(fields=['tenant', 'action', '-timestamp']),
            models.Index(fields=['-timestamp'])
        ]

        # Keeps logs ordered by most recent first
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action} - {self.project.name} by {self.user.email if self.user else 'System'}"