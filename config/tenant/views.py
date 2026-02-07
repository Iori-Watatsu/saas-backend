from rest_framework import viewsets, permissions, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.utils.representation import serializer_repr

from .models import Tenant, Domain
from .serializers import TenantSerializer, PremiumTenantSingupSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from config.database_connections import DatabaseConnectionManager


# Create your views here.
class TenantView(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAdminUser]
    authentication_classes = [TokenAuthentication]
    queryset = Tenant.objects.all()
    serializer_class = TenantSerializer

    # Custom query filter
    #def get_queryset(self):
     ##  name_filter = self.request.query_params.get('name', None)
       # if name_filter is not None:
        #    queryset = queryset.filter(name__icontains=name_filter)
        #return queryset

    # Upgrade tenant to premium plan
    @action(detail=True, methods=['post'])
    def upgrade_to_premium(self, request, pk=None):
        tenant = self.get_object()

        if tenant.tenant_type == 'premium':
            return Response(
                {'error': 'Tenant is already on premium plan.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Upgrade tenant
        tenant.plan = 'professional'
        tenant.tenant_type = 'premium'
        tenant.save()

        # Seperate database creation
        success = DatabaseConnectionManager.create_tenant_database(tenant)

        if not success:
            tenant.plan = 'starter'
            tenant.tenant_type = 'standard'
            tenant.save()
            return Response(
                {'error': 'Failed to create premium database'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Create data from schema database
        self._migrate_tenant_data(tenant)

        return Response({
            'message': 'Tenant successfully upgraded to premium',
            'database': tenant.database_name
        })

    # Migrate data from schema to seperate
    def _migrate_tenant_data(self, tenant):
        # research data migration
        pass

    # Seperate database premium tenant signup API
    class PremiumTenantSignupView(APIView):
        permission_classes = [permissions.AllowAny]

        def post(self, request):
            serializer = PremiumTenantSingupSerializer(data=request.data)

            # validate subdomain availability
            if serializer.is_valid():
                subdomain = serializer.validated_data['subdomain']
                if Tenant.objects.filter(subdomain=subdomain).exists():

                    return Response(
                        {'error': 'Subdomain already taken'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Create premium tenant
                tenant = Tenant(
                    name=serializer.validated_data['company_name'],
                    subdomain=subdomain,
                    plan='professional',
                    tenant_type='premium',
                    schema_name=subdomain,
                    auto_create_schema=False
                )
                tenant.save()

                # Create database
                success = DatabaseConnectionManager.create_tenant_database(tenant)

                if not success:
                    tenant.delete()
                    return Response(
                        {'error': 'Failed to create tenant databse'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

                # Create domain
                domain = Domain(
                    domain=f"{subdomain}.yourdomain.com",
                    tenant=tenant,
                    is_primary=True
                )
                domain.save()

                # Create new database admin
                from django.contrib.auth import get_user_model
                User = get_user_model()

                # USe premium tenat context
                from config.tenant_context import premium_tenant_context

                with premium_tenant_context(tenant):
                    user = User.objects.create_user(
                        email=serializer.validated_data['admin_email'],
                        password=serializer.validated_data['password'],
                        first_name=serializer.validated_data['first_name'],
                        last_name=serializer.validated_data['last_name'],
                        is_staff=True,
                        is_superuser=True
                    )

                    return Response({
                        'message': 'Premium tenant created successfully',
                        'tenant_id': str(tenant.id),
                        'subdomain': tenant.subdomain,
                        'database': tenant.database_name,
                        'admin_email': user.email
                    }, status=status.HTTP_201_CREATED)

                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)