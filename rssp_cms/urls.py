from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.urls import include, path
from django.views.generic.base import RedirectView, TemplateView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from complaints.views import (
    AccountRedirectView,
    AdminAllComplaintsView,
    AdminAnalyticsView,
    AdminDashboardView,
    AdminSettingsView,
    AdminUserManagementView,
    AuditorDashboardView,
    ComplaintRegistrationView,
    EditComplaintView,
    HandleComplaintRedirectView,
    HandleComplaintView,
    HandlerDashboardView,
    ManagerDashboardView,
    RegistrarDashboardView,
    ReviewerDashboardView,
    SatisfactionSurveyView,
    SeniorManagementDashboardView,
    SupervisorDashboardView,
    UserProfileView,
)



urlpatterns = [
    path('', RedirectView.as_view(url='/dashboard/admin/', permanent=False), name='home'),
    path('accounts/login/', LoginView.as_view(template_name='accounts/login.html'), name='login'),
    path('accounts/logout/', LogoutView.as_view(next_page='/'), name='logout'),
    path('accounts/profile/', AccountRedirectView.as_view(), name='profile'),
    path('accounts/password_change/', PasswordChangeView.as_view(template_name='registration/password_change_form.html'), name='password_change'),
    path('accounts/password_change/done/', TemplateView.as_view(template_name='registration/password_change_done.html'), name='password_change_done'),
    path('admin/', admin.site.urls),
    path('dashboard/admin/', AdminDashboardView.as_view(), name='admin-dashboard'),
    path('dashboard/admin/complaints/', AdminAllComplaintsView.as_view(), name='admin-all-complaints'),
    path('dashboard/complaints/<int:pk>/edit/', EditComplaintView.as_view(), name='edit-complaint'),
    path('dashboard/complaints/register/', ComplaintRegistrationView.as_view(), name='complaint-register'),
    path('dashboard/admin/analytics/', AdminAnalyticsView.as_view(), name='admin-analytics'),
    path('dashboard/admin/settings/', AdminSettingsView.as_view(), name='admin-settings'),
    path('dashboard/admin/users/', AdminUserManagementView.as_view(), name='admin-user-management'),
    path('dashboard/profile/', UserProfileView.as_view(), name='user-profile'),
    path('dashboard/handler/', HandlerDashboardView.as_view(), name='handler-dashboard'),
    path('dashboard/complaints/handle/', HandleComplaintRedirectView.as_view(), name='complaint-handle-fallback'),
    path('dashboard/complaints/<int:pk>/handle/', HandleComplaintView.as_view(), name='complaint-handle'),
    path('dashboard/registrar/', RegistrarDashboardView.as_view(), name='registrar-dashboard'),
    path('dashboard/reviewer/', ReviewerDashboardView.as_view(), name='reviewer-dashboard'),
    # Phase 6: Operational dashboards (Sections 13.1, 13.2, 13.3, 15)
    path('dashboard/supervisor/', SupervisorDashboardView.as_view(), name='supervisor-dashboard'),
    path('dashboard/manager/', ManagerDashboardView.as_view(), name='manager-dashboard'),
    path('dashboard/senior-management/', SeniorManagementDashboardView.as_view(), name='senior-mgmt-dashboard'),
    path('dashboard/auditor/', AuditorDashboardView.as_view(), name='auditor-dashboard'),
    # Public satisfaction survey (token-based access from closure email)
    path('complaints/<int:pk>/survey/<str:token>/', SatisfactionSurveyView.as_view(), name='satisfaction-survey'),
    path('api/', include('complaints.urls')),


    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
