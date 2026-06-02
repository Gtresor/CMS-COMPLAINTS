from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    default_sla_days = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    ROLE_CHOICES = [
        ('registrar', 'Registrar'),
        ('handler', 'Handler'),
        ('reviewer', 'Reviewer'),
        ('admin', 'Admin'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='registrar')
    phone = models.CharField(max_length=20, blank=True)
    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='users',
    )

    def __str__(self):
        return self.username


class Category(models.Model):
    name = models.CharField(max_length=150, unique=True)
    default_sla_days = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Complaint(models.Model):
    SOURCE_CHOICES = [
        ('call_center', 'Call Center'),
        ('email', 'Email'),
        ('physical', 'Physical'),
        ('online', 'Online'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]

    STATUS_CHOICES = [
        ('new', 'New'),
        ('under_review', 'Under Review'),
        ('assigned', 'Assigned'),
        ('in_progress', 'In Progress'),
        ('pending_info', 'Pending Info'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
        ('pending', 'Pending'),
    ]

    HANDLER_STATUSES = ['in_progress', 'pending_info', 'resolved', 'closed']

    SLA_MODE_CHOICES = [
        ('manual', 'Manual'),
        ('auto', 'Auto'),
    ]

    # === New Registration Form Fields (Section: Complaint Information) ===
    case_number = models.CharField(max_length=20, unique=True, default='', blank=True)
    full_name = models.CharField(max_length=150, default='', blank=True)
    phone_number = models.CharField(max_length=30, default='', blank=True)
    complaint_source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='online')
    email = models.EmailField(blank=True, null=True)
    national_id = models.CharField(max_length=50, blank=True, null=True)
    bank_institution = models.CharField(max_length=255, blank=True, null=True)

    # === New Registration Form Fields (Section: Complaint Details) ===
    complaint_title = models.CharField(max_length=200, default='', blank=True)
    complaint_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True, null=True)

    # === New Registration Form Fields (Section: Assignment & Response) ===
    response_due_days = models.PositiveIntegerField(default=7)

    # === System-generated fields ===
    system_code = models.CharField(max_length=20, blank=True, default='')
    registered_at = models.DateTimeField(auto_now_add=True)

    # === Status ===
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # === Foreign Keys ===
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_complaints',
        limit_choices_to={'role': 'handler'}
    )
    registered_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='registered_complaints',
    )
    assigned_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_by_complaints',
    )

    # === Original fields (kept for backward compatibility with existing data) ===
    complainant_name = models.CharField(max_length=150, blank=True, default='')
    complainant_phone = models.CharField(max_length=30, blank=True, default='')
    complainant_email = models.EmailField(blank=True, default='')
    beneficiary_bank = models.CharField(max_length=255, blank=True, default='')
    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='complaints_category',
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='online')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    date_of_complaint = models.DateField(null=True, blank=True)
    date_entered_system = models.DateTimeField(auto_now_add=True)
    date_assigned = models.DateTimeField(null=True, blank=True)
    sla_days = models.PositiveIntegerField(default=0)
    sla_mode = models.CharField(max_length=10, choices=SLA_MODE_CHOICES, default='manual')
    sla_deadline = models.DateField(null=True, blank=True)
    date_resolved = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date_entered_system']

    def __str__(self):
        return self.case_number or f'Complaint {self.pk}'

    def save(self, *args, **kwargs):
        if not self.case_number:
            self.case_number = self._generate_case_number()

        if not self.system_code:
            self.system_code = self._generate_system_code()

        changes_tracking = {}
        if self.pk:
            original = Complaint.objects.get(pk=self.pk)
            if original.status != self.status:
                changes_tracking['status_changed'] = True

        if self.assigned_to and not self.date_assigned:
            self.date_assigned = timezone.now()

        if self.date_assigned:
            if self.sla_mode == 'auto' and self.category:
                self.sla_deadline = (self.date_assigned + timedelta(days=self.category.default_sla_days)).date()
            elif self.sla_mode == 'manual':
                self.sla_deadline = (self.date_assigned + timedelta(days=self.sla_days)).date()

        if self.status == 'resolved' and not self.date_resolved:
            self.date_resolved = timezone.now()

        super().save(*args, **kwargs)

    def _generate_system_code(self):
        year = timezone.now().year
        prefix = f'SMP-{year}-'
        last = Complaint.objects.filter(system_code__startswith=prefix).order_by('-system_code').first()
        if last and last.system_code:
            try:
                last_index = int(last.system_code.split('-')[-1])
            except (ValueError, IndexError):
                last_index = 0
        else:
            last_index = 0
        return f'{prefix}{last_index + 1:04d}'

    @property
    def is_overdue(self):
        if not self.sla_deadline or self.status in {'resolved', 'closed'}:
            return False
        return timezone.localdate() > self.sla_deadline

    @property
    def days_remaining(self):
        if not self.sla_deadline:
            return None
        remaining = (self.sla_deadline - timezone.localdate()).days
        return remaining

    @property
    def has_action_taken(self):
        """Check if any action has been recorded for this complaint"""
        return self.actions.filter(action_taken=True).exists()

    @classmethod
    def _generate_case_number(cls):
        year = timezone.now().year
        prefix = f'CMP-{year}-'
        last = cls.objects.filter(case_number__startswith=prefix).order_by('-case_number').first()
        if last and last.case_number:
            try:
                last_index = int(last.case_number.split('-')[-1])
            except (ValueError, IndexError):
                last_index = 0
        else:
            last_index = 0
        return f'{prefix}{last_index + 1:04d}'


class ComplaintAction(models.Model):
    """Track actions taken on complaints"""
    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.CASCADE,
        related_name='actions',
    )
    handler = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='complaint_actions',
    )
    action_taken = models.BooleanField(default=False)
    action_description = models.TextField(blank=True, default='')
    previous_status = models.CharField(max_length=20, blank=True, default='')
    new_status = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        action = 'Action taken' if self.action_taken else 'No action'
        return f'{action} on {self.complaint.case_number or self.complaint.pk}: {self.previous_status} → {self.new_status}'


class ComplaintNote(models.Model):
    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.CASCADE,
        related_name='notes',
    )
    author = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='complaint_notes',
    )
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Note for {self.complaint.case_number or self.complaint.pk}'


def complaint_attachment_upload_path(instance, filename):
    return f'complaints/{instance.complaint_id}/{filename}'


class SystemConfiguration(models.Model):
    """Singleton model for global system settings (email, etc.)"""
    sender_email = models.EmailField(default='', blank=True)
    sender_name = models.CharField(max_length=255, default='', blank=True)
    smtp_host = models.CharField(max_length=255, default='', blank=True)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_username = models.CharField(max_length=255, default='', blank=True)
    smtp_password = models.CharField(max_length=255, default='', blank=True)
    use_tls = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Live SMTP test tracking
    last_test_passed = models.BooleanField(default=False)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_message = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'System Configuration'
        verbose_name_plural = 'System Configuration'

    def __str__(self):
        return f'System Configuration ({self.sender_email})'

    @classmethod
    def get_settings(cls):
        config, _ = cls.objects.get_or_create(id=1)
        return config

    @property
    def is_fully_configured(self):
        """True when every required SMTP field is non-empty."""
        return bool(
            self.sender_email
            and self.smtp_host
            and self.smtp_username
            and self.smtp_password
        )

    @property
    def is_working(self):
        """True when the config is filled in AND the last live SMTP test passed."""
        return self.is_fully_configured and self.last_test_passed

    @property
    def status_label(self):
        """Human-friendly status label for UI display."""
        if not self.is_fully_configured:
            return 'Not Configured'
        if self.last_test_passed:
            return 'Working'
        return 'Not Working'


class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('new_complaint', 'New Complaint'),
        ('handler_assigned', 'Handler Assigned'),
        ('status_update', 'Status Update'),
        ('sla_approaching', 'SLA Approaching'),
        ('sla_exceeded', 'SLA Exceeded'),
        ('note_added', 'Note Added'),
        ('action_added', 'Action Added'),
        ('complaint_updated', 'Complaint Updated'),
        ('user_created', 'User Created'),
        ('user_updated', 'User Updated'),
        ('user_deleted', 'User Deleted'),
        ('assignment', 'Assignment'),
    ]
    
    SEVERITY_LEVELS = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='notifications'
    )
    performed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='notifications_performed'
    )
    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, null=True, blank=True, related_name='notifications'
    )
    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPES)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_LEVELS, default='info')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at']),
            models.Index(fields=['complaint', '-created_at']),
        ]

    def __str__(self):
        return f'[{self.notification_type}] {self.message[:60]}'
    
    def mark_as_read(self):
        """Mark notification as read and set read_at timestamp"""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])


class Attachment(models.Model):
    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to=complaint_attachment_upload_path, null=True, blank=True)
    file_type = models.CharField(max_length=100, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.file.name

    @property
    def file_url(self):
        if not self.file:
            return ''
        return self.file.url