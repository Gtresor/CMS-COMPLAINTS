import os, sys, django

# Set the settings module
os.environ['DJANGO_SETTINGS_MODULE'] = 'rssp_cms.settings.development'
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import django
django.setup()
from complaints.models import User

# Delete old users
User.objects.filter(username='experty').delete()
User.objects.filter(username='expery').delete()
User.objects.filter(username='handler1').delete()
User.objects.filter(username='handler2').delete()

# Create admin user with role='admin'
admin_user = User.objects.create_user(
    username='expery',
    password='Admin123',
    email='expery@test.com',
    first_name='Admin',
    last_name='User',
    role='admin',
    is_staff=True,
    is_superuser=True,
)
print(f"Admin user 'expery' created successfully with role='admin'")

# Create handler users for the assigned_to dropdown
handler1 = User.objects.create_user(
    username='handler1', 
    password='Handler@123', 
    role='handler', 
    first_name='John', 
    last_name='Doe'
)
handler2 = User.objects.create_user(
    username='handler2', 
    password='Handler@123', 
    role='handler', 
    first_name='Jane', 
    last_name='Smith'
)
print(f"Handler users created: handler1, handler2")
print(f"\nLogin credentials:")
print(f"  Username: expery")
print(f"  Password: Admin123")