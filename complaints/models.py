"""
Complaints models for the Social Security Institution CMS.

This module implements the data model from the System Design Document:
  - 3 Schemes (Pension, Occupational Hazards, Maternity Leave)
  - 10 Complaint Types (CT-01 .. CT-10) with default priority + SLA hours
  - 8 Intake Channels
  - 9 Statuses (incl. escalated + reopened)
  - 4-level escalation matrix
  - SLA in hours with traffic-light indicators
  - SLA clock pause while awaiting additional information
  - 7-year retention of audit trail
  - Post-resolution satisfaction surveys
"""
from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Reference / Lookup tables
# ---------------------------------------------------------------------------

class Scheme(models.Model):
    """
    Top-level scheme the complaint belongs to.  Section 2 of the design:
      - Pension Scheme
      - Occupational Hazards Scheme
      - Maternity Leave Benefits Scheme
    """
    PENSION = 'PENSION'
    OCCUPATIONAL_HAZARDS = 'OCCUPATIONAL_HAZARDS'
    MATERNITY_LEAVE = 'MATERNITY_LEAVE'
    CODE_CHOICES = [
        (PENSION, 'Pension Scheme'),
        (OCCUPATIONAL_HAZARDS, 'Occupational Hazards Scheme'),
        (MATERNITY_LEAVE, 'Maternity Leave Benefits Scheme'),
    ]

    code = models.CharField(max_length=40, choices=CODE_CHOICES, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default='')
    manager = models.ForeignKey(
        'User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='managed_schemes',
    )
    team_email = models.EmailField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ComplaintType(models.Model):
    """
    Section 4.1 of the design.  10 complaint types (CT-01 .. CT-10) each with
    a default priority and default SLA window in hours.
    """
    CT01_DELAYED_PAYMENT = 'CT-01'
    CT02_BENEFIT_CALC = 'CT-02'
    CT03_MISSING_CONTRIB = 'CT-03'
    CT04_EMPLOYER_ISSUE = 'CT-04'
    CT05_MEDICAL_ASSESS = 'CT-05'
    CT06_CLAIM_REJECTION = 'CT-06'
    CT07_DOC_ISSUE = 'CT-07'
    CT08_SERVICE_DELIVERY = 'CT-08'
    CT09_APPEAL = 'CT-09'
    CT10_OTHER = 'CT-10'
    CODE_CHOICES = [
        (CT01_DELAYED_PAYMENT, 'Delayed Payment'),
        (CT02_BENEFIT_CALC, 'Benefit Calculation Issue'),
        (CT03_MISSING_CONTRIB, 'Missing or Incorrect Contribution'),
        (CT04_EMPLOYER_ISSUE, 'Employer-Related Issue'),
        (CT05_MEDICAL_ASSESS, 'Medical Assessment Issue'),
        (CT06_CLAIM_REJECTION, 'Claim Rejection'),
        (CT07_DOC_ISSUE, 'Documentation / Administrative Issue'),
        (CT08_SERVICE_DELIVERY, 'Service Delivery Complaint'),
        (CT09_APPEAL, 'Appeal'),
        (CT10_OTHER, 'Other / General Enquiry'),
    ]

    # Section 4.2 default priorities (used to auto-suggest priority at intake)
    PRIORITY_CHOICES = [
        ('critical', 'Critical'),
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ]

    code = models.CharField(max_length=10, choices=CODE_CHOICES, unique=True)
    name = models.CharField(max_length=150)
    default_priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    # Design specifies: Critical 24-48h, High 3-5d, Medium 5-10d, Low 10-15d
    # We store the *upper bound* in hours so the SLA engine can compute deadlines.
    default_sla_hours = models.PositiveIntegerField(default=240)  # 10 days default
    applicable_schemes = models.ManyToManyField(Scheme, blank=True, related_name='complaint_types')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} · {self.name}'


class Location(models.Model):
    """
    Regional office / branch.  Used for routing (Section 8.1) and reporting.
    """
    name = models.CharField(max_length=150, unique=True)
    region_code = models.CharField(max_length=20, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.region_code})' if self.region_code else self.name


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    default_sla_days = models.PositiveIntegerField(default=0)
    # Tie a department to a scheme if applicable
    scheme = models.ForeignKey(
        Scheme,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='departments',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    """
    Custom user model that adds:
      - 7-role hierarchy from Section 6 of the design
      - supervisor self-FK (for the escalation chain)
      - location FK (for routing)
    """
    ROLE_CHOICES = [
        # Section 6 - aligned with the design
        ('intake_officer', 'Intake Officer'),
        ('registrar', 'Registrar (legacy Intake)'),
        ('handler', 'Case Officer'),
        ('reviewer', 'Reviewer (legacy)'),
        ('supervisor', 'Supervisor'),
        ('manager', 'Manager'),
        ('director', 'Senior Management / Director'),
        ('auditor', 'Auditor'),
        ('admin', 'Administrator'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='intake_officer')
    phone = models.CharField(max_length=20, blank=True)
    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='users',
    )
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='users',
    )
    supervisor = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='subordinates',
        help_text='Used by the escalation matrix to find the next escalation target.',
    )
    # Optional: workload tracking for the routing engine (Section 8.1)
    is_available_for_assignment = models.BooleanField(default=True)

    def __str__(self):
        return self.username

    # ----- role helpers (used by permissions, services, escalation) -----
    @property
    def is_supervisor(self):
        return self.role == 'supervisor'

    @property
    def is_manager(self):
        return self.role == 'manager'

    @property
    def is_director(self):
        return self.role == 'director'

    @property
    def is_intake_officer(self):
        return self.role in ('intake_officer', 'registrar')

    @property
    def is_auditor(self):
        return self.role == 'auditor'


class Category(models.Model):
    """Legacy free-text category kept for backward compatibility."""
    name = models.CharField(max_length=150, unique=True)
    default_sla_days = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# SLA configuration (Section 12.2 SLA Configuration table)
# ---------------------------------------------------------------------------

class SLAConfiguration(models.Model):
    """
    Configurable SLA rule per (priority, scheme, complaint_type).

    Business hours support and pause-on-pending flags align with Section 14.
    """
    priority = models.CharField(
        max_length=10,
        choices=ComplaintType.PRIORITY_CHOICES,
    )
    scheme = models.ForeignKey(
        Scheme,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='sla_configs',
        help_text='If null, applies to all schemes.',
    )
    complaint_type = models.ForeignKey(
        ComplaintType,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='sla_configs',
        help_text='If null, applies to all complaint types.',
    )
    # Max resolution window in hours
    max_hours = models.PositiveIntegerField()
    business_hours_only = models.BooleanField(default=False)
    pause_on_pending = models.BooleanField(
        default=True,
        help_text='If true, SLA clock pauses while complaint is in "Pending Additional Information".',
    )
    # Reminder percentages (Section 8.2)
    reminder_50_percent = models.BooleanField(default=True)
    reminder_75_percent = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['priority', 'scheme', 'complaint_type']
        verbose_name = 'SLA Configuration'
        verbose_name_plural = 'SLA Configurations'

    def __str__(self):
        scheme = self.scheme.name if self.scheme else 'All'
        ctype = self.complaint_type.code if self.complaint_type else 'All'
        return f'{self.priority} · {scheme} · {ctype} · {self.max_hours}h'

    @classmethod
    def get_max_hours(cls, priority, scheme=None, complaint_type=None):
        """
        Resolve the SLA in hours using the most-specific config first.
        Falls back through: type+scheme -> type -> scheme -> priority-only.
        """
        candidates = [
            (complaint_type, scheme),
            (complaint_type, None),
            (None, scheme),
            (None, None),
        ]
        for ctype, sch in candidates:
            qs = cls.objects.filter(
                priority=priority,
                is_active=True,
                scheme=sch,
                complaint_type=ctype,
            )
            config = qs.first()
            if config:
                return config.max_hours
        # Hard defaults from Section 4.2 (upper bound)
        fallback = {
            'critical': 48,
            'high': 120,    # 5 days
            'medium': 240,  # 10 days
            'low': 360,     # 15 days
        }
        return fallback.get(priority, 240)


# ---------------------------------------------------------------------------
# Complaint
# ---------------------------------------------------------------------------

def complaint_attachment_upload_path(instance, filename):
    return f'complaints/{instance.complaint_id}/{filename}'


def complaint_proof_upload_path(instance, filename):
    return f'complaints/{instance.complaint_id}/proof/{filename}'


class Complaint(models.Model):
    """
    Main complaint record.  Adds design-aligned fields (scheme, type, location,
    escalation_level, clock_paused, sla_hours, satisfaction_rating, ...).
    """
    # ----- 8 intake channels (Section 3) -----
    SOURCE_CHOICES = [
        ('walk_in', 'Walk-in (Front Desk)'),
        ('phone', 'Phone (Call Centre)'),
        ('email', 'Email (Shared Mailbox)'),
        ('web_form', 'Website / Web Form'),
        ('mobile_app', 'Mobile App'),
        ('social_media', 'Social Media'),
        ('letter', 'Letters / Correspondence'),
        ('referral', 'Institutional Referral'),
        # Legacy aliases - kept so existing data remains valid
        ('call_center', 'Call Center (legacy)'),
        ('physical', 'Physical (legacy)'),
        ('online', 'Online (legacy)'),
    ]

    # ----- 9 statuses (Section 7.1) -----
    STATUS_CHOICES = [
        ('new', 'Received'),
        ('under_review', 'Under Review'),
        ('assigned', 'Assigned'),
        ('in_progress', 'In Progress'),
        ('pending_info', 'Pending Additional Information'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
        ('escalated', 'Escalated'),
        ('reopened', 'Reopened'),
        # Legacy
        ('pending', 'Pending'),
    ]

    HANDLER_STATUSES = ['in_progress', 'pending_info', 'resolved', 'closed', 'reopened']

    PRIORITY_CHOICES = ComplaintType.PRIORITY_CHOICES

    SLA_MODE_CHOICES = [
        ('manual', 'Manual'),
        ('auto', 'Auto'),
    ]

    # ----- Registration form fields (Section 5) -----
    case_number = models.CharField(max_length=20, unique=True, default='', blank=True)
    full_name = models.CharField(max_length=150, default='', blank=True)
    phone_number = models.CharField(max_length=30, default='', blank=True)
    complaint_source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='web_form')
    email = models.EmailField(blank=True, null=True)
    national_id = models.CharField(max_length=50, blank=True, null=True)
    member_number = models.CharField(max_length=50, blank=True, default='')
    bank_institution = models.CharField(max_length=255, blank=True, null=True)

    # ----- Complaint details (Section 5) -----
    complaint_title = models.CharField(max_length=200, default='', blank=True)
    complaint_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True, null=True)

    # ----- Assignment & response (Section 5) -----
    response_due_hours = models.PositiveIntegerField(
        default=240,
        help_text='SLA window in hours.  Auto-filled from SLAConfiguration when auto-mode is on.',
    )

    # ----- System-generated fields -----
    system_code = models.CharField(max_length=20, blank=True, default='')
    registered_at = models.DateTimeField(auto_now_add=True)

    # ----- Status (Section 7.1) -----
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')

    # ----- Foreign keys (Section 5, 6) -----
    scheme = models.ForeignKey(
        Scheme,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='complaints',
    )
    complaint_type = models.ForeignKey(
        ComplaintType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='complaints',
    )
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='complaints',
    )
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_complaints',
        limit_choices_to={'role__in': ['handler', 'intake_officer']},
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

    # ----- Escalation (Section 9) -----
    ESCALATION_LEVEL_CHOICES = [
        (0, 'None'),
        (1, 'Level 1 - Supervisor'),
        (2, 'Level 2 - Manager'),
        (3, 'Level 3 - Director'),
        (4, 'Level 4 - Senior Management'),
    ]
    escalation_level = models.PositiveSmallIntegerField(
        choices=ESCALATION_LEVEL_CHOICES,
        default=0,
    )

    # ----- SLA (Section 14) -----
    # Stored in hours to match the design's resolution windows.
    sla_hours = models.PositiveIntegerField(default=0)
    sla_mode = models.CharField(max_length=10, choices=SLA_MODE_CHOICES, default='auto')
    sla_deadline = models.DateTimeField(null=True, blank=True)

    # Clock-pause (Section 14: pause when "Pending Additional Information")
    clock_paused = models.BooleanField(default=False)
    clock_paused_at = models.DateTimeField(null=True, blank=True)
    clock_paused_duration = models.DurationField(null=True, blank=True)

    # ----- Reopen tracking (Section 7.1 status: Reopened) -----
    is_reopened = models.BooleanField(default=False)
    reopened_count = models.PositiveSmallIntegerField(default=0)
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reopened_complaints',
    )
    reopen_reason = models.TextField(blank=True, default='')

    # ----- Final resolution (Section 5) -----
    final_resolution = models.TextField(blank=True, default='')
    resolution_proof = models.FileField(
        upload_to=complaint_proof_upload_path,
        null=True,
        blank=True,
    )
    date_closed = models.DateTimeField(null=True, blank=True)

    # ----- Member satisfaction (Section 5, 12.2) -----
    satisfaction_rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text='1-5 rating submitted by complainant post-closure.',
    )
    satisfaction_comments = models.TextField(blank=True, default='')
    satisfaction_submitted_at = models.DateTimeField(null=True, blank=True)

    # ----- Audit / metadata (Section 10) -----
    # Note: 'date_entered_system' (below) already serves as the created_at
    # timestamp, so we don't add a separate created_at field.
    updated_at = models.DateTimeField(default=timezone.now)

    # ----- Legacy fields (kept for backward compatibility with existing data) -----
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
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='web_form')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    date_of_complaint = models.DateField(null=True, blank=True)
    date_entered_system = models.DateTimeField(auto_now_add=True)
    date_assigned = models.DateTimeField(null=True, blank=True)
    sla_days = models.PositiveIntegerField(default=0, help_text='Legacy field, use sla_hours.')
    sla_deadline_date = models.DateField(null=True, blank=True, help_text='Legacy date-only SLA field.')
    date_resolved = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date_entered_system']
        indexes = [
            models.Index(fields=['scheme', 'status']),
            models.Index(fields=['escalation_level', 'status']),
            models.Index(fields=['sla_deadline']),
        ]

    def __str__(self):
        return self.case_number or f'Complaint {self.pk}'

    # -----------------------------------------------------------------
    # SLA engine
    # -----------------------------------------------------------------
    def save(self, *args, **kwargs):
        # Auto-generate identifiers on first save
        if not self.case_number:
            self.case_number = self._generate_case_number()
        if not self.system_code:
            self.system_code = self._generate_system_code()

        # Auto-suggest priority from complaint type if not explicitly set
        if self.complaint_type and (not self.priority or self.priority == 'medium'):
            # Only override if the user did not explicitly pick a different value
            if self.pk is None:  # new complaint
                self.priority = self.complaint_type.default_priority

        # Track status change before save (for StatusHistory)
        is_status_change = False
        old_status = None
        if self.pk:
            try:
                original = Complaint.objects.get(pk=self.pk)
                if original.status != self.status:
                    is_status_change = True
                    old_status = original.status
            except Complaint.DoesNotExist:
                pass

        # Auto-pause / resume SLA clock based on status transitions
        if self.status == 'pending_info' and not self.clock_paused:
            self.clock_paused = True
            self.clock_paused_at = timezone.now()
        elif self.status != 'pending_info' and self.clock_paused and self.clock_paused_at:
            # Resume: add the paused duration to the SLA deadline
            pause_delta = timezone.now() - self.clock_paused_at
            self.clock_paused_duration = (self.clock_paused_duration or timedelta()) + pause_delta
            if self.sla_deadline:
                self.sla_deadline = self.sla_deadline + pause_delta
            self.clock_paused = False
            self.clock_paused_at = None

        # Compute SLA deadline (in hours)
        if self.sla_mode == 'auto':
            if self.sla_hours == 0:
                self.sla_hours = SLAConfiguration.get_max_hours(
                    self.priority, scheme=self.scheme, complaint_type=self.complaint_type
                )
            self.response_due_hours = self.sla_hours
            base_time = self.date_assigned or self.registered_at or timezone.now()
            self.sla_deadline = base_time + timedelta(hours=self.sla_hours)
            self.sla_deadline_date = self.sla_deadline.date()

        # Set date_assigned on first assignment
        if self.assigned_to and not self.date_assigned:
            self.date_assigned = timezone.now()

        # Set date_resolved / date_closed
        if self.status == 'resolved' and not self.date_resolved:
            self.date_resolved = timezone.now()
        if self.status == 'closed' and not self.date_closed:
            self.date_closed = timezone.now()

        super().save(*args, **kwargs)

        # Record status history (post-save, only when status actually changed)
        if is_status_change and old_status is not None:
            StatusHistory.objects.create(
                complaint=self,
                old_status=old_status,
                new_status=self.status,
                changed_by=self._last_changer,
            )

    # Internal: set by views/services just before save to attribute the change
    _last_changer = None

    def _generate_system_code(self):
        year = timezone.now().year
        prefix = f'SMP-{year}-'
        last = Complaint.objects.filter(system_code__startswith=prefix).order_by('-system_code').first()
        last_index = 0
        if last and last.system_code:
            try:
                last_index = int(last.system_code.split('-')[-1])
            except (ValueError, IndexError):
                last_index = 0
        return f'{prefix}{last_index + 1:04d}'

    @classmethod
    def _generate_case_number(cls):
        year = timezone.now().year
        prefix = f'CMP-{year}-'
        last = cls.objects.filter(case_number__startswith=prefix).order_by('-case_number').first()
        last_index = 0
        if last and last.case_number:
            try:
                last_index = int(last.case_number.split('-')[-1])
            except (ValueError, IndexError):
                last_index = 0
        return f'{prefix}{last_index + 1:04d}'

    # -----------------------------------------------------------------
    # Properties used by templates / dashboards / escalation engine
    # -----------------------------------------------------------------
    @property
    def is_overdue(self):
        """A complaint is overdue when its deadline has passed and it is not in a terminal state."""
        if not self.sla_deadline or self.status in {'resolved', 'closed', 'reopened'}:
            return False
        if self.clock_paused:
            return False
        return timezone.now() > self.sla_deadline

    @property
    def days_remaining(self):
        if not self.sla_deadline:
            return None
        remaining = self.sla_deadline - timezone.now()
        return remaining.days

    @property
    def hours_remaining(self):
        if not self.sla_deadline:
            return None
        delta = self.sla_deadline - timezone.now()
        return int(delta.total_seconds() // 3600)

    @property
    def traffic_light(self):
        """
        Section 14 - SLA Traffic Light:
          - green: more than 25% of the SLA window remaining
          - amber: within 25% of the deadline OR 75% consumed
          - red:   overdue
        """
        if self.status in {'resolved', 'closed'}:
            return 'green'
        if self.is_overdue:
            return 'red'
        if not self.sla_deadline or not self.sla_hours:
            return 'green'
        total = self.sla_hours * 3600
        remaining = (self.sla_deadline - timezone.now()).total_seconds()
        if total <= 0:
            return 'green'
        pct_remaining = remaining / total
        if pct_remaining <= 0.25:
            return 'amber'
        return 'green'

    @property
    def sla_pct_consumed(self):
        """Returns the percentage of the SLA window consumed (0-100, can exceed 100 if overdue)."""
        if not self.sla_deadline or not self.sla_hours or self.status in {'resolved', 'closed'}:
            return 0
        total = self.sla_hours * 3600
        if total <= 0:
            return 0
        elapsed = (timezone.now() - (self.date_assigned or self.registered_at)).total_seconds()
        # Subtract paused duration
        paused = (self.clock_paused_duration or timedelta()).total_seconds()
        elapsed -= paused
        return max(0, int((elapsed / total) * 100))

    @property
    def has_action_taken(self):
        return self.actions.filter(action_taken=True).exists()

    @property
    def escalation_label(self):
        return self.get_escalation_level_display()

    def can_be_reopened(self):
        return self.status == 'closed' and self.reopened_count < 3


# ---------------------------------------------------------------------------
# Audit / history tables (Section 10, 12.2)
# ---------------------------------------------------------------------------

class StatusHistory(models.Model):
    """
    Immutable log of status changes.  Section 10 (Audit Trail).
    """
    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.CASCADE,
        related_name='status_history',
    )
    old_status = models.CharField(max_length=20, blank=True, default='')
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='status_changes',
    )
    note = models.TextField(blank=True, default='')
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']
        verbose_name_plural = 'Status histories'

    def __str__(self):
        return f'{self.complaint_id}: {self.old_status} → {self.new_status}'


class EscalationLog(models.Model):
    """
    Section 9 Escalation Matrix - every escalation is logged.
    """
    ESCALATION_LEVELS = Complaint.ESCALATION_LEVEL_CHOICES

    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.CASCADE,
        related_name='escalation_logs',
    )
    level = models.PositiveSmallIntegerField(choices=ESCALATION_LEVELS)
    triggered_at = models.DateTimeField(auto_now_add=True)
    trigger_reason = models.CharField(max_length=255, default='overdue')
    escalated_to = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='escalations_received',
    )
    escalated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='escalations_triggered',
        help_text='System (null) when triggered automatically by the SLA engine.',
    )
    notes = models.TextField(blank=True, default='')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='escalations_resolved',
    )
    resolution_notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-triggered_at']
        indexes = [
            models.Index(fields=['complaint', '-triggered_at']),
            models.Index(fields=['level', '-triggered_at']),
        ]

    def __str__(self):
        return f'Escalation L{self.level} for {self.complaint_id} at {self.triggered_at:%Y-%m-%d %H:%M}'


class ComplaintAction(models.Model):
    """Track actions taken on complaints (status changes, etc.)."""
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
        return self.file.name if self.file else 'attachment'

    @property
    def file_url(self):
        if not self.file:
            return ''
        return self.file.url


class SatisfactionSurvey(models.Model):
    """
    Section 5 & 12.2 Satisfaction Surveys - post-closure 1-5 rating.
    """
    complaint = models.OneToOneField(
        Complaint,
        on_delete=models.CASCADE,
        related_name='satisfaction_survey',
    )
    rating = models.PositiveSmallIntegerField(
        help_text='1 (very dissatisfied) - 5 (very satisfied)',
    )
    comments = models.TextField(blank=True, default='')
    submitted_at = models.DateTimeField(auto_now_add=True)
    submitted_by_email = models.EmailField(blank=True, default='')

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f'Rating {self.rating}/5 for {self.complaint_id}'


class AuditEntry(models.Model):
    """
    Section 10 - Immutable field-level audit log.  Every save() on a Complaint
    (and its related models) produces one or more AuditEntry rows recording
    what changed, by whom, and when.  Old/new values are stored as text so
    the table never gets out of sync with the source schema.

    Retention: minimum 7 years (per Section 10 / 15.4 of the design).
    """
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'
    ACTION_CHOICES = [
        (ACTION_CREATE, 'Created'),
        (ACTION_UPDATE, 'Updated'),
        (ACTION_DELETE, 'Deleted'),
    ]

    content_type = models.CharField(max_length=100, help_text='app_label.ModelName')
    object_id = models.PositiveBigIntegerField()
    object_repr = models.CharField(max_length=255, blank=True, default='')
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    field_name = models.CharField(max_length=100, blank=True, default='')
    old_value = models.TextField(blank=True, default='')
    new_value = models.TextField(blank=True, default='')
    actor = models.ForeignKey(
        'User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='audit_entries',
    )
    actor_label = models.CharField(max_length=255, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['content_type', 'object_id', '-timestamp']),
            models.Index(fields=['actor', '-timestamp']),
            models.Index(fields=['action', '-timestamp']),
        ]
        verbose_name_plural = 'Audit entries'

    def __str__(self):
        return f'{self.action} {self.content_type}#{self.object_id} {self.field_name or "-"} @ {self.timestamp:%Y-%m-%d %H:%M}'

    def save(self, *args, **kwargs):
        # Entries are append-only at the application layer; admins can still
        # bulk-delete for retention cleanup.
        super().save(*args, **kwargs)



class SystemConfiguration(models.Model):
    """Singleton model for global system settings (email, etc.)."""
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

    # Notification toggle (per Section 8.2 - 50% / 75% reminders)
    enable_50pct_reminder = models.BooleanField(default=True)
    enable_75pct_reminder = models.BooleanField(default=True)
    enable_complainant_emails = models.BooleanField(default=True)
    enable_escalation_emails = models.BooleanField(default=True)

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
    """
    In-app notification log.  Now extended to track delivery channel + recipient type
    so the design's per-recipient notifications (Section 8.2) can be filtered.
    """
    NOTIFICATION_TYPES = [
        ('new_complaint', 'New Complaint'),
        ('handler_assigned', 'Handler Assigned'),
        ('status_update', 'Status Update'),
        ('sla_approaching', 'SLA Approaching'),
        ('sla_exceeded', 'SLA Exceeded'),
        ('sla_reminder_50', 'SLA Reminder 50%'),
        ('sla_reminder_75', 'SLA Reminder 75%'),
        ('escalation', 'Escalation Alert'),
        ('complainant_acknowledgement', 'Complainant Acknowledgement'),
        ('complainant_status_update', 'Complainant Status Update'),
        ('complainant_resolved', 'Complainant Resolved'),
        ('complainant_closed', 'Complainant Closed'),
        ('note_added', 'Note Added'),
        ('action_added', 'Action Added'),
        ('complaint_updated', 'Complaint Updated'),
        ('complaint_reopened', 'Complaint Reopened'),
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

    RECIPIENT_TYPES = [
        ('handler', 'Case Officer'),
        ('supervisor', 'Supervisor'),
        ('manager', 'Manager'),
        ('director', 'Director'),
        ('admin', 'Admin'),
        ('complainant', 'Complainant'),
        ('system', 'System'),
    ]

    DELIVERY_CHANNELS = [
        ('in_app', 'In-App'),
        ('email', 'Email'),
        ('sms', 'SMS'),
        ('teams', 'Microsoft Teams'),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True,
        help_text='Internal staff recipient.  Null for complainant notifications.',
    )
    performed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='notifications_performed',
    )
    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, null=True, blank=True, related_name='notifications',
    )
    notification_type = models.CharField(max_length=40, choices=NOTIFICATION_TYPES)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_LEVELS, default='info')
    recipient_type = models.CharField(
        max_length=20, choices=RECIPIENT_TYPES, default='handler',
    )
    delivery_channel = models.CharField(
        max_length=10, choices=DELIVERY_CHANNELS, default='in_app',
    )
    recipient_email = models.EmailField(blank=True, default='')
    recipient_phone = models.CharField(max_length=30, blank=True, default='')
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at']),
            models.Index(fields=['complaint', '-created_at']),
            models.Index(fields=['notification_type', '-created_at']),
        ]

    def __str__(self):
        return f'[{self.notification_type}] {self.message[:60]}'

    def mark_as_read(self):
        """Mark notification as read and set read_at timestamp."""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])


