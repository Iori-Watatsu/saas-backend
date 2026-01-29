from django.shortcuts import render
from rest_framework import generics
from rest_framework.permissions import AllowAny
from .models import Tenant
from .serializers import TenantSerializer

# Create your views here.
class TenantCreateAPIView(generics.ListCreateAPIView):
    queryset = Tenant.objects.all()
    serializer_class = TenantSerializer
    permission_classes = [AllowAny]

    # Custom query filter
    def get_queryset(self):
        queryset = self.queryset
        name_filter = self.request.query_params.get('name', None)
        if name_filter is not None:
            queryset = queryset.filter(name__icontains=name_filter)
        return queryset