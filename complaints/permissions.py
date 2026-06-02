from rest_framework import permissions


class ComplaintPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        role = getattr(user, 'role', None)
        if request.method in permissions.SAFE_METHODS:
            return role in {'admin', 'reviewer', 'handler'}
        if request.method == 'POST':
            return role in {'admin', 'registrar'}
        if request.method in {'PUT', 'PATCH'}:
            return role in {'admin', 'reviewer', 'handler'}
        if request.method == 'DELETE':
            return role == 'admin'
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.role == 'admin':
            return True
        if request.method in permissions.SAFE_METHODS:
            if request.user.role == 'reviewer':
                return True
            if request.user.role == 'handler':
                return obj.assigned_to_id == request.user.id
            return False
        if request.method in {'PUT', 'PATCH'}:
            if request.user.role == 'reviewer':
                return True
            if request.user.role == 'handler':
                return obj.assigned_to_id == request.user.id
            return False
        return False


class AttachmentPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        role = getattr(user, 'role', None)
        if request.method in permissions.SAFE_METHODS:
            return role == 'admin'
        if request.method == 'POST':
            return role in {'admin', 'registrar'}
        if request.method in {'PUT', 'PATCH', 'DELETE'}:
            return role == 'admin'
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.role == 'admin':
            return True
        return False


class ComplaintNotePermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        role = getattr(user, 'role', None)
        if request.method in permissions.SAFE_METHODS:
            return role in {'admin', 'handler'}
        if request.method == 'POST':
            return role in {'admin', 'handler'}
        if request.method in {'PUT', 'PATCH', 'DELETE'}:
            return role == 'admin'
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.role == 'admin':
            return True
        if request.user.role == 'handler':
            return obj.complaint.assigned_to_id == request.user.id
        return False
