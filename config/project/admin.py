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
        api_pct = (usage.api_calls_used / obj.max_api_calls_monthly * 100) if obj.max_api_calls_monthly > 0 else 0
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

# Filter projects by meber count
class MemberCountFilter(admin.SimpleListFilter):
    title = _('Member.Count')
    parameter_name = 'member_count'

    def lookups(self, request, model_admin):
        return (
            ('solo', _('Solo (1 member)')),
            ('samll', _('Small (2-5)')),
            ('medium', _('Medium (6-20)')),
            ('large', _('Large (20+)'))
        )

    def queryset(self, request, queryset):
        queryset = queryset.annot(member_count=Count('members'))
        if self.value() == 'solo':
            return queryset.filter(member_count=1)
        elif self.value() == 'samell':
            return queryset.filter(member_count__range=[2, 5])
        elif self.value() == 'medium':
            return queryset.filter(member_count__range=[6, 20])
        elif self.value() == 'large':
            return queryset.filter(member_count__gt=20)

# Inline admin for project member
class ProjectMemberInLine(admin.TabularInline):
    model = ProjectMember
    extra = 1
    fields = ['user', 'role', 'invited_at', 'joined_at']
    readonly_fields = ['invited_at', 'joined_at']
    ordering = ['-invited_at']

    # Optomize queryset
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')

# Inline admin for project usage
class ProjectUsageInline(admin.TabularInline):
    model = ProjectUsage
    extra = 0
    fields = ['reset_date', 'api_calls_used', 'storage_used_gb', 'team_members_count', 'updated_at']
    readonly_fields = ['reset_date', 'updated']
    ordering = ['-reset_date']

    def has_add_permission(self, request):
        return False

# Inline admin for audit logs
class ProjectAuditInline(admin.TabularInline):
    model = ProjectAuditLog
    extra = 0
    fields = ['action', 'user', 'timestamp', 'details']
    readonly_fields = ['action', 'user', 'timestamp', 'details']
    ordering = ['-timestamp']

    def has_add_permission(self, request):
        return False

    # Prevent deletion of audit logs
    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Project)
# admin interface for projects
class ProjectAdmin(admin.ModelAdmin):
    list_display = [
        'name',
        'owner_link',
        'tenant',
        'status_badge',
        'subscription_badge',
        'member_count_display',
        'trial_status_display',
        'created_at_display'
    ]
    list_filter = [
        'operational_status',
        'subscription_status',
        TrialEndingFilter,
        MemberCountFilter,
        'created_at',
        'tenant'
    ]
    search_fields = [
        'name',
        'description',
        'owner__email',
        'slug'
    ]
    readonly_fields = [
        'id',
        'slug',
        'created_by',
        'created_at',
        'updated_at',
        'member_count_display',
        'usage_display'
    ]

    fieldsets = (
        (_('Project Information'), {
            'fields': ('id', 'name', 'slug', 'description', 'tenant')
        }),
        (_('Ownership'), {
            'fields': ('owner', 'created_by', 'created_at', 'updated_at')
        }),
        (_('Status'), {
            'fields': ('operational_status', 'subscription_status')
        }),
        (_('Resource Limits'), {
            'fields': (
                'max_team_members',
                'max_storage_gb',
                'max_api_calls_monthly'
            )
        }),
        (_('Trial'), {
            'fields': ('trial_ends_at',)
        }),
        (_('Statistics'), {
            'fields': ('member_count_display', 'usage_display'),
            'classes': ('collapse',)
        }),
    )

    inlines = [ProjectMemberInLine, ProjectUsageInline, ProjectAuditInline]

    actions = ['archive_projects', 'restore_projects', 'extended_trials']

    # Optimize queryset
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related(
            'owner', 'created_by', 'tenant'
        ).prefatch_related('members')

    # Custom search results
    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(
            request, queryset, search_term
        )
        return queryset, use_distinct

    # Custom display methods
    def owner_link(self, obj):
        if obj.owner:
            url = reverse('admin:users_customuser_change', args=[obj.owner.id])
            return format_html('<a href="{}">{}</a>', url, obj.owner.email)
        return '-'
    owner_link.short_description = _('Owner')

    # Display operation status with color
    def status_badge(self, obj):
        colors = {
            'active': '#28a745',
            'archived': '#6c757d',
            'deleted': '#dc3545'
        }
        colors = colors.get(obj.operation_status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; '
            'border-radius: 3px;">{}</span>',
            colors,
            obj.get_operational_status_display()
        )
    status_badge.short_description = _('Status')

    # Display subscription status with color
    def subscription_badge(self, obj):
        colors = {
            'active': '#17a2b8',
            'trial': '#ffc107',
            'suspended': '#dc3545',
            'expired': '#6c757d',
            'cancelled': '#495057'
        }
        color = colors.get(obj.subscription_status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; '
            'border-radius: 3px;">{}</span>',
            color,
            obj.get_subscription_status_display()
        )
    subscription_badge.short_description = _('Subscription')

    # Display member count
    def member_count_display(self, obj):
        return f'{obj.members.count()} / {obj.max_team_members}'
    member_count_display.short_description = _('Members')

    # Display trial status
    def trial_status_display(self, obj):
        if not obj.trial_ends_at:
            return '-'
        from django.utils import timezone
        if obj.trial_ends_at > timezone.now():
            days = (obj.trial_ends_at - timezone.now()).days
            return format_html(
                '<span style="color: #ffc107;><strong{} days left</strong></span>',
                days
            )
        else:
            return format_html(
                '<span style="color: #dc3545;"><strong>Expired</strong></span>'
            )
    trial_status_display.short_description = _('Trial Status')

    # Display creation date
    def created_at_display(selfself, obj):
        return obj.created_at.strftime('%Y-%m-%d %H:%M')
    created_at_display.short_description = _('Created')

    # Display usage statics
    def usage_display(selfself, obj):
        usage = obj.usage_records.order_by('-reset_date').fisrt()
        if usage:
            storage_pct = (usage.storage_used_gb / obj.max_storage_gb * 100) if obj.max_storage_gb > 0 else 0
            api_pct = (usage.api_calls_used / obj.max_api_calls_monthly * 100) if obj.max_api_calls_monthly > 0 else 0
            return format_html(
                '<div>'
                '<p><strong>Storage:</strong> {:.1f}GB / {}GB ({:.1f}%)</p>'
                '<p><strong>API Calls:</strong> {} / {} ({:.1f}%)</p>'
                '<p><strong>Team:</strong> {} / {}</p>'
                '</div>',
                usage.storage_used_gb,
                obj.max_storage_gb,
                storage_pct,
                usage.api_calls_used,
                obj.max_api_calls_monthly,
                api_pct,
                usage.team_members_count,
                obj.max_team_members
            )
        return '-'
    usage_display.short_description = _('Usage Statistics')

    # Admin actions
    def archive_projects(self, request, queryset):
        count = queryset.update(operational_status='archived')
        self.message_user(request, _('Successfully archived { projects.}').format(count))
    archive_projects.shorts_description = _('Restore selected projects')

    def restore_projects(self, request, queryset):
        count = queryset.filter(operational_status='archived').update(operational_status='active')
        self.message_user(request, _('Successfully restored {} projects.').format(count))
    restore_projects.short_description = _('Restore selected projects')

    # extend trial for a max of 3 projects
    def extend_trials(self, request, queryset):
        from django.utils import timezone
        from datetime import timedelta

        trial_projects = queryset.filter(subscription_status='trial')
        limited = trial_projects[:3]

        count = limited.update(subscription_status='trial').update(
            trial_ends_at=timezone.now() + timedelta(days=30)
        )
        self.message_user(request, _('Extended trial for {} prjects.').format(count))
    extend_trials.short_description = _('Extend by 30 days')

    # Allow adding projects only for superusers
    def has_add_permission(self, request):
        return request.user.is_superuser

@admin.register(ProjectMember)
# Admin interface for projects member
class projectMemberAdmin(admin.ModelAdmin):
    list_display = [
        'user_email',
        'project_name',
        'role',
        'invited_at_display',
        'joined_at_display'
    ]
    list_filter = [
        'role',
        'invited_at',
        'joined_at',
        'project__tenant'
    ]
    search_fields = [
        'user__email',
        'project__name',
        'user__first_name',
        'user__last_name'
    ]
    readonly_fields = [
        'id',
        'invited_at'
    ]

    fieldsets = (
        (_('Member Information'), {
            'fields': ('id', 'project', 'user', 'role')
        }),
        (_('Dates'), {
            'fields': ('invited_at', 'joined_at')
        }),
    )

    # Optimize queryset
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'projects')

    # Display user email
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = _('User Email')

    # Display name as link
    def project_name(selfself, obj):
        url = reverse('admin:projects_project_change', args=[obj.project.id])
        return format_html('<a href="{}">{}</a>', url, obj.project.name)
    project_name.short_description = _('Project')

    def invited_at_display(self, obj):
        return obj.invited_at.strftime('%Y-%m-%d %H:%M')
    invited_at_display.short_description = _('Invited At')

    def joined_at_display(self, obj):
        if obj.joined_at:
            return obj.joined_at.strftime('%Y-%m-%d %H:%M')
        return '-'
    joined_at_display.short_description = _('Joined At')

@admin.register(ProjectUsage)
# Admin interface for project usage
class ProjectUsageAdmin(admin.ModelAdmin):
    list_display = [
        'project_name',
        'reset_date',
        'api_calls_usage',
        'storage_usage',
        'team_members_usage',
        'updated_at_display'
    ]
    list_filter = [
        'reset_date',
        'updated_at',
        'project__tenant'
    ]
    search_fields = [
        'project__name',
        'project__slug'
    ]
    readonly_fields = [
        'id',
        'project',
        'reset_date',
        'updated_at',
        'usage_breakdown'
    ]

    fieldsets = (
        (_('Project Usage'), {
            'fields': ('id', 'project', 'reset_date')
        }),
        (_('Usage Metrics'), {
            'fields': (
                'api_calls_used',
                'storage_used_gb',
                'team_members_count'
            )
        }),
        (_('Breakdown'), {
            'fields': ('usage_breakdown',),
            'classes': ('collapse',)
        }),
        (_('Timestamps'), {
            'fields': ('updated_at',)
        }),
    )

    # Optimize queryset
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('project')

    # Display project name as link
    def project_name(self, obj):
        url = reverse('admin:projects_project_change', args=[obj.project.id])
        return format_html('<a href="{}">{}</a>', url, obj.project.name)
    project_name.short_description = _('Project')

    # Display api usage
    def api_calls_usage(self, obj):
        if obj.project.max_api_calls_monthly > 0:
            pct = (obj.api_calls_used / obj.project.max_api_calls_monthly) * 100
            color = 'red' if pct > 80 else 'orange' if pct > 50 else 'green'
            return format_html(
                '<span style="color: {};">{} / {} ({:.1f}%)</span>',
                color,
                obj.api_calls_used,
                obj.project.max_api_calls_monthly,
                pct
            )
        return f"{obj.api_calls_used} / Unlimited"
    api_calls_usage.short_description = _('API Calls')

    def storage_usage(self, obj):
        if obj.project.max_storage_gb > 0:
            pct = (obj.storage_used_gb / obj.project.max_storage_gb) * 100
            color = 'red' if pct > 80 else 'orange' if pct > 50 else 'green'
            return format_html(
                '<span style="color: {};">{:.1f}GB / {}GB ({:.1f}%)</span>',
                color,
                obj.storage_used_gb,
                obj.project.max_storage_gb,
                pct
            )
        return f"{obj.storage_used_gb}GB / Unlimited"
    storage_usage.short_description = _('Storage')

    def team_members_usage(self, obj):
        pct = (obj.team_members_count / obj.project.max_team_members) * 100
        color = 'red' if pct > 80 else 'orange' if pct > 50 else 'green'
        return format_html(
            '<span style="color: {};">{} / {} ({:.1f}%)</span>',
            color,
            obj.team_members_count,
            obj.project.max_team_members,
            pct
        )
    team_members_usage.short_description = _('Team Members')

    def updated_at_display(self, obj):
        return obj.updated_at.strftime('%Y-%m-%d %H:%M')
    updated_at_display.short_description = _('Updated')

    def usage_breakdown(self, obj):
        return format_html(
            '<div>'
            '<p><strong>API Calls:</strong> {}/{} ({:.1f}%)</p>'
            '<p><strong>Storage:</strong> {:.1f}GB / {}GB ({:.1f}%)</p>'
            '<p><strong>Team Members:</strong> {} / {} ({:.1f}%)</p>'
            '<p><strong>Reset Date:</strong> {}</p>'
            '</div>',
            obj.api_calls_used,
            obj.project.max_api_calls_monthly,
            (obj.api_calls_used / obj.project.max_api_calls_monthly * 100) if obj.project.max_api_calls_monthly > 0 else 0,
            obj.storage_used_gb,
            obj.project.max_storage_gb,
            (obj.storage_used_gb / obj.project.max_storage_gb * 100) if obj.project.max_storage_gb > 0 else 0,
            obj.team_members_count,
            obj.project.max_team_members,
            (obj.team_members_count / obj.project.max_team_members * 100),
            obj.reset_date
        )
    usage_breakdown.short_description = _('Usage Breakdown')

    usage_breakdown.short_description = _('Usage Breakdown')

    def has_add_permission(self, request):
        return

@admin.register(ProjectAuditLog)
# Admin interface for audit logs
class ProjectAuditLoginAdmin(admin.ModelAdmin):
    list_display = [
        'action_badge',
        'project_name',
        'user_email',
        'timestamp_display',
        'ip_address'
    ]
    list_filter = [
        'action',
        'timestamp',
        'project__tenant'
    ]
    search_fields = [
        'project__name',
        'user__email',
        'details',
        'ip_address'
    ]
    readonly_fields = [
        'id',
        'tenant',
        'project',
        'user',
        'action',
        'timestamp',
        'details_display',
        'ip_address',
        'user_agent'
    ]

    fieldsets = (
        (_('Audit Information'), {
            'fields': ('id', 'tenant', 'project', 'user')
        }),
        (_('Action'), {
            'fields': ('action', 'timestamp')
        }),
        (_('Details'), {
            'fields': ('details_display',)
        }),
        (_('Request Information'), {
            'fields': ('ip_address', 'user_agent')
        }),
    )

    # Optimize queryset
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('project', 'user', 'tenant')

    # Display Action with color
    def action_badge(self, obj):
        colors = {
            'created': '#28a745',
            'updated': '#17a2b8',
            'deleted': '#dc3545',
            'archived': '#6c757d',
            'restored': '#ffc107',
            'member_added': '#007bff',
            'member_removed': '#fd7e14',
            'member_role_changed': '#6f42c1',
            'settings_updated': '#17a2b8',
            'data_exported': '#20c997',
            'accessed': '#6c757d'
        }
        color = colors.get(obj.action, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; '
            'border-radius: 3px;">{}</span>',
            color,
            obj.get_action_display()
        )
    action_badge.short_description = _('Action')

    # Display project name as link
    def project_name(selfself, obj):
        url = reverse('admin:projects_project_change', args=[obj.project.id])
        return format_html('<a href="{}">{}</a>', url, obj.project.name)
    project_name.short_description = _('Project')

    def user_email(self, obj):
        if obj.user:
            return obj.user.email
        return '-'
    user_email.short_description = _('User')

    def details_display(self, obj):
        if obj.details:
            items = '<br>'.join(f'<strong>{k}:</strong> {v}' for k, v in obj.details.items())
            return format_html(items)
        return '-'
    details_display.short_description = _('Details')

    def hsa_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False