from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import (
    AttachmentViewSet,
    AgentPerformanceView,
    ComplaintNoteViewSet,
    ComplaintViewSet,
    DashboardStatsView,
    MarkAllNotificationsReadView,
    NotificationsPageView,
    NotificationViewSet,
    UnreadNotificationCountView,
)

router = DefaultRouter()
router.register('complaints', ComplaintViewSet, basename='complaint')
router.register('attachments', AttachmentViewSet, basename='attachment')
router.register('notes', ComplaintNoteViewSet, basename='complaintnote')
router.register('notifications', NotificationViewSet, basename='notification')

urlpatterns = [
    path('dashboard/stats/', DashboardStatsView.as_view(), name='dashboard-stats'),
    path('dashboard/agent-performance/', AgentPerformanceView.as_view(), name='dashboard-agent-performance'),
    path('notifications/unread-count/', UnreadNotificationCountView.as_view(), name='notifications-unread-count'),
    path('notifications/read-all/', MarkAllNotificationsReadView.as_view(), name='notifications-read-all'),
    path('notifications/page/', NotificationsPageView.as_view(), name='notifications-page'),
    path('', include(router.urls)),
]
