from rest_framework import serializers
from .models import Attachment, Category, Complaint, ComplaintNote, Department, Notification, User


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name', 'default_sla_days']


class UserSerializer(serializers.ModelSerializer):
    department = DepartmentSerializer(read_only=True)
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        source='department',
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'role',
            'phone',
            'department',
            'department_id',
        ]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'default_sla_days']


class ComplaintNoteSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)

    class Meta:
        model = ComplaintNote
        fields = ['id', 'complaint', 'author', 'note', 'created_at']


class AttachmentSerializer(serializers.ModelSerializer):
    file = serializers.FileField(read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Attachment
        fields = ['id', 'complaint', 'file', 'file_url', 'file_type', 'uploaded_at']
        read_only_fields = ['id', 'complaint', 'file', 'file_url', 'file_type', 'uploaded_at']

    def get_file_url(self, obj):
        if not obj.file:
            return None
        request = self.context.get('request')
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url


class AttachmentUploadSerializer(serializers.Serializer):
    files = serializers.ListField(
        child=serializers.FileField(),
        allow_empty=False,
        write_only=True,
    )

    def validate_files(self, files):
        allowed_content_types = {
            'application/pdf',
            'image/jpeg',
            'image/jpg',
            'image/png',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/msword',
        }
        allowed_extensions = ('.pdf', '.jpg', '.jpeg', '.png', '.docx', '.doc')
        max_size = 10 * 1024 * 1024

        for file in files:
            if file.size > max_size:
                raise serializers.ValidationError(
                    f'File "{file.name}" exceeds the 10MB size limit.'
                )
            content_type = (file.content_type or '').lower()
            if content_type not in allowed_content_types and not file.name.lower().endswith(allowed_extensions):
                raise serializers.ValidationError(
                    f'File "{file.name}" has invalid type. Allowed types are PDF, JPG, PNG, DOCX.'
                )
        return files


class ComplaintSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        source='category',
        write_only=True,
        required=False,
        allow_null=True,
    )
    registered_by = UserSerializer(read_only=True)
    assigned_to = UserSerializer(read_only=True)
    assigned_to_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role='handler'),
        source='assigned_to',
        write_only=True,
        required=False,
        allow_null=True,
    )
    assigned_by = UserSerializer(read_only=True)
    assigned_by_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source='assigned_by',
        write_only=True,
        required=False,
        allow_null=True,
    )
    full_name = serializers.CharField(max_length=150)
    phone_number = serializers.CharField(max_length=30)
    complaint_source = serializers.ChoiceField(choices=Complaint.SOURCE_CHOICES)
    email = serializers.EmailField(required=False)
    national_id = serializers.CharField(max_length=50, required=False)
    bank_institution = serializers.CharField(max_length=255, required=False)
    complaint_title = serializers.CharField(max_length=200)
    complaint_date = serializers.DateField()
    description = serializers.CharField(required=False, allow_blank=True)
    response_due_days = serializers.IntegerField(min_value=1)
    system_code = serializers.CharField(read_only=True)
    registered_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Complaint
        fields = [
            'id', 'case_number', 'full_name', 'phone_number', 'complaint_source',
            'email', 'national_id', 'bank_institution', 'complaint_title',
            'complaint_date', 'description', 'assigned_to', 'response_due_days',
            'system_code', 'registered_at', 'status', 'registered_by',
            'assigned_by', 'category', 'priority', 'sla_mode', 'sla_days',
            'source', 'complainant_name', 'complainant_phone', 'complainant_email',
            'beneficiary_bank', 'date_of_complaint', 'date_entered_system',
            'date_assigned', 'sla_deadline', 'date_resolved'
        ]
        read_only_fields = ['id', 'case_number', 'system_code', 'registered_at']

    def validate_case_number(self, value):
        if Complaint.objects.filter(case_number=value).exists():
            raise serializers.ValidationError("A complaint with this case number already exists.")
        return value

    def validate_phone_number(self, value):
        # Basic phone number validation - can be enhanced as needed
        if not value.replace('+', '').replace('-', '').replace(' ', '').isdigit():
            raise serializers.ValidationError("Phone number must contain only digits, spaces, hyphens, and plus signs.")
        return value

    def validate_assigned_to(self, value):
        if value and value.role != 'handler':
            raise serializers.ValidationError("Assigned user must have the role 'handler'.")
        return value


class NotificationSerializer(serializers.ModelSerializer):
    complaint_case_number = serializers.CharField(source='complaint.case_number', read_only=True, default='')
    performed_by_name = serializers.SerializerMethodField()
    performed_by_role = serializers.SerializerMethodField()
    time_ago = serializers.SerializerMethodField()
    severity_icon = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            'id', 'user', 'complaint', 'complaint_case_number',
            'notification_type', 'message', 'is_read', 'created_at',
            'severity', 'metadata', 'performed_by', 'performed_by_name',
            'performed_by_role', 'time_ago', 'severity_icon',
        ]
        read_only_fields = ['id', 'user', 'created_at']

    def get_performed_by_name(self, obj):
        if obj.performed_by:
            return obj.performed_by.get_full_name() or obj.performed_by.username
        return 'System'

    def get_performed_by_role(self, obj):
        if obj.performed_by:
            return obj.performed_by.role
        return ''

    def get_time_ago(self, obj):
        from django.utils import timezone
        now = timezone.now()
        diff = now - obj.created_at
        if diff.days > 0:
            return f'{diff.days}d ago'
        if diff.seconds >= 3600:
            return f'{diff.seconds // 3600}h ago'
        if diff.seconds >= 60:
            return f'{diff.seconds // 60}m ago'
        return 'Just now'

    def get_severity_icon(self, obj):
        icons = {
            'info': 'fa-circle-info',
            'warning': 'fa-triangle-exclamation',
            'critical': 'fa-circle-exclamation',
        }
        return icons.get(obj.severity, 'fa-circle-info')
