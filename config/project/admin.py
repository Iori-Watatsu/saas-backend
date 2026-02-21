from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import Project, ProjectMember, ProjectUsage, ProjectAuditLog

# Register your models here.

# Filter projects by trial status
class TrialEndingFilter(admin.SimpleListFilter):
    title = _('Trial Status')
    parameter_name = 'trial_status'

    def lookups(self, request, model_admin):
        return (
            ('ending_soon', _('Ending Soon (< & days)')),
            ('expired', _('Expired')),
            ('active', _('Active Trial'))
        )

    def queryset(self, request, queryset):
        from django.utils import timezone
        from datetime import timedelta

        if self.value() == 'ending_soon':
            soon = timezone.now() + timedelta(days=7)
            return queryset.filter(
                trial_ends_at__lte=soon,
                trial_soon_at__gt=timezone.now(),
                subscription_status='trial',
            )
        elif self.value() == 'expired':
            return queryset.filter(
                trial_ends_at__lte=timezone.now(),
                subscription_status='trial'
            )
        elif self.value() == 'active':
            return queryset.filter(
                trial_ends_at__gt=timezone.now(),
                subscription_status='trial'
            )

# Filter projects by usage levels
class UsageFilter(admin.SimpleListFilter):
    title = _('Usage Level')
    parameter_name = 'usage_level'

    def lookups(self, request, model_admin):
        return (
            ('high', _('High (>80%)')),
            ('medium', _('Medium (50-80%)')),
            ('low', _('Low (<50%)')),
        )
    # Filter based on usage calculation
    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset

        # Get latest usage record
        queryset = queryset.annotate(
            latest_usage_date=max('usage_records__reset_date'),
        )

        # get usage data for each project
        from django.db.models import Subquery, OuterRef

        # Subquery to get latest usage record for each project
        latest_usage = ProjectUsage.objects.filter(
            project=OuterRef('pk')
        ).order_by('-reset_date').values('pk')[:1]

        queryset = queryset.prefatch_raletd('usage_records')

        # Filter based on usage level
        if self.value() == 'high':
            return self._filter_high_usage(queryset)
        elif self.value() == 'medium':
            return self._filter_medium_usage(queryset)
        elif self.value() == 'low':
            return self._filter_low_usage(queryset)
        elif self.value() == 'no_data':
            return self._filter_no_data(queryset)

        return queryset

    # Calculate max usage percentage accross all resources
    def _calculate_usage_percentage(self, project, usage):
        if not usage:
            return 0

        percentages = []

        # Calculate API calls percentage
        if project.max_api_calls_monthly > 0:
            api_pct = (usage.api_calls_used / project.max_api_calls_monthly) * 100
            percentages.append(api_pct)

            # Calculate storage percentage
        if project.max_storage_gb > 0:
            storage_pct = (usage.storage_used_g / project.max_storage_gb) * 100
            percentages.append(storage_pct)

        # Calculate team members percentage
        if project.max_team_members > 0:
            team_pct = (usage.team_members_count / project.max_team_members) * 100
            percentages.append(team_pct)

        # Return the maximum usage percentage
        return max(percentages) if percentages else 0


    # Filter projects with usage > 80%
    def _filter_high_usage(self, queryset):
        high_usage_projects = []

        # Get latest usage record
        for project in queryset:
            usage = project.usage_records.order_by('-reset_date').first()
            usage_pct = self._calculate_usage_percentage(project, usage)

            if usage_pct > 80:
                high_usage_projects.append(project.id)

        return queryset.filter(id__in=high_usage_projects)

    # Filter projects with usage 50-80%
    def _filter_medium_usage(self, queryset):
        medium_usage_projects = []

        for project in queryset:
            usage = project.usage_records.order_by('-reset_date').first()
            usage_pct = self._calculate_usage_percentage(project, usage)

            if 50 <= usage_pct <= 80:
                medium_usage_projects.append(project.id)

        return queryset.filter(id__in=medium_usage_projects)

    # Filter projects with usage < 50%
    def _filter_low_usage(self, queryset):
        low_usage_projects = []

        for project in queryset:
            usage = project.usage_records.order_by('-reset_date').first()
            usage_pct = self._calculate_usage_percentage(project, usage)

            if usage_pct < 50:
                low_usage_projects.append(project.id)

        return queryset.filter(id__in=low_usage_projects)

    # Filter projects with no usage data
    def _filter_no_data(self, queryset):
        projects_with_data = ProjectUsage.objects.values_list(
            'project_id', flat=True
        ).distinct()

        return queryset.exclude(id__in=projects_with_data)

# Filter projects by usage levels using database annotations, efficietn for large datasets
class UsageFilterOptimized(admin.SimpleListFilter):
    title = _('Usage Level')
    parameter_name = 'usage_level'

    def lookups(self, request, model_admin):
        return (
            ('high', _('High (>80%)')),
            ('medium', _('Medium (50-80%)')),
            ('low', _('Low (<50%)')),
            ('no_data', _('No Usage Data')),
        )

    # Filter using database annotations for better performance
    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset

        # Get latedt usage for each project
        from django.db.models import Window
        from django.db.models.functions import Rank

        queryset = queryset.select_related().prefetch_related(
            'usage_records'
        )

        if self.value() == 'high':
            return self._high_usage_queryset(queryset)
        elif self.value() == 'medium':
            return self._medium_usage_queryset(queryset)
        elif self.value() == 'low':
            return self._low_usage_queryset(queryset)
        elif self.value() == 'no_data':
            # Projects with no usage records
            return queryset.filter(usage_records__isnull=True).distinct()

        return queryset

    # Database-optimized high usage filter
    def _high_usage_queryset(self, queryset):
        high_projects = []

        for project in queryset:
            usage = project.usage_records.order_by('-reset_date').first()

            if usage:
                max_pct = max(
                    (usage.api_calls_used / project.max_api_calls_monthly * 100) if project.max_api_calls_monthly > 0 else 0,
                    (usage.storage_used_gb / project.max_storage_gb * 100) if project.max_storage_gb > 0 else 0,
                    (usage.team_members_count / project.max_team_members * 100) if project.max_team_members > 0 else 0,
                )
                if max_pct > 80:
                    high_projects.append(project.id)
        return queryset.filter(id__in=high_projects)

    # Database-optimized medium usage filter
    def _medium_usage_queryset(self, queryset):
        medium_projects = []

        for project in queryset:
            usage = project.usage_records.order_by('reset_date').first()

            if usage:
                max_pct = max(
                    (usage.api_calls_used / project.max_api_calls_monthly * 100) if project.max_api_calls_monthly > 0 else 0,
                    (usage.storage_used_gb / project.max_storage_gb * 100) if project.max_storage_gb > 0 else 0,
                    (usage.team_members_count / project.max_team_members * 100) if project.max_team_members > 0 else 0,
                )
                if 50 <= max_pct <= 80:
                    medium_projects.append(project.id)

        return queryset.filter(id__in=medium_projects)

    # Database-optimized low usage filter
    def _low_usage_queryset(self, queryset):
        low_projects = []

        for project in queryset:
            usage = project.usage_records.order_by('reset_date').first()

            if usage:
                max_pct = max(
                    (usage.api_calls_used / project.max_api_calls_monthly * 100) if project.max_api_calls_monthly > 0 else 0,
                    (usage.storage_used_gb / project.max_storage_gb * 100) if project.max_storage_gb > 0 else 0,
                    (usage.team_members_count / project.max_team_members * 100) if project.max_team_members > 0 else 0,
                )
                if max_pct < 50:
                    low_projects.append(project.id)

        return queryset.filter(id__in=low_projects)

# Optimize large datasets performance by filtering projects by usage levels with caching
class UsageFilterCached(admin.SimpleListFilter):
    title = _('Usage Level')
    parameter_name = 'usage_level'
    cache_timeout = 300 # 5mins

    def lookups(self, request, model_admin):
        return (
            ('high', _('High (>80%)')),
            ('medium', _('Medium (50-80%)')),
            ('low', _('Low (<50%)')),
            ('no_data', _('No Usage Data')),
        )

    # Filter using cached calculations
    def queryset(self, request, queryset):
        from django.core.cache import cache

        if self.value() is None:
            return queryset

        # Build cache key
        cache_key = f'usage_filter_{self.value()}_projects'

        # Try to get from cache
        project_ids = cache_key.get(cache_key)

        if project_ids is None:
            # Calculate and cache
            project_ids = self._calculate_usage_projects(queryset, self.value())
            cache.set(cache_key, project_ids, self.cache_timeout)

        if self.value() == 'no_data':
            return queryset.filter(id__in=project_ids)

        return queryset.filter(id__in=project_ids)

    # Calculate which projects match the usage level
    def _calculate_usage_projects(self, queryset, usage_level):
        matching_projects = []

        for project in queryset.prefetch_related('usage_records'):
            usage = project.usage_records.order_by('-reset_date').first()

            if usage_level == 'no_data':
                if not usage:
                    matching_projects.append(project.id)

            else:
                if usage:
                    usage_pct = self._get_max_usage_percentage(project, usage)

                    if usage_level == 'high' and usage_pct > 80:
                        matching_projects.append(project.id)
                    elif usage_level == 'medium' and 50 <= usage_pct <= 80:
                        matching_projects.append(project.id)
                    elif usage_level == 'low' and usage_pct < 50:
                        matching_projects.append(project.id)

        return matching_projects

    # Calculate max usage percentage
    def _get_max_usage_percentage(self, project, usage):
        percentages = []

        if project.max_api_calls_monthly > 0:
            percentages.append(usage.api_calls_used / project.max_api_calls_monthly * 100)

        if project.max_storage_gb > 0:
            percentages.append(usage.storage_used_gb / project.max_storage_gb * 100)

        if project.max_team_members > 0:
            percentages.append(usage.team_members_count / project.max_team_members * 100)

        return max(percentages) if percentages else 0

class ProjectAdminWithUsageFilter(admin.ModelAdmin):
    list_filter = [
        'operational_status',
        'subscription_status',
        UsageFilter,
        'created_at'
    ]

    # Optimizer queryset for admin
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related(
            'owner', 'created_by', 'tenant'
        ).prefetch_related('usage_records')

    # Display usage percentage in list view
    def usage_percentage_display(self, obj):
        usage = obj.usage_records.order_by('-reset_date').first()

        if not usage:
            return '-'

        # Calculate percentages
        api_pct = (usage.api_calls_used / obj.max_api_calls_monthly * 100) if obj.max_api_calls_monthly > 0 else 0lambda
        storage_pct = (usage.storage_used_gb / obj.max_storage_gb * 100) if obj.max_storage_gb > 0 else 0
        team_pct = (usage.team_members_count / obj.max_team_members * 100) if obj.max_team_members > 0 else 0

        max_usage = max(api_pct, storage_pct, team_pct)

        # Color code based on usage
        if max_usage > 80:
            color = 'red'
            label = 'High'
        elif max_usage >= 50:
            color = 'orange'
            label = 'Medium'
        else:
            color = 'green'
            label = 'Low'

        return format_html(
            '<span style="color: {}; font-weight: bold;">{} ({:.1f}%)</span>',
            color,
            label,
            max_usage
        )

    usage_percentage_display.short_description = _('Usage Level')

def calculate_project_usage_percentage(project, usage=None):
    if usage is None:
        usage = project.usage_records.order_by('reset_date').first()

    if not usage:
        return 0, {
            'api_calls_pct': 0,
            'storage_pct': 0,
            'team_pct': 0,
            'has_data': False
        }

    # Calculate each percentage
    api_calls_pct = (usage.api_calls_used / project.max_api_calls_monthly * 100) if project.max_api_calls_monthly > 0 else 0
    storage_pct = (usage.storage_used_gb / project.max_storage_gb * 100) if project.max_storage_gb > 0 else 0
    team_pct = (usage.team_members_count / project.max_team_members * 100) if project.max_team_members > 0 else 0

    max_pct = max(api_calls_pct, storage_pct, team_pct)

    return max_pct, {
        'api_calls_pct': round(api_calls_pct, 2),
        'storage_pct': round(storage_pct, 2),
        'team_pct': round(team_pct, 2),
        'max_usage': round(max_pct, 2),
        'has_data': True,
        'usage_level': 'High' if max_pct > 80 else 'Medium' if max_pct >= 50 else 'Low'
    }