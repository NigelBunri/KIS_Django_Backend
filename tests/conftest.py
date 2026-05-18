import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def sample_user(db):
    return User.objects.create_user(
        phone="+10000000001",
        password="Test1234!",
        username="testuser",
    )


@pytest.fixture
def authenticated_client(api_client, sample_user):
    api_client.force_authenticate(user=sample_user)
    return api_client
