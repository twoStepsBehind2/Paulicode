from django.contrib.admin.apps import AdminConfig


class PauliCodeAdminConfig(AdminConfig):
    """Admin config that uses the custom PauliCodeAdminSite for /admin/."""
    default_site = 'User.admin_site.PauliCodeAdminSite'
