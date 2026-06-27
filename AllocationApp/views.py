
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.conf import settings
from django.utils import timezone
from django.db import models
from django.db.models import Sum
from functools import wraps
import os
import base64
import io
import math
import csv
import json
import re
import urllib.request
import urllib.error
import matplotlib.pyplot as plt
import numpy as np
from .models import User, ClientRequest, DonorResource, UserNotification, ChatMessage
from HybridResourceAllocator import HybridResourceAllocator

# Constants
RESOURCES = ['CPU', 'Memory', 'Storage']
CAPACITIES = {'CPU': 100, 'Memory': 200, 'Storage': 500}

ADMIN_CREDENTIALS = {
    'username': 'admin',
    'password': 'admin'
}

REQUEST_CSV_PATH = os.path.join(settings.BASE_DIR, 'client_requests_live.csv')
REQUEST_CSV_FALLBACK_PATH = os.path.join(settings.BASE_DIR, 'AllocationApp', 'data', 'client_requests_live.csv')

resources = RESOURCES
capacities = CAPACITIES
demands = []
existing = []
propose = []
extension = []


# global username
# global propose, existing, extension
# global resources, capacities, demands

# Utility functions

def get_logged_user(request):
    return request.user if request.user.is_authenticated else None


def is_admin(user):
    return user.is_superuser or user.username == 'admin'


def get_admin_request_user(request):
    admin_user_id = request.session.get('admin_user_id')
    if admin_user_id:
        admin_user = User.objects.filter(id=admin_user_id).first()
        if admin_user and is_admin(admin_user):
            return admin_user
    if request.user.is_authenticated and is_admin(request.user):
        return request.user
    return None


def ensure_directory(path):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def write_csv_atomic(path, fieldnames, rows):
    ensure_directory(path)
    temp_path = path + '.tmp'
    with open(temp_path, 'w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    os.replace(temp_path, path)


def get_register_contact_column():
    rows = fetch_all("SHOW COLUMNS FROM register")
    columns = {row[0] for row in rows}
    if 'contact' in columns:
        return 'contact'
    if 'contact_no' in columns:
        return 'contact_no'
    return None


def ensure_client_request_table():
    con = get_db_connection()
    with con:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS client_requests (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                cpu INT NOT NULL,
                memory INT NOT NULL,
                storage INT NOT NULL,
                priority FLOAT NOT NULL,
                status VARCHAR(50) NOT NULL,
                shortage_note VARCHAR(255) DEFAULT '',
                allocated_cpu INT DEFAULT 0,
                allocated_memory INT DEFAULT 0,
                allocated_storage INT DEFAULT 0,
                source VARCHAR(30) DEFAULT 'manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT NULL
            )
            """
        )
    ensure_table_column("client_requests", "feedback_status", "VARCHAR(30) DEFAULT 'pending'")
    ensure_table_column("client_requests", "feedback_note", "VARCHAR(255) DEFAULT ''")
    ensure_table_column("client_requests", "requested_extra_cpu", "INT DEFAULT 0")
    ensure_table_column("client_requests", "requested_extra_memory", "INT DEFAULT 0")
    ensure_table_column("client_requests", "requested_extra_storage", "INT DEFAULT 0")


def ensure_donor_table():
    con = get_db_connection()
    with con:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS donor_resources (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                cpu_allocated INT NOT NULL,
                memory_allocated INT NOT NULL,
                storage_allocated INT NOT NULL,
                extra_cpu INT NOT NULL,
                extra_memory INT NOT NULL,
                extra_storage INT NOT NULL,
                share_decision VARCHAR(20) NOT NULL,
                note VARCHAR(255) DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def ensure_notification_table():
    con = get_db_connection()
    with con:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_notifications (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                request_id INT DEFAULT NULL,
                message VARCHAR(255) NOT NULL,
                is_read TINYINT(1) DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def ensure_chat_table():
    con = get_db_connection()
    with con:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_chat_messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                sender VARCHAR(100) NOT NULL,
                receiver VARCHAR(100) NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def ensure_offer_table():
    con = get_db_connection()
    with con:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_resource_offers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                request_id INT NOT NULL,
                username VARCHAR(100) NOT NULL,
                offer_cpu INT NOT NULL DEFAULT 0,
                offer_memory INT NOT NULL DEFAULT 0,
                offer_storage INT NOT NULL DEFAULT 0,
                decision VARCHAR(20) NOT NULL,
                note VARCHAR(255) DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_request_user (request_id, username)
            )
            """
        )
    ensure_table_column("user_resource_offers", "is_consumed", "TINYINT(1) DEFAULT 0")


def ensure_workflow_tables():
    pass  # No longer needed with Django models


def require_user(view_func):
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('UserLogin')
        return view_func(request, *args, **kwargs)
    return wrapper


def require_admin(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        admin_user = get_admin_request_user(request)
        if not admin_user:
            return redirect('AdminLogin')
        request.admin_user = admin_user
        return view_func(request, *args, **kwargs)
    return wrapper


def fetch_all(query, params=None):
    con = get_db_connection()
    with con:
        cur = con.cursor()
        cur.execute(query, params or ())
        return cur.fetchall()


def execute_query(query, params=None, many=False):
    con = get_db_connection()
    with con:
        cur = con.cursor()
        if many:
            cur.executemany(query, params or [])
        else:
            cur.execute(query, params or ())
        con.commit()



def get_request_csv_display_path():
    if os.path.exists(REQUEST_CSV_FALLBACK_PATH):
        return REQUEST_CSV_FALLBACK_PATH
    return REQUEST_CSV_PATH


def sync_client_requests_csv():
    fieldnames = [
        'id', 'username', 'cpu', 'memory', 'storage', 'priority', 'status', 'shortage_note',
        'allocated_cpu', 'allocated_memory', 'allocated_storage', 'source', 'created_at',
        'feedback_status', 'feedback_note', 'requested_extra_cpu', 'requested_extra_memory', 'requested_extra_storage'
    ]
    csv_rows = []
    for row in ClientRequest.objects.select_related('user').order_by('id'):
        csv_rows.append({
            'id': int(row.id),
            'username': row.user.username,
            'cpu': int(row.cpu),
            'memory': int(row.memory),
            'storage': int(row.storage),
            'priority': float(row.priority),
            'status': row.status,
            'shortage_note': row.shortage_note or '',
            'allocated_cpu': int(row.allocated_cpu),
            'allocated_memory': int(row.allocated_memory),
            'allocated_storage': int(row.allocated_storage),
            'source': row.source,
            'created_at': timezone.localtime(row.created_at).isoformat() if row.created_at else '',
            'feedback_status': row.feedback_status or 'pending',
            'feedback_note': row.feedback_note or '',
            'requested_extra_cpu': int(row.requested_extra_cpu),
            'requested_extra_memory': int(row.requested_extra_memory),
            'requested_extra_storage': int(row.requested_extra_storage),
        })
    try:
        write_csv_atomic(REQUEST_CSV_PATH, fieldnames, csv_rows)
        return REQUEST_CSV_PATH
    except PermissionError:
        write_csv_atomic(REQUEST_CSV_FALLBACK_PATH, fieldnames, csv_rows)
        return REQUEST_CSV_FALLBACK_PATH


def calculate_base_available_capacity():
    totals = ClientRequest.objects.filter(status='allocated').aggregate(
        used_cpu=Sum('allocated_cpu'),
        used_memory=Sum('allocated_memory'),
        used_storage=Sum('allocated_storage'),
    )
    used_cpu = totals['used_cpu'] or 0
    used_memory = totals['used_memory'] or 0
    used_storage = totals['used_storage'] or 0
    return {
        'CPU': max(0, CAPACITIES['CPU'] - int(used_cpu)),
        'Memory': max(0, CAPACITIES['Memory'] - int(used_memory)),
        'Storage': max(0, CAPACITIES['Storage'] - int(used_storage)),
    }


def calculate_approved_extra_pool():
    totals = DonorResource.objects.filter(
        share_decision__in=['share', 'approve', 'approved']
    ).aggregate(
        extra_cpu=Sum('extra_cpu'),
        extra_memory=Sum('extra_memory'),
        extra_storage=Sum('extra_storage'),
    )
    return {
        'CPU': int(totals['extra_cpu'] or 0),
        'Memory': int(totals['extra_memory'] or 0),
        'Storage': int(totals['extra_storage'] or 0),
    }


def get_request_shortage(cpu, memory, storage):
    available = calculate_base_available_capacity()
    shortage = {
        'CPU': max(0, int(cpu) - available['CPU']),
        'Memory': max(0, int(memory) - available['Memory']),
        'Storage': max(0, int(storage) - available['Storage']),
    }
    return available, shortage


def format_shortage_note(shortage):
    parts = []
    for key in ['CPU', 'Memory', 'Storage']:
        if shortage[key] > 0:
            parts.append(f"{key} shortage {shortage[key]}")
    return ', '.join(parts) if parts else 'No shortage'


def load_user_requests(username):
    rows = ClientRequest.objects.select_related('user').filter(user__username=username).order_by('-id')
    return [serialize_request_row(row) for row in rows]


def load_user_notifications(username):
    rows = UserNotification.objects.select_related('request').filter(
        user__username=username
    ).order_by('-id')
    legacy_fragments = [
        'extra resource request',
        'allocated from base capacity',
        'approval from past users',
        'responded to admin request',
        'requested more resources for request #',
        'admin requests extra resources',
        'i can share',
    ]
    notifications = []
    for row in rows:
        message = row.message or ''
        message_lower = message.lower()
        if any(fragment in message_lower for fragment in legacy_fragments):
            continue
        request_id = row.request_id
        notifications.append({
            'id': int(row.id),
            'request_id': request_id,
            'message': message,
            'is_read': bool(row.is_read),
            'created_at': row.created_at,
        })
    return notifications


def load_user_profile(username):
    user = User.objects.filter(username=username).first()
    if not user:
        return {
            'username': username,
            'contact': '',
            'email': '',
            'address': '',
            'profile_image': '',
        }
    return {
        'username': user.username,
        'contact': user.contact or '',
        'email': user.email or '',
        'address': user.address or '',
        'profile_image': str(user.profile_image or ''),
    }


def update_user_profile(username, contact, email, address, profile_image=None):
    user = User.objects.filter(username=username).first()
    if not user:
        return
    user.contact = contact
    user.email = email
    user.address = address
    if profile_image is None:
        user.save(update_fields=['contact', 'email', 'address'])
    else:
        user.profile_image = profile_image
        user.save(update_fields=['contact', 'email', 'address', 'profile_image'])


def enrich_admin_notifications(notifications):
    enriched = []
    for row in notifications:
        message = row.get('message', '')
        if ' requested more resources for request #' in message:
            continue
        related_user = ''
        if row.get('request_id'):
            request_row = get_request_by_id(row['request_id'])
            if request_row:
                related_user = request_row['username']
        if not related_user:
            if ' sent a new chat message.' in message:
                related_user = message.split(' sent a new chat message.')[0].strip()
            elif ' marked request #' in message:
                related_user = message.split(' marked request #')[0].strip()
        new_row = dict(row)
        new_row['related_user'] = related_user
        enriched.append(new_row)
    return enriched


def count_unread_notifications(username):
    return UserNotification.objects.filter(user__username=username, is_read=False).count()


def mark_notifications_read(username):
    UserNotification.objects.filter(user__username=username, is_read=False).update(is_read=True)


def create_notification(username, message, request_id=None):
    user = User.objects.filter(username=username).first()
    if not user:
        return
    request_row = ClientRequest.objects.filter(id=request_id).first() if request_id else None
    UserNotification.objects.create(user=user, request=request_row, message=message, is_read=False)


def get_primary_admin_user():
    return User.objects.filter(is_superuser=True).order_by('id').first() or User.objects.filter(username='admin').first()


def get_admin_chat_users():
    admin_users = User.objects.filter(is_superuser=True).order_by('id')
    if admin_users.exists():
        return admin_users
    fallback_admin = User.objects.filter(username='admin')
    return fallback_admin


def get_or_create_assistant_user():
    assistant_user = User.objects.filter(username='assistant').first()
    if assistant_user:
        return assistant_user
    assistant_user = User(username='assistant', email='', contact='', address='', is_active=False)
    assistant_user.set_unusable_password()
    assistant_user.save()
    return assistant_user


def load_chat_for_user(username):
    target_user = User.objects.filter(username=username).first()
    admin_users = get_admin_chat_users()
    assistant_user = User.objects.filter(username='assistant').first()
    if not target_user:
        return []
    rows = list(ChatMessage.objects.select_related('sender', 'receiver').filter(
        models.Q(sender=target_user, receiver__in=admin_users) |
        models.Q(sender__in=admin_users, receiver=target_user) |
        (models.Q(sender=assistant_user, receiver=target_user) if assistant_user else models.Q(pk__isnull=True))
    ).order_by('-id')[:100])
    rows.reverse()
    return [
        {
            'id': int(row.id),
            'sender': (
                'admin'
                if row.sender and (row.sender.is_superuser or row.sender.username == 'admin')
                else row.sender.username
            ),
            'receiver': row.receiver.username,
            'message': row.message,
            'created_at': timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M'),
        }
        for row in rows
    ]


def count_unread_admin_chat_notifications(username):
    return UserNotification.objects.filter(
        user__username=username,
        is_read=False,
        message__icontains='Admin sent you a new chat message.'
    ).count()


def mark_admin_chat_notifications_read(username):
    UserNotification.objects.filter(
        user__username=username,
        is_read=False,
        message__icontains='Admin sent you a new chat message.'
    ).update(is_read=True)


def get_ai_target_request(username, message=''):
    user_requests = load_user_requests(username)
    if not user_requests:
        return None, user_requests
    match = re.search(r'request\s*#?\s*(\d+)', message or '', re.IGNORECASE)
    if match:
        target_id = int(match.group(1))
        for row in user_requests:
            if int(row.get('id', 0)) == target_id:
                return row, user_requests
    return user_requests[0], user_requests


def explain_status_text(status):
    mapping = {
        'waiting_for_admin': 'waiting for admin evaluation',
        'allocated': 'already allocated',
        'rejected': 'rejected',
    }
    return mapping.get(status, status or 'unknown')


def generate_ai_help_reply(message, username=None):
    text = (message or '').strip().lower()
    target_request = None
    user_requests = []
    if username:
        target_request, user_requests = get_ai_target_request(username, message)
        allocated_count = len([row for row in user_requests if row.get('status') == 'allocated'])
        pending_count = len([row for row in user_requests if row.get('status') == 'waiting_for_admin'])
    else:
        allocated_count = 0
        pending_count = 0

    if not text:
        if username and user_requests:
            return f"You currently have {len(user_requests)} request(s): {allocated_count} allocated and {pending_count} pending admin review. Ask me about status, allocation, priority, or latest request details."
        return "I can help explain request status, allocation amount, priority meaning, and how Fixed, Hybrid, and Genetic comparison works."
    if any(word in text for word in ['hello', 'hi', 'hey']):
        if username and user_requests:
            return f"Hello. You have {len(user_requests)} request(s) in the system. Your latest request is #{target_request['id']} and it is {explain_status_text(target_request.get('status'))}."
        return "Hello. I can help with request status, allocation, priority, and algorithm comparison."
    if 'priority' in text:
        return "Priority tells the allocator which requests are more important during congestion. Higher priority requests are favored more strongly when CPU, memory, or storage are limited."
    if 'cpu' in text or 'memory' in text or 'storage' in text:
        return "In this project, CPU means processing need, memory means running workload need, and storage means saved data need. A common practical pattern is CPU < Memory < Storage."
    if 'best' in text or 'better' in text or 'green' in text:
        return "The dashboard comparison is kept in admin view. The project compares Fixed, Hybrid, and Genetic honestly using fulfillment, weighted unfulfilled demand, utilization, fairness, and balance."
    if 'graph' in text or 'compare' in text or 'comparison' in text or 'algorithm' in text:
        return "The comparison graph and proof metrics are generated in admin view after Fixed, Hybrid, and Genetic are run on submitted demands. Users only see their own request results and allocated resources."
    if target_request and ('status' in text or 'waiting' in text or 'pending' in text or 'latest' in text):
        return f"Your latest tracked request is #{target_request['id']}. It is currently {explain_status_text(target_request.get('status'))}. Requested: CPU {target_request['cpu']}, Memory {target_request['memory']}, Storage {target_request['storage']}."
    if target_request and ('allocate' in text or 'allocated' in text or 'resource' in text):
        return f"Request #{target_request['id']} currently shows allocated CPU {target_request['allocated_cpu']}, Memory {target_request['allocated_memory']}, and Storage {target_request['allocated_storage']}. If these are zero, admin has not applied the final allocation yet."
    if 'how many' in text or 'summary' in text or 'count' in text:
        return f"You currently have {len(user_requests)} request(s): {allocated_count} allocated, {pending_count} pending, and {len([row for row in user_requests if row.get('status') == 'rejected'])} rejected."
    if 'why' in text and 'reject' in text:
        return "A request is usually rejected when available capacity is not enough or the admin evaluation chooses not to allocate it. You can check the request row and updates panel for its current state."
    if 'demo' in text or 'presentation' in text or 'viva' in text:
        return "For presentation, explain that users submit demands, admin runs Fixed, Hybrid, and Genetic, and the framework compares overall fulfillment, fairness, and priority-aware utilization."
    if username and not user_requests:
        return "You have not submitted a request yet. Start by entering CPU, memory, storage, and priority in the request form."
    return "I can answer about your latest request status, allocated resources, pending review, priority meaning, and how the Fixed, Hybrid, and Genetic evaluation works."


def build_ai_support_instructions(username=None):
    base = (
        "You are an AI support assistant inside a cloud resource allocation project dashboard. "
        "Answer like a helpful product assistant for student users. "
        "Focus on client-side doubts, project explanation, request workflow, CPU/memory/storage meaning, priority meaning, "
        "and how Fixed, Hybrid, and Genetic evaluation works. "
        "Keep answers concise, clear, and practical. "
        "Do not mention internal implementation details unless the user asks. "
        "If the user asks about their own request status or allocation, use only the provided context. "
        "If a fact is unavailable, say that clearly instead of guessing."
    )
    if username:
        base += f" Current logged-in user: {username}."
    return base


def build_ai_support_input(username, message):
    target_request, user_requests = get_ai_target_request(username, message)
    latest_context = "No requests submitted yet."
    if target_request:
        latest_context = (
            f"Latest relevant request #{target_request['id']}: status={target_request['status']}, "
            f"requested CPU={target_request['cpu']}, Memory={target_request['memory']}, Storage={target_request['storage']}, "
            f"allocated CPU={target_request['allocated_cpu']}, Memory={target_request['allocated_memory']}, Storage={target_request['allocated_storage']}."
        )
    summary = (
        f"User has {len(user_requests)} total request(s), "
        f"{len([row for row in user_requests if row.get('status') == 'allocated'])} allocated, "
        f"{len([row for row in user_requests if row.get('status') == 'waiting_for_admin'])} pending, "
        f"{len([row for row in user_requests if row.get('status') == 'rejected'])} rejected. "
    )
    return summary + latest_context + f" User question: {message}"


def request_openai_ai_reply(username, message):
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not api_key:
        return None

    model = os.environ.get('OPENAI_MODEL', '').strip() or 'gpt-4.1-mini'
    payload = {
        "model": model,
        "instructions": build_ai_support_instructions(username),
        "input": build_ai_support_input(username, message),
        "max_output_tokens": 220,
    }
    req = urllib.request.Request(
        'https://api.openai.com/v1/responses',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            body = json.loads(response.read().decode('utf-8'))
            output_text = body.get('output_text')
            if output_text:
                return output_text.strip()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None
    return None


def generate_support_reply(message, username=None):
    ai_reply = request_openai_ai_reply(username, message)
    if ai_reply:
        return ai_reply
    return generate_ai_help_reply(message, username)


def load_admin_chat():
    rows = ChatMessage.objects.select_related('sender', 'receiver').order_by('-id')[:40]
    return [
        {
            'id': int(row.id),
            'sender': (
                'admin'
                if row.sender and (row.sender.is_superuser or row.sender.username == 'admin')
                else row.sender.username
            ),
            'receiver': (
                'admin'
                if row.receiver and (row.receiver.is_superuser or row.receiver.username == 'admin')
                else row.receiver.username
            ),
            'message': row.message,
            'created_at': timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M'),
        }
        for row in rows
    ]


def load_conversation(user_a, user_b):
    left_user = User.objects.filter(username=user_a).first()
    right_user = User.objects.filter(username=user_b).first()
    if not left_user or not right_user:
        return []
    rows = list(ChatMessage.objects.select_related('sender', 'receiver').filter(
        models.Q(sender=left_user, receiver=right_user) |
        models.Q(sender=right_user, receiver=left_user)
    ).order_by('-id')[:100])
    rows.reverse()
    return [
        {
            'id': int(row.id),
            'sender': row.sender.username,
            'receiver': row.receiver.username,
            'message': row.message,
            'created_at': timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M'),
        }
        for row in rows
    ]


def load_admin_conversation(username):
    target_user = User.objects.filter(username=username).first()
    admin_users = get_admin_chat_users()
    if not target_user or not admin_users.exists():
        return []
    rows = list(ChatMessage.objects.select_related('sender', 'receiver').filter(
        models.Q(sender__in=admin_users, receiver=target_user) |
        models.Q(sender=target_user, receiver__in=admin_users)
    ).order_by('-id')[:100])
    rows.reverse()
    return [
        {
            'id': int(row.id),
            'sender': (
                'admin'
                if row.sender and (row.sender.is_superuser or row.sender.username == 'admin')
                else row.sender.username
            ),
            'receiver': (
                'admin'
                if row.receiver and (row.receiver.is_superuser or row.receiver.username == 'admin')
                else row.receiver.username
            ),
            'message': row.message,
            'created_at': timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M'),
        }
        for row in rows
    ]


def load_chat_users():
    admin_users = get_admin_chat_users()
    if not admin_users.exists():
        return []
    usernames = set(
        ChatMessage.objects.filter(sender__in=admin_users).values_list('receiver__username', flat=True)
    )
    usernames.update(
        ChatMessage.objects.filter(receiver__in=admin_users).values_list('sender__username', flat=True)
    )
    return sorted(username for username in usernames if username and username not in set(admin_users.values_list('username', flat=True)))


def create_chat_message(sender, receiver, message):
    if isinstance(sender, User):
        sender_user = sender
    elif str(sender) == 'admin':
        sender_user = get_primary_admin_user()
    elif str(sender) == 'assistant':
        sender_user = get_or_create_assistant_user()
    else:
        sender_user = User.objects.filter(username=str(sender)).first()

    if isinstance(receiver, User):
        receiver_user = receiver
    elif str(receiver) == 'admin':
        receiver_user = get_primary_admin_user()
    else:
        receiver_user = User.objects.filter(username=str(receiver)).first()
    if not sender_user or not receiver_user:
        return None
    return ChatMessage.objects.create(sender=sender_user, receiver=receiver_user, message=message)


def load_other_registered_users(exclude_username):
    return list(
        User.objects.exclude(username=exclude_username).order_by('username').values_list('username', flat=True)
    )


def save_user_offer(request_id, username, cpu, memory, storage, decision, note=''):
    existing_rows = fetch_all(
        "SELECT id FROM user_resource_offers WHERE request_id = %s AND username = %s",
        (request_id, username)
    )
    if existing_rows:
        execute_query(
            """
            UPDATE user_resource_offers
            SET offer_cpu = %s, offer_memory = %s, offer_storage = %s, decision = %s, note = %s, is_consumed = 0
            WHERE request_id = %s AND username = %s
            """,
            (cpu, memory, storage, decision, note, request_id, username)
        )
    else:
        execute_query(
            """
            INSERT INTO user_resource_offers(request_id, username, offer_cpu, offer_memory, offer_storage, decision, note, is_consumed)
            VALUES(%s, %s, %s, %s, %s, %s, %s, 0)
            """,
            (request_id, username, cpu, memory, storage, decision, note)
        )


def load_user_offers_for_user(username):
    rows = fetch_all(
        """
        SELECT o.id, o.request_id, o.offer_cpu, o.offer_memory, o.offer_storage, o.decision, o.note, o.is_consumed,
               r.username, r.cpu, r.memory, r.storage, r.status
        FROM user_resource_offers o
        JOIN client_requests r ON r.id = o.request_id
        WHERE o.username = %s
        ORDER BY o.id DESC
        """,
        (username,)
    )
    return [
        {
            'id': int(row[0]),
            'request_id': int(row[1]),
            'offer_cpu': int(row[2]),
            'offer_memory': int(row[3]),
            'offer_storage': int(row[4]),
            'decision': row[5],
            'note': row[6] or '',
            'is_consumed': bool(row[7]),
            'request_user': row[8],
            'request_cpu': int(row[9]),
            'request_memory': int(row[10]),
            'request_storage': int(row[11]),
            'request_status': row[12],
        }
        for row in rows
    ]


def load_request_offers(request_id=None):
    query = """
        SELECT o.id, o.request_id, o.username, o.offer_cpu, o.offer_memory, o.offer_storage, o.decision, o.note, o.is_consumed
        FROM user_resource_offers o
    """
    params = ()
    if request_id is not None:
        query += " WHERE o.request_id = %s"
        params = (request_id,)
    query += " ORDER BY o.request_id DESC, o.id DESC"
    rows = fetch_all(query, params)
    return [
        {
            'id': int(row[0]),
            'request_id': int(row[1]),
            'username': row[2],
            'offer_cpu': int(row[3]),
            'offer_memory': int(row[4]),
            'offer_storage': int(row[5]),
            'decision': row[6],
            'note': row[7] or '',
            'is_consumed': bool(row[8]),
        }
        for row in rows
    ]


def calculate_approved_offer_pool(request_id):
    rows = fetch_all(
        """
        SELECT COALESCE(SUM(offer_cpu), 0), COALESCE(SUM(offer_memory), 0), COALESCE(SUM(offer_storage), 0)
        FROM user_resource_offers
        WHERE request_id = %s AND LOWER(decision) = 'approve' AND is_consumed = 0
        """,
        (request_id,)
    )
    cpu, memory, storage = rows[0]
    return {'CPU': int(cpu), 'Memory': int(memory), 'Storage': int(storage)}


def mark_request_offers_consumed(request_id):
    execute_query(
        "UPDATE user_resource_offers SET is_consumed = 1 WHERE request_id = %s AND LOWER(decision) = 'approve'",
        (request_id,)
    )


def parse_selected_users(raw_value, exclude_username):
    if not raw_value:
        return load_other_registered_users(exclude_username)
    seen = set()
    selected = []
    for value in raw_value.split(','):
        username_value = value.strip()
        if not username_value or username_value == exclude_username or username_value in seen:
            continue
        seen.add(username_value)
        selected.append(username_value)
    return selected


def load_demands_for_algorithms():
    demands_list = []
    rows = ClientRequest.objects.filter(
        status__in=['pending', 'shortage', 'waiting_for_admin', 'waiting_for_user_approval', 'allocated']
    ).order_by('id')
    for row in rows:
        demands_list.append({
            'id': int(row.id),
            'resource_needs': {
                'CPU': int(row.cpu),
                'Memory': int(row.memory),
                'Storage': int(row.storage),
            },
            'priority': float(row.priority)
        })
    return demands_list


def serialize_request_row(row, overall_best_algorithm='', allocated_display=''):
    base_available = calculate_base_available_capacity()
    shortage = {
        'CPU': max(0, int(row.cpu) - base_available['CPU']),
        'Memory': max(0, int(row.memory) - base_available['Memory']),
        'Storage': max(0, int(row.storage) - base_available['Storage']),
    }
    return {
        'id': int(row.id),
        'username': row.user.username,
        'cpu': int(row.cpu),
        'memory': int(row.memory),
        'storage': int(row.storage),
        'priority': float(row.priority),
        'status': row.status,
        'shortage_note': row.shortage_note or '',
        'allocated_cpu': int(row.allocated_cpu),
        'allocated_memory': int(row.allocated_memory),
        'allocated_storage': int(row.allocated_storage),
        'allocated_display': allocated_display or f"{int(row.allocated_cpu)}/{int(row.allocated_memory)}/{int(row.allocated_storage)}",
        'source': row.source,
        'created_at': row.created_at,
        'feedback_status': row.feedback_status or 'pending',
        'feedback_note': row.feedback_note or '',
        'requested_extra_cpu': int(row.requested_extra_cpu),
        'requested_extra_memory': int(row.requested_extra_memory),
        'requested_extra_storage': int(row.requested_extra_storage),
        'shortage_cpu': shortage['CPU'],
        'shortage_memory': shortage['Memory'],
        'shortage_storage': shortage['Storage'],
        'overall_best_algorithm': overall_best_algorithm or '',
    }


def load_all_requests(overall_best_algorithm='', best_allocation_map=None):
    rows = ClientRequest.objects.select_related('user').order_by('id')
    best_allocation_map = best_allocation_map or {}
    return [
        serialize_request_row(row, overall_best_algorithm, best_allocation_map.get(int(row.id), ''))
        for row in rows
    ]


def load_donor_records():
    rows = DonorResource.objects.select_related('user').order_by('-id')
    return [
        {
            'id': int(row.id),
            'username': row.user.username,
            'cpu_allocated': int(row.cpu_allocated),
            'memory_allocated': int(row.memory_allocated),
            'storage_allocated': int(row.storage_allocated),
            'extra_cpu': int(row.extra_cpu),
            'extra_memory': int(row.extra_memory),
            'extra_storage': int(row.extra_storage),
            'share_decision': row.share_decision,
            'note': row.note or '',
            'created_at': row.created_at,
        }
        for row in rows
    ]


def get_request_by_id(request_id):
    row = ClientRequest.objects.select_related('user').filter(id=request_id).first()
    if not row:
        return None
    return serialize_request_row(row)


def update_request_status(request_id, status, note='', allocated=None):
    allocated = allocated or {'CPU': 0, 'Memory': 0, 'Storage': 0}
    row = ClientRequest.objects.filter(id=request_id).first()
    if not row:
        return
    row.status = status
    row.shortage_note = note
    row.allocated_cpu = int(allocated.get('CPU', 0))
    row.allocated_memory = int(allocated.get('Memory', 0))
    row.allocated_storage = int(allocated.get('Storage', 0))
    row.save(update_fields=['status', 'shortage_note', 'allocated_cpu', 'allocated_memory', 'allocated_storage', 'updated_at'])
    sync_client_requests_csv()


def update_request_feedback(request_id, feedback_status, feedback_note='', extra=None):
    extra = extra or {'CPU': 0, 'Memory': 0, 'Storage': 0}
    row = ClientRequest.objects.filter(id=request_id).first()
    if not row:
        return
    row.feedback_status = feedback_status
    row.feedback_note = feedback_note
    row.requested_extra_cpu = int(extra.get('CPU', 0))
    row.requested_extra_memory = int(extra.get('Memory', 0))
    row.requested_extra_storage = int(extra.get('Storage', 0))
    row.save(update_fields=['feedback_status', 'feedback_note', 'requested_extra_cpu', 'requested_extra_memory', 'requested_extra_storage', 'updated_at'])
    sync_client_requests_csv()


def build_algorithm_dashboard_context():
    if len(existing) == 0 or len(propose) == 0 or len(extension) == 0:
        return {}

    all_demands = load_demands_for_algorithms()
    fixed_m = evaluate_allocation_metrics(existing, capacities)
    hybrid_m = evaluate_allocation_metrics(propose, capacities)
    genetic_m = evaluate_allocation_metrics(extension, capacities)
    overall_best_algorithm = determine_overall_best_algorithm(fixed_m, hybrid_m, genetic_m)
    best_allocation_map = build_best_allocation_map(overall_best_algorithm)

    return {
        'proof_rows': [
            {'name': 'Fixed', **fixed_m},
            {'name': 'Hybrid', **hybrid_m},
            {'name': 'Multiobjective', **genetic_m},
        ],
        'comparison_rows': build_request_comparison_rows(all_demands, existing, propose, extension),
        'overall_best_algorithm': overall_best_algorithm,
        'best_allocation_map': best_allocation_map,
        'comparison_fixed': (
            f"Unfulfilled {fmt_metric_change(fixed_m['total_unfulfilled'], genetic_m['total_unfulfilled'], False)}, "
            f"Priority-weighted Unfulfilled {fmt_metric_change(fixed_m['weighted_unfulfilled'], genetic_m['weighted_unfulfilled'], False)}, "
            f"Critical Unfulfilled {fmt_metric_change(fixed_m['critical_unfulfilled'], genetic_m['critical_unfulfilled'], False)}, "
            f"Critical Success {fmt_metric_change(fixed_m['critical_request_success_pct'], genetic_m['critical_request_success_pct'], True)}, "
            f"Users Served {fmt_metric_change(fixed_m['served_requests_pct'], genetic_m['served_requests_pct'], True)}, "
            f"Minimum Satisfaction {fmt_metric_change(fixed_m['min_satisfaction'], genetic_m['min_satisfaction'], True)}, "
            f"Utilization {fmt_metric_change(fixed_m['utilization_pct'], genetic_m['utilization_pct'], True)}, "
            f"Wastage {fmt_metric_change(fixed_m['waste_pct'], genetic_m['waste_pct'], False)}, "
            f"Fairness {fmt_metric_change(fixed_m['fairness_index'], genetic_m['fairness_index'], True)}, "
            f"Load Balance {fmt_metric_change(fixed_m['balance_index'], genetic_m['balance_index'], True)}"
        ),
        'comparison_hybrid': (
            f"Unfulfilled {fmt_metric_change(hybrid_m['total_unfulfilled'], genetic_m['total_unfulfilled'], False)}, "
            f"Priority-weighted Unfulfilled {fmt_metric_change(hybrid_m['weighted_unfulfilled'], genetic_m['weighted_unfulfilled'], False)}, "
            f"Critical Unfulfilled {fmt_metric_change(hybrid_m['critical_unfulfilled'], genetic_m['critical_unfulfilled'], False)}, "
            f"Critical Success {fmt_metric_change(hybrid_m['critical_request_success_pct'], genetic_m['critical_request_success_pct'], True)}, "
            f"Users Served {fmt_metric_change(hybrid_m['served_requests_pct'], genetic_m['served_requests_pct'], True)}, "
            f"Minimum Satisfaction {fmt_metric_change(hybrid_m['min_satisfaction'], genetic_m['min_satisfaction'], True)}, "
            f"Utilization {fmt_metric_change(hybrid_m['utilization_pct'], genetic_m['utilization_pct'], True)}, "
            f"Wastage {fmt_metric_change(hybrid_m['waste_pct'], genetic_m['waste_pct'], False)}, "
            f"Fairness {fmt_metric_change(hybrid_m['fairness_index'], genetic_m['fairness_index'], True)}, "
            f"Load Balance {fmt_metric_change(hybrid_m['balance_index'], genetic_m['balance_index'], True)}"
        ),
    }


def render_admin_dashboard(request, extra_context=None):
    extra_context = extra_context or {}
    base_context = build_algorithm_dashboard_context()
    if extra_context:
        base_context.update(extra_context)
    template_name = base_context.pop('template_name', 'AdminDashboard.html')

    registered_users = list(
        User.objects.filter(is_superuser=False).exclude(username='assistant').order_by('username').values_list('username', flat=True)
    )
    selected_chat_user = request.GET.get('chat_with', '').strip()
    if selected_chat_user not in registered_users:
        selected_chat_user = registered_users[0] if registered_users else ''

    overall_best_algorithm = base_context.get('overall_best_algorithm', '')
    requests = load_all_requests(overall_best_algorithm, base_context.get('best_allocation_map'))
    chat_threads_json = {username: load_admin_conversation(username) for username in registered_users}
    chat_profiles_json = {username: load_user_profile(username) for username in registered_users}
    flash = request.session.pop('flash', None)
    admin_user = getattr(request, 'admin_user', None) or get_admin_request_user(request)

    context = {
        'flash': flash,
        'admin_username': admin_user.username if admin_user else 'admin',
        'registered_users': registered_users,
        'requests': requests,
        'donors': load_donor_records(),
        'base_capacity': calculate_base_available_capacity(),
        'approved_pool': calculate_approved_extra_pool(),
        'selected_chat_user': selected_chat_user,
        'chat_threads_json': chat_threads_json,
        'chat_profiles_json': chat_profiles_json,
        'request_csv_path': get_request_csv_display_path(),
        'capacities': CAPACITIES,
        'resources': RESOURCES,
    }
    context.update(base_context)
    return render(request, template_name, context)


def consume_approved_donor_resources(shortage):
    remaining = {
        'CPU': int(shortage.get('CPU', 0)),
        'Memory': int(shortage.get('Memory', 0)),
        'Storage': int(shortage.get('Storage', 0)),
    }
    if remaining['CPU'] == 0 and remaining['Memory'] == 0 and remaining['Storage'] == 0:
        return True

    donor_rows = fetch_all(
        """
        SELECT id, extra_cpu, extra_memory, extra_storage
        FROM donor_resources
        WHERE LOWER(share_decision) = 'approve'
        ORDER BY id ASC
        """
    )

    for donor_id, extra_cpu, extra_memory, extra_storage in donor_rows:
        use_cpu = min(int(extra_cpu), remaining['CPU'])
        use_memory = min(int(extra_memory), remaining['Memory'])
        use_storage = min(int(extra_storage), remaining['Storage'])

        if use_cpu == 0 and use_memory == 0 and use_storage == 0:
            continue

        execute_query(
            """
            UPDATE donor_resources
            SET extra_cpu = extra_cpu - %s,
                extra_memory = extra_memory - %s,
                extra_storage = extra_storage - %s
            WHERE id = %s
            """,
            (use_cpu, use_memory, use_storage, int(donor_id))
        )

        remaining['CPU'] -= use_cpu
        remaining['Memory'] -= use_memory
        remaining['Storage'] -= use_storage

        if remaining['CPU'] == 0 and remaining['Memory'] == 0 and remaining['Storage'] == 0:
            return True

    return False

@require_user
def AddDemand(request):
    if request.method == 'POST':
        try:
            cpu = int(request.POST.get('cpu'))
            memory = int(request.POST.get('memory'))
            storage = int(request.POST.get('storage'))
            priority = float(request.POST.get('priority'))
        except (ValueError, TypeError):
            messages.error(request, 'Invalid input values')
            return redirect('UserScreen')

        # Check if request exceeds capacities
        shortage = {
            'CPU': max(0, cpu - CAPACITIES['CPU']),
            'Memory': max(0, memory - CAPACITIES['Memory']),
            'Storage': max(0, storage - CAPACITIES['Storage'])
        }
        has_shortage = any(shortage[key] > 0 for key in shortage)

        status = 'waiting_for_admin'
        note = f"Shortage: CPU={shortage['CPU']}, Memory={shortage['Memory']}, Storage={shortage['Storage']}" if has_shortage else 'Submitted for evaluation'

        # Create the request
        client_request = ClientRequest.objects.create(
            user=request.user,
            cpu=cpu,
            memory=memory,
            storage=storage,
            priority=priority,
            status=status,
            shortage_note=note,
        )

        # Create notification for admin
        admin_user = User.objects.filter(username='admin').first()
        if admin_user:
            UserNotification.objects.create(
                user=admin_user,
                request=client_request,
                message=f"New request #{client_request.id} submitted by {request.user.username}. Status: {status}."
            )

        # Create notification for user
        UserNotification.objects.create(
            user=request.user,
            request=client_request,
            message=f"Your request #{client_request.id} is waiting for admin evaluation."
        )

        messages.success(request, "Your request was submitted successfully.")
        return redirect('UserScreen')


def getResult(values):
    y = []
    for result in values:
        unfill = result['unfulfilled']
        total = unfill['CPU'] + unfill['Memory'] + unfill['Storage']
        y.append(total)
    return y

def get_unfulfilled_by_original_request(all_demands, values):
    totals = [0.0] * len(all_demands)
    for row in values:
        original_no = get_original_request_no(all_demands, row['demand'])
        if original_no > 0 and original_no <= len(all_demands):
            totals[original_no - 1] = round(total_unfulfilled(row['unfulfilled']), 4)
    return totals

def get_original_request_no(all_demands, allocated_demand):
    for i, d in enumerate(all_demands):
        if d is allocated_demand:
            return i + 1
        if d.get('id', None) is not None and allocated_demand.get('id', None) is not None and d.get('id') == allocated_demand.get('id'):
            return i + 1
    return -1

def total_unfulfilled(unfulfilled):
    return unfulfilled.get('CPU', 0) + unfulfilled.get('Memory', 0) + unfulfilled.get('Storage', 0)

def jains_fairness(values):
    vals = [max(0.0, float(v)) for v in values]
    if len(vals) == 0:
        return 0.0
    s = sum(vals)
    sq = sum(v * v for v in vals)
    if sq <= 0:
        return 0.0
    return (s * s) / (len(vals) * sq)

def evaluate_allocation_metrics(results, capacities):
    per_resource_alloc = {r: 0.0 for r in capacities.keys()}
    total_requested = 0.0
    total_allocated = 0.0
    total_unf = 0.0
    weighted_unf = 0.0
    per_request_satisfaction = []
    critical_unf = 0.0
    critical_met = 0
    critical_total = 0
    served_count = 0

    for row in results:
        need_sum = 0.0
        alloc_sum = 0.0
        priority = float(row['demand'].get('priority', 1.0))

        for r in capacities.keys():
            need = float(row['demand']['resource_needs'].get(r, 0.0))
            alloc = float(row['allocation'].get(r, 0.0))
            unf = float(row['unfulfilled'].get(r, 0.0))

            need_sum += need
            alloc_sum += alloc
            total_unf += unf
            weighted_unf += unf * priority
            per_resource_alloc[r] += alloc

        total_requested += need_sum
        total_allocated += alloc_sum
        satisfaction = (alloc_sum / need_sum) if need_sum > 0 else 1.0
        per_request_satisfaction.append(satisfaction)
        if alloc_sum > 0:
            served_count += 1

        if priority >= 0.8:
            critical_total += 1
            critical_unf += (need_sum - alloc_sum) * (1.0 + priority)
            if satisfaction >= 0.9:
                critical_met += 1

    total_capacity = float(sum(capacities.values()))
    total_waste = max(0.0, total_capacity - total_allocated)
    utilization_pct = (total_allocated / total_capacity * 100.0) if total_capacity > 0 else 0.0
    waste_pct = (total_waste / total_capacity * 100.0) if total_capacity > 0 else 0.0

    util_rates = []
    for r in capacities.keys():
        cap = float(capacities[r])
        util_rates.append((per_resource_alloc[r] / cap) if cap > 0 else 0.0)

    mean_util = sum(util_rates) / len(util_rates) if len(util_rates) > 0 else 0.0
    std_util = math.sqrt(sum((u - mean_util) ** 2 for u in util_rates) / len(util_rates)) if len(util_rates) > 0 else 0.0
    cv = (std_util / mean_util) if mean_util > 1e-9 else 1.0
    balance_index = max(0.0, 1.0 - cv)
    served_requests_pct = (served_count / len(results) * 100.0) if len(results) > 0 else 0.0
    min_satisfaction = min(per_request_satisfaction) if per_request_satisfaction else 0.0

    return {
        'total_unfulfilled': round(total_unf, 4),
        'weighted_unfulfilled': round(weighted_unf, 4),
        'total_requested': round(total_requested, 4),
        'total_allocated': round(total_allocated, 4),
        'total_waste': round(total_waste, 4),
        'utilization_pct': round(utilization_pct, 2),
        'waste_pct': round(waste_pct, 2),
        'critical_unfulfilled': round(critical_unf, 4),
        'critical_request_success_pct': round((critical_met / critical_total) * 100.0, 2) if critical_total > 0 else 0.0,
        'served_requests_pct': round(served_requests_pct, 2),
        'min_satisfaction': round(min_satisfaction, 4),
        'zero_allocation_requests': max(0, len(results) - served_count),
        'fairness_index': round(jains_fairness(per_request_satisfaction), 4),
        'balance_index': round(balance_index, 4),
    }


def format_allocation_triplet(values):
    return f"CPU {round(values.get('CPU', 0), 2)}, Memory {round(values.get('Memory', 0), 2)}, Storage {round(values.get('Storage', 0), 2)}"


def fmt_metric_change(base, other, higher_is_better):
    diff = round(other - base, 4)
    if abs(diff) < 1e-9:
        return "no change"

    if higher_is_better:
        if diff > 0:
            pct = round((diff / base) * 100, 2) if base > 0 else 0
            return f"improved by {round(diff, 4)} ({pct}%)"
        return f"dropped by {round(abs(diff), 4)}"

    if diff < 0:
        pct = round((abs(diff) / base) * 100, 2) if base > 0 else 0
        return f"decreased by {round(abs(diff), 4)} ({pct}%)"
    return f"increased by {round(diff, 4)}"


def determine_overall_best_algorithm(fixed_m, hybrid_m, genetic_m):
    scored = [
        ('Fixed', fixed_m),
        ('Hybrid', hybrid_m),
        ('Genetic', genetic_m),
    ]
    winner = min(
        scored,
        key=lambda item: (
            item[1]['zero_allocation_requests'],
            -item[1]['served_requests_pct'],
            -item[1]['min_satisfaction'],
            -item[1]['fairness_index'],
            item[1]['weighted_unfulfilled'],
            item[1]['total_unfulfilled'],
            item[1]['critical_unfulfilled'],
            -item[1]['critical_request_success_pct'],
            -item[1]['utilization_pct'],
            item[1]['waste_pct'],
            -item[1]['balance_index'],
        )
    )
    return winner[0]


def get_results_for_algorithm_name(algorithm_name):
    if algorithm_name == 'Fixed':
        return existing
    if algorithm_name == 'Hybrid':
        return propose
    return extension


def build_best_allocation_map(algorithm_name):
    allocation_map = {}
    for row in get_results_for_algorithm_name(algorithm_name):
        demand = row.get('demand', {})
        request_id = int(demand.get('id', 0) or 0)
        if request_id <= 0:
            continue
        allocation_map[request_id] = format_allocation_triplet(row.get('allocation', {}))
    return allocation_map


def apply_algorithm_results_to_requests(results, algorithm_name):
    for row in results:
        demand = row.get('demand', {})
        request_id = demand.get('id')
        if request_id is None:
            continue
        allocation = {
            'CPU': int(round(float(row['allocation'].get('CPU', 0.0)))),
            'Memory': int(round(float(row['allocation'].get('Memory', 0.0)))),
            'Storage': int(round(float(row['allocation'].get('Storage', 0.0)))),
        }
        note = f"Automatically applied from overall best algorithm: {algorithm_name}"
        update_request_status(request_id, 'allocated', note, allocation)


def auto_apply_overall_best_results():
    if len(existing) == 0 or len(propose) == 0 or len(extension) == 0:
        return ''

    fixed_m = evaluate_allocation_metrics(existing, capacities)
    hybrid_m = evaluate_allocation_metrics(propose, capacities)
    genetic_m = evaluate_allocation_metrics(extension, capacities)
    overall_best_algorithm = determine_overall_best_algorithm(fixed_m, hybrid_m, genetic_m)
    apply_algorithm_results_to_requests(get_results_for_algorithm_name(overall_best_algorithm), overall_best_algorithm)
    return overall_best_algorithm


def build_request_comparison_rows(all_demands, fixed_results, hybrid_results, genetic_results):
    request_rows = {
        row.id: row.user.username
        for row in ClientRequest.objects.select_related('user').filter(id__in=[d.get('id') for d in all_demands])
    }
    result_sets = {
        'Fixed': fixed_results or [],
        'Hybrid': hybrid_results or [],
        'Genetic': genetic_results or [],
    }
    indexed = {}

    for method_name, rows in result_sets.items():
        for row in rows:
            request_no = get_original_request_no(all_demands, row['demand'])
            if request_no <= 0:
                continue
            entry = indexed.setdefault(request_no, {})
            allocation = {
                'CPU': round(float(row['allocation'].get('CPU', 0.0)), 2),
                'Memory': round(float(row['allocation'].get('Memory', 0.0)), 2),
                'Storage': round(float(row['allocation'].get('Storage', 0.0)), 2),
            }
            unfulfilled_total = round(total_unfulfilled(row['unfulfilled']), 4)
            allocated_total = round(sum(allocation.values()), 4)
            demand_info = row['demand']
            entry[method_name] = {
                'allocation': allocation,
                'allocation_text': format_allocation_triplet(allocation),
                'unfulfilled_total': unfulfilled_total,
                'allocated_total': allocated_total,
            }
            entry['request_id'] = int(demand_info.get('id', request_no))
            entry['priority'] = round(float(demand_info.get('priority', 0.0)), 2)
            entry['demand_text'] = format_allocation_triplet(demand_info.get('resource_needs', {}))

    comparison_rows = []
    for request_no, entry in sorted(indexed.items()):
        if 'Fixed' not in entry or 'Hybrid' not in entry or 'Genetic' not in entry:
            continue
        comparison_rows.append({
            'request_no': request_no,
            'request_id': entry['request_id'],
            'username': request_rows.get(entry['request_id'], ''),
            'priority': entry['priority'],
            'demand_text': entry['demand_text'],
            'fixed': entry['Fixed'],
            'hybrid': entry['Hybrid'],
            'genetic': entry['Genetic'],
        })
    return comparison_rows


def build_user_comparison_rows(username, comparison_rows):
    user_request_ids = {row['id'] for row in load_user_requests(username)}
    return [row for row in comparison_rows if row['request_id'] in user_request_ids]

@require_admin
def Graph(request):
    if request.method == 'GET':
        global existing, propose, extension, demands
        demands = load_demands_for_algorithms()

        # if user didn't run algorithms yet, show message
        if len(existing) == 0 or len(propose) == 0 or len(extension) == 0:
            request.session['flash'] = ('error', 'Run Fixed, Hybrid and Multi-Agent algorithms first, then click Graph.')
            return redirect('AdminView')

        ex = get_unfulfilled_by_original_request(demands, existing)
        pr = get_unfulfilled_by_original_request(demands, propose)
        ext = get_unfulfilled_by_original_request(demands, extension)

        fixed_m = evaluate_allocation_metrics(existing, capacities)
        hybrid_m = evaluate_allocation_metrics(propose, capacities)
        genetic_m = evaluate_allocation_metrics(extension, capacities)

        n_points = len(ex)
        x = np.arange(1, n_points + 1, dtype=float)

        # Display-only x-offset so overlapping lines remain visible.
        x_fixed = x - 0.06
        x_hybrid = x
        x_genetic = x + 0.06

        if n_points <= 35:
            fig_w, fig_h = 7.2, 4.0
            marker_size = 6
            show_markers = True
            tick_step = 1
        elif n_points <= 90:
            fig_w, fig_h = 10.5, 4.2
            marker_size = 0
            show_markers = False
            tick_step = max(1, n_points // 16)
        else:
            fig_w, fig_h = 12.0, 4.4
            marker_size = 0
            show_markers = False
            tick_step = max(1, n_points // 20)

        fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor='none')
        ax.set_facecolor((1, 1, 1, 0.08))

        fixed_marker = 'o' if show_markers else None
        hybrid_marker = 's' if show_markers else None
        genetic_marker = '^' if show_markers else None

        ax.plot(x_fixed, ex, color='#61b5ff', marker=fixed_marker, markersize=marker_size, linewidth=2.2, linestyle='-', label='Fixed')
        ax.plot(x_hybrid, pr, color='#ffb266', marker=hybrid_marker, markersize=marker_size, linewidth=2.2, linestyle='--', label='Hybrid')
        ax.plot(x_genetic, ext, color='#77d992', marker=genetic_marker, markersize=marker_size, linewidth=2.2, linestyle='-.', label='Multiobjective')

        tick_positions = np.arange(1, n_points + 1, tick_step)
        if tick_positions[-1] != n_points:
            tick_positions = np.append(tick_positions, n_points)
        ax.set_xticks(tick_positions)
        ax.set_xticklabels([int(v) for v in tick_positions])

        ax.set_title("Comparison of 3 Allocations (Unfulfilled by Original Request)", fontsize=11, color='#eaf0ff', pad=10)
        ax.set_xlabel("Original Request No", fontsize=9, color='#eaf0ff')
        ax.set_ylabel("Total Unfulfilled", fontsize=9, color='#eaf0ff')
        ax.tick_params(axis='both', colors='#eaf0ff', labelsize=8)
        for spine in ax.spines.values():
            spine.set_color((0.92, 0.95, 1.0, 0.45))

        legend = ax.legend(fontsize=8, frameon=True)
        legend.get_frame().set_facecolor((0.05, 0.12, 0.22, 0.45))
        legend.get_frame().set_edgecolor((0.92, 0.95, 1.0, 0.35))
        for t in legend.get_texts():
            t.set_color('#eaf0ff')

        # For dense datasets, keep only horizontal grid to reduce visual clutter.
        if n_points > 40:
            ax.grid(axis='y', color=(1, 1, 1, 0.22), linestyle='-', linewidth=0.8)
        else:
            ax.grid(color=(1, 1, 1, 0.22), linestyle='-', linewidth=0.8)
        ax.margins(x=0.04, y=0.10)

        buf = io.BytesIO()
        fig.tight_layout(pad=1.1)
        fig.savefig(buf, format='png', dpi=150, transparent=True, bbox_inches='tight', pad_inches=0.20)
        img_b64 = base64.b64encode(buf.getvalue()).decode()
        plt.close(fig)

        proof_rows = [
            {'name': 'Fixed', **fixed_m},
            {'name': 'Hybrid', **hybrid_m},
            {'name': 'Multiobjective', **genetic_m},
        ]
        auto_apply_overall_best_results()
        comparison_rows = build_request_comparison_rows(demands, existing, propose, extension)
        overall_best_algorithm = determine_overall_best_algorithm(fixed_m, hybrid_m, genetic_m)

        comparison_fixed = (
            f"Unfulfilled {fmt_metric_change(fixed_m['total_unfulfilled'], genetic_m['total_unfulfilled'], False)}, "
            f"Priority-weighted Unfulfilled {fmt_metric_change(fixed_m['weighted_unfulfilled'], genetic_m['weighted_unfulfilled'], False)}, "
            f"Critical Unfulfilled {fmt_metric_change(fixed_m['critical_unfulfilled'], genetic_m['critical_unfulfilled'], False)}, "
            f"Critical Success {fmt_metric_change(fixed_m['critical_request_success_pct'], genetic_m['critical_request_success_pct'], True)}, "
            f"Users Served {fmt_metric_change(fixed_m['served_requests_pct'], genetic_m['served_requests_pct'], True)}, "
            f"Minimum Satisfaction {fmt_metric_change(fixed_m['min_satisfaction'], genetic_m['min_satisfaction'], True)}, "
            f"Utilization {fmt_metric_change(fixed_m['utilization_pct'], genetic_m['utilization_pct'], True)}, "
            f"Wastage {fmt_metric_change(fixed_m['waste_pct'], genetic_m['waste_pct'], False)}, "
            f"Fairness {fmt_metric_change(fixed_m['fairness_index'], genetic_m['fairness_index'], True)}, "
            f"Load Balance {fmt_metric_change(fixed_m['balance_index'], genetic_m['balance_index'], True)}"
        )

        comparison_hybrid = (
            f"Unfulfilled {fmt_metric_change(hybrid_m['total_unfulfilled'], genetic_m['total_unfulfilled'], False)}, "
            f"Priority-weighted Unfulfilled {fmt_metric_change(hybrid_m['weighted_unfulfilled'], genetic_m['weighted_unfulfilled'], False)}, "
            f"Critical Unfulfilled {fmt_metric_change(hybrid_m['critical_unfulfilled'], genetic_m['critical_unfulfilled'], False)}, "
            f"Critical Success {fmt_metric_change(hybrid_m['critical_request_success_pct'], genetic_m['critical_request_success_pct'], True)}, "
            f"Users Served {fmt_metric_change(hybrid_m['served_requests_pct'], genetic_m['served_requests_pct'], True)}, "
            f"Minimum Satisfaction {fmt_metric_change(hybrid_m['min_satisfaction'], genetic_m['min_satisfaction'], True)}, "
            f"Utilization {fmt_metric_change(hybrid_m['utilization_pct'], genetic_m['utilization_pct'], True)}, "
            f"Wastage {fmt_metric_change(hybrid_m['waste_pct'], genetic_m['waste_pct'], False)}, "
            f"Fairness {fmt_metric_change(hybrid_m['fairness_index'], genetic_m['fairness_index'], True)}, "
            f"Load Balance {fmt_metric_change(hybrid_m['balance_index'], genetic_m['balance_index'], True)}"
        )

        return render_admin_dashboard(request, {
            'template_name': 'AdminView.html',
            'data': "",
            'img': img_b64,
            'proof_rows': proof_rows,
            'comparison_rows': comparison_rows,
            'overall_best_algorithm': overall_best_algorithm,
            'comparison_fixed': comparison_fixed,
            'comparison_hybrid': comparison_hybrid,
        })


@require_admin
def RunFixed(request):
    if request.method == 'GET':
        global resources, capacities, demands, existing
        demands = load_demands_for_algorithms()

        # ✅ ADD THIS CHECK HERE
        if len(demands) == 0:
            request.session['flash'] = ('error', 'Please submit client requests first.')
            return redirect('AdminView')

        fixed_allocator = HybridResourceAllocator(resources, capacities)
        existing, remaining_resources = fixed_allocator.allocate_hybrid(demands,"fixed")
        output = "<font size=3 color=blue>Admin Review: Fixed Allocation</font><br/><br/>"
        output += "<font size=2 color=black>Order shown below reflects the fixed-strategy allocation results for submitted demands.</font><br/><br/>"
        index = 1
        for result in existing:
            original_no = get_original_request_no(demands, result['demand'])
            output += "Admin Queue No : "+str(index)+"<br/>"
            output += "Client Request No : "+str(original_no)+"<br/>"
            output += "Priority : "+str(result['demand']['priority'])+"<br/>"
            output += "Allocated Resources: "+str(result['allocation'])+"<br/>"
            output += "Unfulfilled Resources: "+str(result['unfulfilled'])+"<br/><br/>"
            index += 1
        auto_apply_overall_best_results()
        return render_admin_dashboard(request, {
            'template_name': 'AdminView.html',
            'data': output,
        })
            
@require_admin
def RunHybrid(request):
    if request.method == 'GET':
        global resources, capacities, demands, propose
        demands = load_demands_for_algorithms()

        # ✅ ADD HERE
        if len(demands) == 0:
            request.session['flash'] = ('error', 'Please submit client requests first.')
            return redirect('AdminView')

        hybrid_allocator = HybridResourceAllocator(resources, capacities)
        propose, remaining_resources = hybrid_allocator.allocate_hybrid(demands,"hybrid")
        output = "<font size=3 color=blue>Admin Review: Hybrid Allocation</font><br/><br/>"
        output += "<font size=2 color=black>Order shown below reflects the hybrid-strategy allocation results for submitted demands.</font><br/><br/>"
        index = 1
        for result in propose:
            original_no = get_original_request_no(demands, result['demand'])
            output += "Admin Queue No : "+str(index)+"<br/>"
            output += "Client Request No : "+str(original_no)+"<br/>"
            output += "Priority : "+str(result['demand']['priority'])+"<br/>"
            output += "Allocated Resources: "+str(result['allocation'])+"<br/>"
            output += "Unfulfilled Resources: "+str(result['unfulfilled'])+"<br/><br/>"
            index += 1
        auto_apply_overall_best_results()
        return render_admin_dashboard(request, {
            'template_name': 'AdminView.html',
            'data': output,
        })

@require_admin
def RunGenetic(request):
    if request.method == 'GET':
        global resources, capacities, demands, extension, propose
        demands = load_demands_for_algorithms()

        # ✅ ADD HERE
        if len(demands) == 0:
            request.session['flash'] = ('error', 'Please submit client requests first.')
            return redirect('AdminView')

        ext_allocator = HybridResourceAllocator(resources, capacities)
        extension, remaining_resources = ext_allocator.allocate_hybrid(demands,"multiobjective")
        output = "<font size=3 color=blue>Admin Review: Genetic Multiobjective Allocation</font><br/><br/>"
        output += "<font size=2 color=black>Order shown below reflects the optimized genetic allocation results for submitted demands.</font><br/><br/>"
        output += "<font size=2 color=black>Values below show the final resources assigned and remaining unfulfilled demand.</font><br/><br/>"
        index = 1
        for result in extension:
            original_no = get_original_request_no(demands, result['demand'])

            output += "Admin Queue No : "+str(index)+"<br/>"
            output += "Client Request No : "+str(original_no)+"<br/>"
            output += "Priority : "+str(result['demand']['priority'])+"<br/>"
            output += "Allocated Resources: "+str(result['allocation'])+"<br/>"
            output += "Unfulfilled Resources: "+str(result['unfulfilled'])+"<br/><br/>"
            index += 1            
        auto_apply_overall_best_results()
        return render_admin_dashboard(request, {
            'template_name': 'AdminView.html',
            'data': output,
        })

def RegisterAction(request):
    if request.method == 'POST':
        username = request.POST.get('t1', '').strip()
        password = request.POST.get('t2', '').strip()
        contact = request.POST.get('t3', '').strip()
        email = request.POST.get('t4', '').strip()
        address = request.POST.get('t5', '').strip()

        if not all([username, password, contact, email, address]):
            messages.error(request, 'All fields are required')
            return render(request, 'Register.html')

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists')
            return render(request, 'Register.html')

        try:
            user = User.objects.create_user(
                username=username,
                password=password,
                email=email
            )
            user.contact = contact
            user.address = address
            user.save()
            messages.success(request, 'Registration successful. Please login.')
            return redirect('UserLogin')
        except Exception as e:
            messages.error(request, f'Registration failed: {str(e)}')
            return render(request, 'Register.html')    

def UserLoginAction(request):
    if request.method == 'POST':
        username = request.POST.get('t1', '')
        password = request.POST.get('t2', '')
        user = authenticate(request, username=username, password=password)
        if user and user.is_active:
            login(request, user)
            # messages.success(request, f'Welcome {username}')  # Removed welcome message
            return redirect('UserScreen')
        else:
            messages.error(request, 'Invalid username or password')
            return render(request, 'UserLogin.html')

@require_user
def UserScreen(request):
    user_requests = ClientRequest.objects.filter(user=request.user).order_by('-created_at')
    flash = request.session.pop('flash', None)
    unread_admin_chat_notifications = count_unread_admin_chat_notifications(request.user.username)

    summary = {
        'total_requests': user_requests.count(),
        'allocated_requests': user_requests.filter(status='allocated').count(),
        'pending_requests': user_requests.filter(
            status__in=['pending', 'shortage', 'waiting_for_admin', 'waiting_for_user_approval']
        ).count(),
        'rejected_requests': user_requests.filter(status='rejected').count(),
        'latest_request': user_requests.first(),
        'total_allocated_cpu': user_requests.aggregate(total=Sum('allocated_cpu'))['total'] or 0,
        'total_allocated_memory': user_requests.aggregate(total=Sum('allocated_memory'))['total'] or 0,
        'total_allocated_storage': user_requests.aggregate(total=Sum('allocated_storage'))['total'] or 0,
    }

    context = {
        'flash': flash,
        'current_user': request.user.username,
        'requests': user_requests,
        'user_requests': user_requests,
        'summary': summary,
        'profile': request.user,
        'chat_messages': load_chat_for_user(request.user.username),
        'open_ai_chat': bool(request.session.pop('open_ai_chat', False)),
        'unread_admin_chat_notifications': unread_admin_chat_notifications,
        'capacities': CAPACITIES,
        'resources': RESOURCES,
    }
    return render(request, 'UserScreen.html', context)


def UpdateProfile(request):
    if request.method == 'POST':
        contact = (request.POST.get('contact') or '').strip()
        email = (request.POST.get('email') or '').strip()
        address = (request.POST.get('address') or '').strip()

        # Update user fields
        request.user.email = email
        request.user.contact = contact
        request.user.address = address

        # Handle profile image upload
        uploaded_image = request.FILES.get('profile_image')
        if uploaded_image:
            from django.core.files.storage import FileSystemStorage
            import os
            from django.conf import settings
            storage = FileSystemStorage(location=os.path.join(settings.BASE_DIR, 'AllocationApp', 'static', 'profile_images'))
            saved_name = storage.save(uploaded_image.name, uploaded_image)
            request.user.profile_image = f"profile_images/{saved_name}"

        request.user.save()
        messages.success(request, 'Profile updated successfully.')
        return redirect('UserScreen')

    return redirect('UserScreen')

def UserLogin(request):
    if request.method == 'GET':
       return render(request, 'UserLogin.html', {})

def index(request):
    if request.method == 'GET':
       request.session.flush()
       return render(request, 'index.html', {})

def Register(request):
    if request.method == 'GET':
       return render(request, 'Register.html', {})

def AdminLogin(request):
    if request.method == 'GET':
       return render(request, 'AdminLogin.html', {})


def AdminLoginAction(request):
    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user and is_admin(user):
            request.session['admin_user_id'] = user.id
            return redirect('AdminView')
        return render(request, 'AdminLogin.html', {'error': 'Invalid admin credentials'})


@require_admin
def AdminDashboard(request):
    return render_admin_dashboard(request, {'template_name': 'AdminDashboard.html'})


@require_admin
def AdminView(request):
    return render_admin_dashboard(request, {'template_name': 'AdminView.html'})


@require_admin
def SendForUserApproval(request, request_id):
    ensure_workflow_tables()
    request.session['flash'] = (
        'info',
        'Past-user approval is disabled in this evaluation build. Compare fixed, hybrid, and genetic allocation from the dashboard instead.'
    )
    return redirect('AdminView')


@require_admin
def AllocateApprovedRequest(request, request_id):
    ensure_workflow_tables()

    row = get_request_by_id(request_id)
    if not row:
        request.session['flash'] = ('error', 'Request not found.')
        return redirect('AdminView')

    base_available = calculate_base_available_capacity()
    required = {
        'CPU': row['cpu'],
        'Memory': row['memory'],
        'Storage': row['storage'],
    }

    shortage = {
        'CPU': max(0, required['CPU'] - base_available['CPU']),
        'Memory': max(0, required['Memory'] - base_available['Memory']),
        'Storage': max(0, required['Storage'] - base_available['Storage']),
    }

    enough = (
        required['CPU'] <= base_available['CPU'] and
        required['Memory'] <= base_available['Memory'] and
        required['Storage'] <= base_available['Storage']
    )

    if not enough:
        update_request_status(request_id, 'waiting_for_admin', format_shortage_note(shortage))
        request.session['flash'] = ('error', f"Base capacity is still not enough for request #{request_id}. Use the comparison dashboard for evaluation results or reject the request.")
        return redirect('AdminView')

    allocated = {'CPU': row['cpu'], 'Memory': row['memory'], 'Storage': row['storage']}
    update_request_status(request_id, 'allocated', 'Allocated after admin evaluation using base capacity', allocated)
    update_request_feedback(request_id, 'pending', '', {'CPU': 0, 'Memory': 0, 'Storage': 0})
    create_notification(row['username'], f"Your request #{request_id} was allocated by admin using available base capacity.", request_id)
    request.session['flash'] = ('success', f"Request #{request_id} allocated using available base capacity.")
    return redirect('AdminView')


@require_admin
def RejectRequest(request, request_id):
    ensure_workflow_tables()
    row = get_request_by_id(request_id)
    if not row:
        request.session['flash'] = ('error', 'Request not found.')
        return redirect('AdminView')
    update_request_status(request_id, 'rejected', 'Rejected by admin after review')
    update_request_feedback(request_id, 'pending')
    create_notification(row['username'], f"Admin rejected request #{request_id}.", request_id)
    request.session['flash'] = ('success', f"Request #{request_id} rejected.")
    return redirect('AdminView')


@require_user
def SendUserChat(request):
    ensure_workflow_tables()
    if request.method == 'POST':
        message = (request.POST.get('message') or '').strip()
        current_user = get_logged_user(request)
        if message:
            if message.lower().startswith('@admin'):
                create_chat_message(current_user, 'admin', message)
                create_notification('admin', f"{current_user} sent a new chat message.", None)
                request.session['flash'] = ('success', 'Message sent to admin.')
            else:
                create_chat_message(current_user, 'admin', message)
                create_chat_message('assistant', current_user, generate_support_reply(message, current_user))
                create_notification('admin', f"{current_user} sent a new chat message.", None)
                request.session['flash'] = ('success', 'Message sent. AI support also replied.')
            request.session['open_ai_chat'] = True
    return redirect('UserScreen')


@require_user
def UserChatState(request):
    return JsonResponse({
        'messages': load_chat_for_user(request.user.username),
        'unread_admin_chat_notifications': count_unread_admin_chat_notifications(request.user.username),
    })


@require_user
def MarkUserChatRead(request):
    if request.method == 'POST':
        mark_admin_chat_notifications_read(request.user.username)
    return JsonResponse({'ok': True})


@require_admin
def SendAdminChat(request):
    ensure_workflow_tables()
    if request.method == 'POST':
        receiver = (request.POST.get('receiver') or '').strip()
        message = (request.POST.get('message') or '').strip()
        if receiver and message:
            create_chat_message(getattr(request, 'admin_user', None) or get_admin_request_user(request), receiver, message)
            create_notification(receiver, 'Admin sent you a new chat message.')
            return redirect(f"/AdminView.html?chat_with={receiver}")
    return redirect('AdminView')


@require_user
def SubmitResourceOffer(request):
    ensure_workflow_tables()
    if request.method == 'POST':
        current_user = request.user.username
        request_id = int(request.POST.get('request_id'))
        decision = (request.POST.get('decision') or 'approve').strip().lower()
        cpu = int(request.POST.get('offer_cpu') or 0)
        memory = int(request.POST.get('offer_memory') or 0)
        storage = int(request.POST.get('offer_storage') or 0)
        note = (request.POST.get('note') or '').strip()

        if decision not in ['approve', 'reject']:
            request.session['flash'] = ('error', 'Invalid offer decision.')
            return redirect('UserScreen')

        if decision == 'reject':
            cpu = 0
            memory = 0
            storage = 0

        save_user_offer(request_id, current_user, cpu, memory, storage, decision, note)
        requester_row = get_request_by_id(request_id)
        if requester_row:
            create_notification(
                requester_row['username'],
                f"{current_user} responded to admin request #{request_id} with {decision}: CPU {cpu}, Memory {memory}, Storage {storage}.",
                request_id
            )
        create_notification(
            'admin',
            f"{current_user} responded to request #{request_id} with {decision}: CPU {cpu}, Memory {memory}, Storage {storage}.",
            request_id
        )
        request.session['flash'] = ('success', f'Your response for request #{request_id} was submitted.')
    return redirect('UserScreen')


@require_user
def SubmitAllocationFeedback(request, request_id):
    ensure_workflow_tables()
    if request.method != 'POST':
        return redirect('UserScreen')

    current_user = request.user.username
    row = get_request_by_id(request_id)
    if not row or row['username'] != current_user:
        request.session['flash'] = ('error', 'Request not found.')
        return redirect('UserScreen')

    feedback = (request.POST.get('feedback_status') or '').strip().lower()
    note = (request.POST.get('feedback_note') or '').strip()
    extra = {
        'CPU': int(request.POST.get('extra_cpu') or 0),
        'Memory': int(request.POST.get('extra_memory') or 0),
        'Storage': int(request.POST.get('extra_storage') or 0),
    }

    if row['status'] != 'allocated':
        request.session['flash'] = ('error', 'Feedback can only be submitted after allocation.')
        return redirect('UserScreen')

    if feedback == 'satisfied':
        update_request_feedback(request_id, 'satisfied', note, {'CPU': 0, 'Memory': 0, 'Storage': 0})
        create_notification('admin', f"{current_user} marked request #{request_id} as satisfactory.", request_id)
        request.session['flash'] = ('success', f'Request #{request_id} marked as satisfactory.')
        return redirect('UserScreen')

    if feedback == 'needs_more':
        request.session['flash'] = (
            'info',
            'Extra resource requests are disabled in this evaluation build. Please use the request note only for satisfaction feedback.'
        )
        return redirect('UserScreen')

    request.session['flash'] = ('error', 'Invalid feedback option.')
    return redirect('UserScreen')


@require_user
def DeleteUserRequest(request, request_id):
    ensure_workflow_tables()
    current_user = request.user.username
    row = ClientRequest.objects.filter(id=request_id, user__username=current_user).first()
    if not row:
        request.session['flash'] = ('error', 'Request not found.')
        return redirect('UserScreen')

    UserNotification.objects.filter(request=row).delete()
    row.delete()
    sync_client_requests_csv()
    request.session['flash'] = ('success', f'Request #{request_id} deleted from history.')
    return redirect('UserScreen')

@require_admin
def ResetAllocation(request):
    if request.method == 'GET':
        ensure_workflow_tables()
        UserNotification.objects.filter(request__isnull=False).delete()
        ClientRequest.objects.all().delete()
        DonorResource.objects.all().delete()
        global demands, existing, propose, extension
        demands = []
        existing = []
        propose = []
        extension = []
        sync_client_requests_csv()
        request.session['flash'] = ('success', 'Experiment reset successfully. You can now submit or import new request data.')
        return redirect('AdminView')
