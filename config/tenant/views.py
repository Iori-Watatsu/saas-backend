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