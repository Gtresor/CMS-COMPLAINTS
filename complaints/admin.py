from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Attachment, Category, Complaint, ComplaintNote, Department, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User
    list_display = ('username', 'email', 'first_name', 'last_name', 'role', 'department', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('Role & Contact', {'fields': ('role', 'phone', 'department')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Role & Contact', {'fields': ('role', 'phone', 'department')}),
    )


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'default_sla_days')
    search_fields = ('name',)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'default_sla_days')
    search_fields = ('name',)


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = ('case_number', 'complainant_name', 'status', 'priority', 'date_of_complaint', 'registered_by')
    list_filter = ('status', 'priority', 'source', 'category')
    search_fields = ('case_number', 'complainant_name', 'complainant_phone', 'complainant_email', 'description')
    ordering = ('-date_entered_system',)


@admin.register(ComplaintNote)
class ComplaintNoteAdmin(admin.ModelAdmin):
    list_display = ('complaint', 'author', 'created_at')
    search_fields = ('complaint__case_number', 'note', 'author__username')
    ordering = ('-created_at',)


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ('complaint', 'file_type', 'uploaded_at')
    search_fields = ('complaint__case_number', 'file_url', 'file_type')
    ordering = ('-uploaded_at',)
