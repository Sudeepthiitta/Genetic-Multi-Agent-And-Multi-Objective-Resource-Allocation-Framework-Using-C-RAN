from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    contact = models.CharField(max_length=30)
    address = models.TextField()
    profile_image = models.ImageField(upload_to='profile_images/', blank=True, null=True)

    groups = models.ManyToManyField(
        'auth.Group',
        related_name='allocation_user_set',
        blank=True,
        help_text='The groups this user belongs to.',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='allocation_user_set',
        blank=True,
        help_text='Specific permissions for this user.',
        verbose_name='user permissions',
    )

class ClientRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('allocated', 'Allocated'),
        ('shortage', 'Shortage'),
        ('rejected', 'Rejected'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    cpu = models.IntegerField()
    memory = models.IntegerField()
    storage = models.IntegerField()
    priority = models.FloatField()
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='pending')
    shortage_note = models.CharField(max_length=255, blank=True)
    allocated_cpu = models.IntegerField(default=0)
    allocated_memory = models.IntegerField(default=0)
    allocated_storage = models.IntegerField(default=0)
    source = models.CharField(max_length=30, default='manual')
    feedback_status = models.CharField(max_length=30, default='pending')
    feedback_note = models.CharField(max_length=255, blank=True)
    requested_extra_cpu = models.IntegerField(default=0)
    requested_extra_memory = models.IntegerField(default=0)
    requested_extra_storage = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Request by {self.user.username} - {self.status}"

class DonorResource(models.Model):
    SHARE_CHOICES = [
        ('share', 'Share'),
        ('keep', 'Keep'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    cpu_allocated = models.IntegerField()
    memory_allocated = models.IntegerField()
    storage_allocated = models.IntegerField()
    extra_cpu = models.IntegerField()
    extra_memory = models.IntegerField()
    extra_storage = models.IntegerField()
    share_decision = models.CharField(max_length=20, choices=SHARE_CHOICES)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class UserNotification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    request = models.ForeignKey(ClientRequest, on_delete=models.CASCADE, null=True, blank=True)
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class ChatMessage(models.Model):
    sender = models.ForeignKey(User, related_name='sent_messages', on_delete=models.CASCADE)
    receiver = models.ForeignKey(User, related_name='received_messages', on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

class Offer(models.Model):
    # Assuming offers are related to requests or general
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
