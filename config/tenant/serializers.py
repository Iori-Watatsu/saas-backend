from rest_framework import serializers
from .models import Tenant
from.models import Tenant, Domain

# Serializer for tenant modeladmin operations
class TenantSerializer(serializers.ModelSerializer):

    # Computed fields
    database_type = serializers.SerializerMethodField()
    user_count= serializers.SerializerMethodField()
    project_count = serializers.SerializerMethodField()
    domain = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = [
            'id',
            'name',
            'status',
            'subdomain',
            'tenant_type',
            'plan',
            'database_name',
            'max_users',
            'max_projects',
            'custom_domain',
            'created_at',
            'updated_at',
            # Computed fields
            'database_type',
            'user_count',
            'project_count',
            'domain'
        ]
        read_only_fields = [
            'id',
            'tenant_type',
            'database_name',
            'created_at',
            'updated_at',
            'database_type',
            'user_count',
            'project_count',
            'domain'
        ]

    @staticmethod
    def get_database_type(obj):
        if obj.tenant_type == 'premium':
            return 'Dedicated database'
        return 'Shared schema'

    @staticmethod
    def get_user_count(obj):
        try:
            from django_tenants.utils import tenant_context

            if obj.tenant_type == 'standard':
                with tenant_context(obj):
                    from users.models import User
                    return User.objects.filter(tenant=obj).count()
            else:
                return 'N/A'
        except:
            return 'Unkown'

    @staticmethod
    def get_projects_count(obj):
        try:
            from django_tenants.utils import tenant_context

            if obj.tenant_type == 'standard':
                with tenant_context(obj):
                    from project.models import Project
                    return Project.objects.filter(tenant=obj).count()
            else:
                return 'N/A'
        except:
            return 'Unknown'

    @staticmethod
    def get_domain(obj):
        try:
            primary_domain = Domain.objects.filter(
                tenant = obj,
                is_primary = True
            ).first()
            return primary_domain.doamin if primary_domain else None

        except:
            return None

    def validate_subdomain(self, value):
        import re

        value = value.lower()

        if len(value) < 3:
            raise serializers.ValidationError("Subdomain must be atleast 3 characters")
        if len(value) > 100:
            raise serializers.ValidationError("Subdomain cannot exceed 100 characters")

        if not re.match(r'^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$', value):
            raise serializers.ValidationError(
                "Subdomain can only contain lowercase letters, numbers, and hyphens. "
                "Cannot start or end with a hyphen."
            )

        reserved_subdomains = [
            'www', 'admin', 'api', 'app', 'dashboard', 'mail', 'blog',
            'support', 'help', 'status', 'dev', 'test', 'staging', 'prod'
        ]
        if value in reserved_subdomains:
            raise serializers.ValidationError(f"Subdomain '{value}' is reserved")

        # check for existings dubdomain
        instance = self.instance
        if instance:
            if Tenant.objects.exclude(id=instance.id).filter(subdomain=value).exists():
                raise serializers.ValidationError(f"Subdomain '{value}' is already taken")
        else:
            if Tenant.objects.filter(subdomain=value).exists():
                raise serializers.ValidationError(f"Subdomain '{value}' is already taken")

        return value

    # Handle tenant plan changes
    def update(self, instance, validated_data):
        old_plan = instance.plan
        new_plan = validated_data.get('plan', old_plan)

        instance = super().update(instance, validated_data)

        # Handle plan upgrade/downgrade
        if old_plan != new_plan:
            self.handle_plan_change(instance, old_plan, new_plan)

        return instance

    # Handle tenant plan change logic
    def handle_plan_change(self, tenant, old_plan, new_plan):
        from django.utils import timezone

        # Upgrade to premium
        if old_plan in ['free', 'starter'] and new_plan in ['professional', 'enterprise']:
            tenant.pla_change_date = timezone.now()
            tenant.plan_change_note = f"Upgraded from {old_plan} to {new_plan}"

            # Trigger async database creation (In production use celery or similar)
            self.trigger_database_creation(tenant)

        # Downgrade from premium
        elif old_plan in ['professional', 'enterprise'] and new_plan == 'starter':
            tenant.plan_change_date = timezone.now()
            tenant.plan_change_note = f"Downgraded from {old_plan} to {new_plan}"

            self.schedule_downgrade_migration(tenant, old_plan, new_plan)

        tenant.save()

    # A celery async database trigger task in production, this is logging just for dev
    @staticmethod
    def trigger_database_creation(tenant):
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Triggered tenant database creation {tenant.subdomain}")

    #Schedule database migration for schema downgrades
    @staticmethod
    def shcedule_downgrade_mirgration(tenant):
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Sheduling migration downgrade for{tenant.subdomain}")

# Domain Model Serializer
class DomainSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source='tenant.companyname', read_only=True)

    class Meta:
        model = Domain
        fields = [
            'id',
            'domain',
            'tenant',
            'tenant_name',
            'is_primary',
            'is_custom',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

        def __init__(self):
            self.instance = None

        def validate_domain(self, value):
            import re

            if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$', value):
                raise serializers.ValidationError("Enter a valid domain name")

            # Checkdomain registration
            instance = self.instance
            if instance:
                if Domain.objects.exlude(id=instance.id).filter(domain=value).exists():
                    raise serializers.ValidationError(f"Domain '{value}' aldready registered")
            else:
                if Domain.objects.filter(domain=value).exists():
                    raise serializers.ValidationError(f"Domain '{value}' already registered")

            return value.lower()