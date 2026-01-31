from rest_framework import viewsets
from rest_framework.permissions import AllowAny
from .models import Tenant
from .serializers import TenantSerializer

# Create your views here.
class TenantView(viewsets.ModelViewSet):
    queryset = Tenant.objects.select_related()
    serializer_class = TenantSerializer
    permission_classes = [AllowAny]

    # Custom query filter
    def get_queryset(self):
        queryset = self.queryset
        name_filter = self.request.query_params.get('name', None)
        if name_filter is not None:
            queryset = queryset.filter(name__icontains=name_filter)
        return queryset

