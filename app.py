from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory, send_file
import pymysql
import bcrypt
import os
import uuid
from datetime import datetime, timedelta
import pytz
import secrets
from functools import wraps
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import uuid
from werkzeug.utils import secure_filename

from PIL import Image

app = Flask(__name__)
app.secret_key = 'your-fixed-secret-key-for-development-only'

# Performance optimizations
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000  # 1 year cache for static files
app.config['JSON_AS_ASCII'] = False  # Support for non-ASCII characters in JSON
app.config['COMPRESS_MIMETYPES'] = ['text/html', 'text/css', 'text/xml', 'application/json', 'application/javascript']

# Add response headers for optimization
@app.after_request
def after_request(response):
    # Enable gzip compression hint
    response.headers.add('Vary', 'Accept-Encoding')
    
    # Security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # Cache control for static assets
    if request.endpoint and request.endpoint.startswith('static'):
        response.headers['Cache-Control'] = 'public, max-age=31536000'  # 1 year
    elif request.endpoint and 'image' in request.endpoint:
        response.headers['Cache-Control'] = 'public, max-age=2592000'  # 30 days
    else:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    
    return response

# 时区配置
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

def convert_to_beijing_time(datetime_obj):
    """Convert datetime to Beijing time - simple fix: add 4 hours"""
    if datetime_obj is None:
        return None
    
    from datetime import timedelta
    corrected_time = datetime_obj 
    return BEIJING_TZ.localize(corrected_time)

def get_beijing_now():
    """Get current Beijing time for database operations"""
    from datetime import datetime, timezone
    utc_now = datetime.now(timezone.utc)
    beijing_time = utc_now.astimezone(BEIJING_TZ)
    return beijing_time.replace(tzinfo=None)  # Return naive datetime for MySQL

@app.template_filter('image_url')
def image_url_filter(image_path):
    """Convert database image path to proper URL for display
    
    Args:
        image_path (str): Relative path from database
        
    Returns:
        str: Proper URL for web display
    """
    if not image_path:
        return ''
    
    # If it's already a full URL, return as-is
    if image_path.startswith('http'):
        return image_path
    
    # Remove leading slash if present
    if image_path.startswith('/'):
        image_path = image_path[1:]
    
    # Check if path starts with 'image/' (server format)
    if image_path.startswith('image/'):
        # Server environment - serve from /image/ directory
        return f"/{image_path}"
    elif image_path.startswith('uploads/'):
        # Convert uploads/ to cloud format /image/
        # uploads/forum_images/... -> /image/forum_images/...
        cloud_path = image_path.replace('uploads/', 'image/', 1)
        return f"/{cloud_path}"
    elif image_path.startswith('resources/'):
        # Resource images - always use cloud format
        return f"/image/{image_path}"
    else:
        # Fallback - assume it's in image directory
        return f"/image/{image_path}"

# 添加Jinja2模板过滤器
@app.template_filter('beijing_time')
def beijing_time_filter(datetime_obj):
    """Convert datetime to Beijing time for template display"""
    if datetime_obj is None:
        return ''
    beijing_time = convert_to_beijing_time(datetime_obj)
    return beijing_time.strftime('%B %d, %Y at %I:%M %p')

@app.template_filter('short_time')
def short_time_filter(datetime_obj):
    """Convert datetime to short Beijing time for compact display"""
    if datetime_obj is None:
        return ''
    beijing_time = convert_to_beijing_time(datetime_obj)
    return beijing_time.strftime('%m/%d %H:%M')

@app.template_filter('date_only')
def date_only_filter(datetime_obj):
    """Convert datetime to date only in Beijing time"""
    if datetime_obj is None:
        return ''
    beijing_time = convert_to_beijing_time(datetime_obj)
    return beijing_time.strftime('%Y-%m-%d')

@app.template_filter('datetime_full')
def datetime_full_filter(datetime_obj):
    """Convert datetime to full datetime in Beijing time"""
    if datetime_obj is None:
        return ''
    beijing_time = convert_to_beijing_time(datetime_obj)
    return beijing_time.strftime('%Y-%m-%d %H:%M:%S')

@app.template_filter('month_day')
def month_day_filter(datetime_obj):
    """Convert datetime to month/day format in Beijing time"""
    if datetime_obj is None:
        return ''
    beijing_time = convert_to_beijing_time(datetime_obj)
    return beijing_time.strftime('%m/%d')

# Add built-in functions to Jinja2 environment
app.jinja_env.globals.update(min=min, max=max)

# 将current_user添加到Jinja2全局变量，让所有模板都可以访问
@app.context_processor
def inject_current_user():
    return dict(current_user=current_user)

# Email configuration for password reset
EMAIL_CONFIG = {
    'smtp_server': os.getenv('SMTP_SERVER', 'smtp.gmail.com'),
    'smtp_port': int(os.getenv('SMTP_PORT', 587)),
    'email': os.getenv('EMAIL_ADDRESS', 'your-email@gmail.com'),
    'password': os.getenv('EMAIL_PASSWORD', 'your-app-password')
}

# Check if email is properly configured
EMAIL_CONFIGURED = (
    EMAIL_CONFIG['email'] != 'your-email@gmail.com' and 
    EMAIL_CONFIG['password'] != 'your-app-password'
)

# Public routes that don't require authentication
PUBLIC_ROUTES = [
    'login', 'register', 'reset_password', 'reset_password_confirm', 'static', 'admin_login', 'index',
    'forum', 'forum_post_detail', 'view_resource', 'subjects_overview', 'subjects_category',
    'competition_resources', 'university_resources', 'subject_resources', 'subject_level_resources', 
    'subject_level_category_resources', 'category_resources', 'education_resources', 
    'all_university_resources', 'other_resources'
]

# 创建匿名用户对象 - 替代Flask-Login
class AnonymousUser:
    def __init__(self):
        self.id = None
        self.username = 'Anonymous'
        self.email = None
        self.user_role = 'student'
        self.registration_status = 'approved'
        self.is_authenticated = False
        self.is_active = True
        self.is_anonymous = True

# 全局匿名用户对象
current_user = AnonymousUser()

# 用户登录函数 - 简化版本
def login_user(user):
    """登录用户 - 设置session"""
    session['user_id'] = user.id
    session['username'] = user.username
    session['user_role'] = user.user_role
    global current_user
    current_user = user

# 用户登出函数 - 简化版本  
def logout_user():
    """登出用户 - 清除session"""
    session.clear()
    global current_user
    current_user = AnonymousUser()

# 在每个请求前检查用户session状态
@app.before_request
def load_logged_in_user():
    """从session加载用户信息"""
    global current_user
    user_id = session.get('user_id')
    
    if user_id is None:
        current_user = AnonymousUser()
    else:
        # 从数据库加载用户信息
        try:
            connection = get_db_connection()
            if connection is None:
                current_user = AnonymousUser()
                return
                
            cursor = connection.cursor()
            cursor.execute("""
                SELECT id, username, email, user_role, registration_status
                FROM users WHERE id = %s
            """, (user_id,))
            
            user_data = cursor.fetchone()
            if user_data:
                current_user = User(
                    id=user_data[0],
                    username=user_data[1],
                    email=user_data[2],
                    user_role=user_data[3],
                    registration_status=user_data[4]
                )
            else:
                current_user = AnonymousUser()
                session.clear()
                
            cursor.close()
            connection.close()
                
        except Exception as e:
            print(f"Error loading user from session: {e}")
            current_user = AnonymousUser()

def track_page_view(page_type, page_id=None, user_id=None):
    """Record page view in database"""
    try:
        # Simple database connection for tracking
        connection = pymysql.connect(
            **DB_CONFIG,
            autocommit=True,
            charset='utf8mb4'
        )
        cursor = connection.cursor()
        cursor.execute("SET time_zone = '+08:00'")
        cursor.execute("SET SESSION time_zone = '+08:00'")
        
        # Get user information
        ip_address = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
        user_agent = request.environ.get('HTTP_USER_AGENT', '')[:500] if request.environ.get('HTTP_USER_AGENT') else ''
        referrer = request.environ.get('HTTP_REFERER', '')[:500] if request.environ.get('HTTP_REFERER') else ''
        session_id = session.get('session_id', 'anonymous')[:100]
        
        # Insert page view record
        cursor.execute("""
            INSERT INTO page_views (page_type, page_id, user_id, ip_address, user_agent, referrer, session_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (page_type, page_id, user_id, ip_address, user_agent, referrer, session_id))
        
        cursor.close()
        connection.close()
        
    except Exception as e:
        print(f"Error tracking page view: {e}")
        # Don't let tracking errors break the main functionality
        pass

# Database configuration
DB_CONFIG = {
    'host': 'sha1.clusters.zeabur.com',
    'port': 31890,
    'user': 'root',
    'password': 'NgiW5632UC0vmTD7ZlEuPAye9ao41JM8',
    'database': 'zeabur'
}

class User:
    def __init__(self, id, username, email, user_role='student', registration_status='approved'):
        self.id = id
        self.username = username
        self.email = email
        self.user_role = user_role
        self.registration_status = registration_status
        self.is_authenticated = True  # 真实用户总是已认证的
        self.is_active = True

def get_db_connection(max_retries=3):
    """Get database connection with optimized retry mechanism"""
    for attempt in range(max_retries):
        try:
            connection = pymysql.connect(
                **DB_CONFIG,
                autocommit=False,
                charset='utf8mb4',
                connect_timeout=3,  # Reduced connection timeout
                read_timeout=8,     # Reduced read timeout
                write_timeout=8,    # Reduced write timeout
                init_command="SET SESSION sql_mode='STRICT_TRANS_TABLES', time_zone='+08:00'",
                max_allowed_packet=16777216  # 16MB
            )
            # Quick connection test
            connection.ping(reconnect=False)
            return connection
            
        except Exception as e:
            print(f"Database connection attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                print("All database connection attempts failed. Using fallback mode.")
                return None
            import time
            # Faster retry: 0.1, 0.2, 0.4 seconds
            wait_time = 0.1 * (2 ** attempt)
            time.sleep(wait_time)

# Admin authentication
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin123'

def admin_required(f):
    """Decorator to require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def approved_user_required(f):
    """Decorator to require approved user status"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
            
        if current_user.registration_status != 'approved':
            flash('Your account is pending approval. Please wait for admin approval.', 'warning')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def verify_admin_credentials(username, password):
    """Verify admin credentials"""
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

# Image upload configuration and functions
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads', 'forum_images')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
COVER_IMAGE_SIZE = (800, 600)
ADDITIONAL_IMAGE_SIZE = (600, 450)

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def optimize_image(image_path, max_size, quality=85):
    """Optimize image size and quality"""
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if necessary
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            
            # Resize if larger than max_size
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # Save with optimization
            img.save(image_path, optimize=True, quality=quality)
    except Exception as e:
        print(f"Error optimizing image {image_path}: {e}")

def delete_image_file(image_path):
    """Delete image file from storage with enhanced debugging for Zeabur environment
    
    Args:
        image_path (str): Relative path stored in database (e.g., 'forum_images/2025/08/filename.jpg')
    
    Returns:
        bool: True if deleted successfully, False otherwise
    """
    if not image_path or image_path.startswith('http'):
        # Skip external URLs or empty paths
        print(f"🔄 跳过外部URL或空路径: {image_path}")
        return True
    
    # List of possible paths to try (for different deployment environments)
    possible_paths = [
        f"/image/{image_path}",  # Primary path (Zeabur)
        f"./image/{image_path}", # Relative path
        f"/app/image/{image_path}",  # Docker app directory
        f"/code/image/{image_path}", # Another common Docker path
    ]
    
    print(f"🎯 开始删除图片: {image_path}")
    print(f"🌍 当前工作目录: {os.getcwd()}")
    print(f"👤 当前用户: {os.getenv('USER', 'unknown')}")
    
    # Try each possible path
    for attempt, full_path in enumerate(possible_paths, 1):
        
        try:
            # Check if directory exists
            directory = os.path.dirname(full_path)
            print(f"📁 目录: {directory}")
            print(f"📁 目录是否存在: {os.path.exists(directory)}")
            
            if os.path.exists(directory):
                # Check directory permissions
                try:
                    dir_stat = os.stat(directory)
                    print(f"🔐 目录权限: {oct(dir_stat.st_mode)[-3:]}")
                    print(f"🔐 目录可写: {os.access(directory, os.W_OK)}")
                    print(f"🔐 目录所有者UID: {dir_stat.st_uid}")
                except:
                    print("🔐 无法获取目录权限信息")
            
            # Check if file exists
            file_exists = os.path.exists(full_path)
            print(f"📄 文件是否存在: {file_exists}")
            
            if file_exists:
                try:
                    # Check file permissions
                    file_stat = os.stat(full_path)
                    print(f"🔐 文件权限: {oct(file_stat.st_mode)[-3:]}")
                    print(f"🔐 文件可删除: {os.access(full_path, os.W_OK)}")
                    print(f"🔐 文件所有者UID: {file_stat.st_uid}")
                    print(f"📊 文件大小: {file_stat.st_size} bytes")
                except Exception as stat_e:
                    print(f"🔐 无法获取文件权限信息: {stat_e}")
                
                # Try to delete
                try:
                    os.remove(full_path)
                    print(f"✅ 成功删除图片文件: {full_path}")
                    
                    # Verify deletion
                    if not os.path.exists(full_path):
                        print(f"✅ 确认文件已删除: {full_path}")
                        return True
                    else:
                        print(f"❌ 删除命令执行但文件仍然存在: {full_path}")
                        continue
                        
                except PermissionError as pe:
                    print(f"❌ 权限错误 - 无法删除文件: {pe}")
                    # Try with different method
                    try:
                        import subprocess
                        result = subprocess.run(['rm', '-f', full_path], capture_output=True, text=True)
                        if result.returncode == 0:
                            print(f"✅ 使用系统rm命令成功删除: {full_path}")
                            return True
                        else:
                            print(f"❌ 系统rm命令也失败: {result.stderr}")
                    except Exception as subprocess_e:
                        print(f"❌ 系统rm命令执行失败: {subprocess_e}")
                    continue
                    
                except OSError as oe:
                    print(f"❌ 系统错误 - 无法删除文件: {oe}")
                    continue
                    
                except Exception as e:
                    print(f"❌ 未知错误 - 删除图片文件失败: {e}")
                    continue
            else:
                # File doesn't exist at this path, try next
                continue
                
        except Exception as path_e:
            print(f"❌ 路径处理错误: {path_e}")
            continue
    
    # If we get here, none of the paths worked
    print(f"⚠️ 所有路径都尝试失败，图片可能已被删除或不存在: {image_path}")
    
    # Check if this is the first path (expected path) - if file doesn't exist there, consider it deleted
    expected_path = f"/image/{image_path}"
    if not os.path.exists(expected_path):
        print(f"✅ 期望路径文件不存在，视为已删除: {expected_path}")
        return True
    
    print(f"❌ 删除失败: {image_path}")
    return False

def cleanup_post_images(cover_image, additional_images):
    """Clean up all images associated with a post or resource
    
    Args:
        cover_image (str): Cover image path from database
        additional_images (str): Comma-separated additional image paths from database
    
    Returns:
        dict: Summary of cleanup results
    """
    results = {
        'cover_deleted': False,
        'additional_deleted': 0,
        'additional_failed': 0,
        'errors': []
    }
    
    # Delete cover image
    if cover_image:
        if delete_image_file(cover_image):
            results['cover_deleted'] = True
        else:
            results['errors'].append(f"Failed to delete cover image: {cover_image}")
    
    # Delete additional images
    if additional_images:
        image_list = [img.strip() for img in additional_images.split(',') if img.strip()]
        for image_path in image_list:
            if delete_image_file(image_path):
                results['additional_deleted'] += 1
            else:
                results['additional_failed'] += 1
                results['errors'].append(f"Failed to delete additional image: {image_path}")
    
    return results

def save_forum_image(file, image_type='cover', post_id=None):
    """Save forum image to local storage"""
    if not file or file.filename == '':
        return {'success': False, 'error': 'No file provided'}
    
    if not allowed_file(file.filename):
        return {'success': False, 'error': 'File type not allowed'}
    
    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)  # Reset file pointer
    
    if file_size > MAX_FILE_SIZE:
        return {'success': False, 'error': 'File size too large (max 5MB)'}
    
    try:
        # Generate unique filename
        file_ext = file.filename.rsplit('.', 1)[1].lower()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        
        if post_id:
            filename = f"post_{post_id}_{image_type}_{timestamp}_{unique_id}.{file_ext}"
        else:
            filename = f"{image_type}_{timestamp}_{unique_id}.{file_ext}"
        
        # Create subdirectory by date
        date_folder = datetime.now().strftime('%Y/%m')
        
        # Check if we're on server (detect by /image directory existence)
        if os.path.exists('/image'):
            # Server environment - use /image/uploads/
            base_upload_dir = '/image/uploads/forum_images'
            save_folder = os.path.join(base_upload_dir, date_folder)
            os.makedirs(save_folder, exist_ok=True)
            
            # Full file path
            file_path = os.path.join(save_folder, filename)
            
            # Save file
            file.save(file_path)
            
            # Return relative path for database storage (without leading /)
            relative_path = f"image/uploads/forum_images/{date_folder}/{filename}"
        else:
            # Local environment - use static/uploads/
            save_folder = os.path.join(UPLOAD_FOLDER, date_folder)
            os.makedirs(save_folder, exist_ok=True)
            
            # Full file path
            file_path = os.path.join(save_folder, filename)
            
            # Save file
            file.save(file_path)
            
            # Return relative path for web access (relative to static/)
            # Convert absolute path to relative path from static folder
            static_dir = os.path.join(os.getcwd(), 'static')
            relative_path = os.path.relpath(file_path, static_dir).replace('\\', '/')
        
        # Skip image optimization for better performance
        # Images will be displayed as-is to avoid 30-second upload delays
        
        return {
            'success': True,
            'filename': filename,
            'path': relative_path,
            'file_size': file_size
        }
    
    except Exception as e:
        print(f"Error saving forum image: {e}")
        return {'success': False, 'error': f'Error saving image: {str(e)}'}

def save_forum_attachment(file, post_id=None):
    """Save forum attachment to local storage"""
    if not file or file.filename == '':
        return {'success': False, 'error': 'No file provided'}
    
    # Check file size (5MB limit)
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)  # Reset file pointer
    
    if file_size > 5 * 1024 * 1024:  # 5MB
        return {'success': False, 'error': 'File size too large (max 5MB)'}
    
    # Check filename
    if not file.filename:
        return {'success': False, 'error': 'Invalid filename'}
    
    try:
        # Generate unique filename while preserving original name
        original_name = secure_filename(file.filename)
        file_ext = original_name.rsplit('.', 1)[1].lower() if '.' in original_name else ''
        name_without_ext = original_name.rsplit('.', 1)[0] if '.' in original_name else original_name
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        
        if post_id:
            filename = f"post_{post_id}_{name_without_ext}_{timestamp}_{unique_id}.{file_ext}"
        else:
            filename = f"attachment_{name_without_ext}_{timestamp}_{unique_id}.{file_ext}"
        
        # Create attachment subdirectory by date
        date_folder = datetime.now().strftime('%Y/%m')
        
        # Check if we're on server (detect by /image directory existence)
        if os.path.exists('/image'):
            # Server environment - use /image/uploads/attachments/
            base_attachment_dir = '/image/uploads/attachments'
            save_folder = os.path.join(base_attachment_dir, date_folder)
            os.makedirs(save_folder, exist_ok=True)
            
            # Full file path
            file_path = os.path.join(save_folder, filename)
            
            # Save file
            file.save(file_path)
            
            # Return relative path for database storage (without leading /)
            relative_path = f"image/uploads/attachments/{date_folder}/{filename}"
        else:
            # Local environment - use static/uploads/attachments/
            attachment_folder = os.path.join(os.getcwd(), 'static', 'uploads', 'attachments')
            save_folder = os.path.join(attachment_folder, date_folder)
            os.makedirs(save_folder, exist_ok=True)
            
            # Full file path
            file_path = os.path.join(save_folder, filename)
            
            # Save file
            file.save(file_path)
            
            # Return attachment info for database storage
            # Store relative path from static directory for web access
            static_dir = os.path.join(os.getcwd(), 'static')
            relative_path = os.path.relpath(file_path, static_dir).replace('\\', '/')
        
        return {
            'success': True,
            'path': relative_path,  # Store relative path like "uploads/attachments/2025/08/filename.ext"
            'original_name': file.filename,
            'size': file_size,
            'filename': filename
        }
        
    except Exception as e:
        print(f"Error saving attachment: {e}")
        return {'success': False, 'error': f'Error saving file: {str(e)}'}

def send_password_reset_email(email, reset_token):
    """Send password reset email"""
    # Check if email is configured
    if not EMAIL_CONFIGURED:
        print("Email not configured. Please set EMAIL_ADDRESS and EMAIL_PASSWORD environment variables.")
        return False
        
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG['email']
        msg['To'] = email
        msg['Subject'] = "STEM Platform - Password Reset Request"
        
        # Create reset URL
        reset_url = url_for('reset_password_confirm', token=reset_token, _external=True)
        
        body = f"""
        Hello,
        
        You have requested to reset your password for the STEM Academic Resource Platform.
        
        Please click the link below to reset your password:
        {reset_url}
        
        This link will expire in 1 hour.
        
        If you did not request this password reset, please ignore this email.
        
        Best regards,
        STEM Platform Team
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        server.starttls()
        server.login(EMAIL_CONFIG['email'], EMAIL_CONFIG['password'])
        text = msg.as_string()
        server.sendmail(EMAIL_CONFIG['email'], email, text)
        server.quit()
        
        return True
    except Exception as e:
        print(f"Email sending failed: {e}")
        return False

def create_password_reset_token():
    """Create a secure password reset token"""
    return secrets.token_urlsafe(32)

def store_reset_token(email, token):
    """Store password reset token in database"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Delete any existing tokens for this email
        cursor.execute("DELETE FROM password_reset_tokens WHERE email = %s", (email,))
        
        # Insert new token with 1 hour expiration
        beijing_now = get_beijing_now()
        expires_at = beijing_now + timedelta(hours=1)
        cursor.execute("""
            INSERT INTO password_reset_tokens (email, token, expires_at, created_at)
            VALUES (%s, %s, %s, %s)
        """, (email, token, expires_at, beijing_now))
        
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except Exception as e:
        print(f"Error storing reset token: {e}")
        return False

def verify_reset_token(token):
    """Verify password reset token and return email if valid"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        cursor.execute("""
            SELECT email FROM password_reset_tokens 
            WHERE token = %s AND expires_at > NOW()
        """, (token,))
        
        result = cursor.fetchone()
        cursor.close()
        connection.close()
        
        return result[0] if result else None
    except Exception as e:
        print(f"Error verifying reset token: {e}")
        return None

@app.route('/')
def index():
    """Home page - STEM Platform"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get featured resources (restore original resources table query)
        cursor.execute("""
            SELECT r.id, r.title, r.description, r.category, r.view_count, r.like_count,
                   u.username as author_name, r.created_at
            FROM resources r
            JOIN users u ON r.user_id = u.id
            WHERE r.status = 'active'
            ORDER BY r.view_count DESC, r.created_at DESC
            LIMIT 6
        """)
        featured_resources = cursor.fetchall()
        
        # Get recent forum posts (ordered by view count for popular content)
        cursor.execute("""
            SELECT fp.id, fp.title, fp.category, fp.view_count, fp.reply_count,
                   u.username as author_name, fp.created_at
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            WHERE fp.status = 'active' AND fp.approval_status = 'approved'
            ORDER BY fp.view_count DESC, fp.created_at DESC
            LIMIT 5
        """)
        recent_posts = cursor.fetchall()
        
        # Get statistics
        cursor.execute("SELECT COUNT(*) FROM users WHERE registration_status = 'approved'")
        total_users = cursor.fetchone()['COUNT(*)']
        
        cursor.execute("SELECT COUNT(*) FROM resources WHERE status = 'active'")
        total_resources = cursor.fetchone()['COUNT(*)']
        
        cursor.execute("SELECT COUNT(*) FROM forum_posts WHERE status = 'active'")
        total_posts = cursor.fetchone()['COUNT(*)']
        
        stats = {
            'users': total_users,
            'resources': total_resources,
            'posts': total_posts
        }
        
        cursor.close()
        connection.close()
        
        return render_template('index.html', 
                             featured_resources=featured_resources,
                             recent_posts=recent_posts,
                             stats=stats)
        
    except Exception as e:
        print(f"Error loading home page: {e}")
        return render_template('index.html', 
                             featured_resources=[],
                             recent_posts=[],
                             stats={'users': 0, 'resources': 0, 'posts': 0})

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Simplified user registration"""
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # Validation
        if not username or not email or not password:
            flash('All fields are required!', 'error')
            return render_template('register.html')
        
        if password != confirm_password:
            flash('Passwords do not match!', 'error')
            return render_template('register.html')
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long!', 'error')
            return render_template('register.html')
        
        # Validate English name (letters, spaces, hyphens only)
        if not username.replace(' ', '').replace('-', '').isalpha():
            flash('Name must contain only English letters, spaces, and hyphens!', 'error')
            return render_template('register.html')
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            
            # Check if username or email already exists
            cursor.execute("SELECT id FROM users WHERE username = %s OR email = %s", (username, email))
            if cursor.fetchone():
                flash('Name or email already exists!', 'error')
                return render_template('register.html')
            
            # Hash password
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            # Insert new user with approved status for immediate access
            cursor.execute("""
                INSERT INTO users (username, email, password_hash, registration_status, user_role)
                VALUES (%s, %s, %s, 'approved', 'student')
            """, (username, email, password_hash))
            
            connection.commit()
            flash('Registration successful! You can now log in with your account.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            flash(f'Registration failed: {str(e)}', 'error')
            return render_template('register.html')
        finally:
            if 'connection' in locals() and connection.open:
                cursor.close()
                connection.close()
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Username and password are required!', 'error')
            return render_template('login.html')
        
        try:
            connection = get_db_connection()
            if connection is None:
                flash('Database service is temporarily unavailable. Please try again later.', 'error')
                return render_template('login.html')
                
            cursor = connection.cursor()
            
            # Get user from database
            cursor.execute("""
                SELECT id, username, email, password_hash, user_role, registration_status
                FROM users WHERE username = %s OR email = %s
            """, (username, username))
            
            user_data = cursor.fetchone()
            
            if user_data and bcrypt.checkpw(password.encode('utf-8'), user_data[3].encode('utf-8')):
                # Check registration status before allowing login
                registration_status = user_data[5]
                
                if registration_status == 'pending':
                    flash('Your account is pending admin approval. Please wait for activation.', 'warning')
                    return render_template('login.html')
                elif registration_status == 'rejected':
                    flash('Your account has been rejected. Please contact administrators.', 'error')
                    return render_template('login.html')
                
                # Create user object and log in (only if approved)
                user = User(
                    id=user_data[0],
                    username=user_data[1],
                    email=user_data[2],
                    user_role=user_data[4],
                    registration_status=user_data[5]
                )
                login_user(user)
                flash('Login successful!', 'success')
                
                # Log user activity (simplified - no need to wait for commit)
                try:
                    cursor.execute("""
                        INSERT INTO user_activities (user_id, activity_type, ip_address)
                        VALUES (%s, 'login', %s)
                    """, (user.id, request.remote_addr))
                    connection.commit()
                except Exception as activity_error:
                    print(f"Failed to log user activity: {activity_error}")
                    # Don't fail login if activity logging fails
                
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid username or password!', 'error')
                
        except Exception as e:
            print(f"Login error: {e}")
            flash('Database connection error. Please try again later.', 'error')
        finally:
            if 'connection' in locals() and connection and connection.open:
                cursor.close()
                connection.close()
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """User logout"""
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    """Password reset - two methods: with current password or with username+email verification"""
    if request.method == 'POST':
        reset_method = request.form.get('reset_method')
        
        if reset_method == 'with_password':
            # Method 1: Reset with current password
            username = request.form.get('username')
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            if not username or not current_password or not new_password or not confirm_password:
                flash('All fields are required!', 'error')
                return render_template('reset_password.html')
            
            if new_password != confirm_password:
                flash('New passwords do not match!', 'error')
                return render_template('reset_password.html')
            
            if len(new_password) < 6:
                flash('New password must be at least 6 characters long!', 'error')
                return render_template('reset_password.html')
            
            try:
                connection = get_db_connection()
                cursor = connection.cursor()
                
                # Check if username exists and verify current password
                cursor.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
                user_data = cursor.fetchone()
                
                if not user_data:
                    flash('Username does not exist!', 'error')
                    return render_template('reset_password.html')
                
                user_id, stored_password = user_data
                
                # Verify current password
                if not bcrypt.checkpw(current_password.encode('utf-8'), stored_password.encode('utf-8')):
                    flash('Current password is incorrect!', 'error')
                    return render_template('reset_password.html')
                
                # Hash new password
                hashed_new_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                
                # Update password
                cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed_new_password, user_id))
                connection.commit()
                
                cursor.close()
                connection.close()
                
                flash('Password updated successfully! Please login with your new password.', 'success')
                return redirect(url_for('login'))
                
            except Exception as e:
                print(f"Password reset error: {e}")
                flash('An error occurred while updating password. Please try again.', 'error')
        
        elif reset_method == 'without_password':
            # Method 2: Reset with username and email verification
            username = request.form.get('username_verify')
            email = request.form.get('email_verify')
            new_password = request.form.get('new_password_verify')
            confirm_password = request.form.get('confirm_password_verify')
            
            if not username or not email or not new_password or not confirm_password:
                flash('All fields are required!', 'error')
                return render_template('reset_password.html')
            
            if new_password != confirm_password:
                flash('New passwords do not match!', 'error')
                return render_template('reset_password.html')
            
            if len(new_password) < 6:
                flash('New password must be at least 6 characters long!', 'error')
                return render_template('reset_password.html')
            
            try:
                connection = get_db_connection()
                cursor = connection.cursor()
                
                # Verify username and email match
                cursor.execute("SELECT id FROM users WHERE username = %s AND email = %s", (username, email))
                user_data = cursor.fetchone()
                
                if not user_data:
                    flash('Username and email do not match our records!', 'error')
                    return render_template('reset_password.html')
                
                user_id = user_data[0]
                
                # Hash new password
                hashed_new_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                
                # Update password
                cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed_new_password, user_id))
                connection.commit()
                
                cursor.close()
                connection.close()
                
                flash('Password updated successfully! Please login with your new password.', 'success')
                return redirect(url_for('login'))
                
            except Exception as e:
                print(f"Password reset error: {e}")
                flash('An error occurred while updating password. Please try again.', 'error')
    
    return render_template('reset_password.html')

@app.route('/reset_password_confirm/<token>', methods=['GET', 'POST'])
def reset_password_confirm(token):
    """Password reset confirmation"""
    email = verify_reset_token(token)
    if not email:
        flash('Invalid or expired reset link.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not password or not confirm_password:
            flash('Both password fields are required!', 'error')
            return render_template('reset_password_confirm.html')
        
        if password != confirm_password:
            flash('Passwords do not match!', 'error')
            return render_template('reset_password_confirm.html')
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long!', 'error')
            return render_template('reset_password_confirm.html')
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            
            # Hash new password
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            # Update password
            cursor.execute("""
                UPDATE users SET password_hash = %s WHERE email = %s
            """, (password_hash, email))
            
            # Delete used token
            cursor.execute("DELETE FROM password_reset_tokens WHERE token = %s", (token,))
            
            connection.commit()
            cursor.close()
            connection.close()
            
            flash('Password successfully reset! Please log in with your new password.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            flash('Failed to reset password. Please try again.', 'error')
    
    return render_template('reset_password_confirm.html')

@app.route('/dashboard')
def dashboard():
    """User dashboard - redirect based on user status"""
    # 检查用户是否已登录
    if not current_user.is_authenticated:
        flash('Please log in to access your dashboard.', 'warning')
        return redirect(url_for('login'))
    
    if current_user.registration_status != 'approved':
        return render_template('pending_approval.html')
    else:
        # Track page view for dashboard
        user_id = current_user.id if current_user.is_authenticated else None
        track_page_view('dashboard', None, user_id)
        
        # Redirect to my_resources as before
        return redirect(url_for('my_resources'))

@app.route('/my_resources')
def my_resources():
    """User's resources management page - shows forum posts and feedback"""
    # 检查用户是否已登录
    if not current_user.is_authenticated:
        flash('Please log in to access your resources.', 'warning')
        return redirect(url_for('login'))
    
    try:
        # Track page view
        user_id = current_user.id if current_user.is_authenticated else None
        track_page_view('my_resources', None, user_id)
        
        connection = get_db_connection()
        if connection is None:
            flash('Database service temporarily unavailable, showing default data.', 'warning')
            stats = {'total_posts': 0, 'approved_posts': 0, 'pending_posts': 0, 'rejected_posts': 0, 
                    'total_feedback': 0, 'responded_feedback': 0, 'pending_feedback': 0, 'total_views': 0}
            return render_template('my_resources.html', 
                                 user=current_user, 
                                 forum_posts=[], 
                                 feedback_list=[],
                                 stats=stats)
                             
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get current user's forum posts
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content, fp.category, fp.approval_status, 
                   fp.view_count, fp.reply_count, fp.created_at, fp.rejection_reason,
                   fp.reviewed_at, reviewer.username as reviewer_name
            FROM forum_posts fp
            LEFT JOIN users reviewer ON fp.reviewed_by = reviewer.id
            WHERE fp.user_id = %s
            ORDER BY fp.created_at DESC
        """, (current_user.id,))
        
        forum_posts = cursor.fetchall()
        
        # Get current user's feedback
        cursor.execute("""
            SELECT uf.id, uf.title as subject, uf.description as message, uf.status, uf.created_at,
                   uf.admin_response, uf.responded_at, uf.responded_by,
                   admin.username as admin_username
            FROM user_feedback uf
            LEFT JOIN users admin ON uf.responded_by = admin.id
            WHERE uf.user_id = %s
            ORDER BY uf.created_at DESC
        """, (current_user.id,))
        
        feedback_list = cursor.fetchall()
        
        # Calculate statistics
        stats = {
            'total_posts': len(forum_posts),
            'approved_posts': len([p for p in forum_posts if p['approval_status'] == 'approved']),
            'pending_posts': len([p for p in forum_posts if p['approval_status'] == 'pending']),
            'rejected_posts': len([p for p in forum_posts if p['approval_status'] == 'rejected']),
            'total_feedback': len(feedback_list),
            'responded_feedback': len([f for f in feedback_list if f['admin_response']]),
            'pending_feedback': len([f for f in feedback_list if not f['admin_response']]),
            'total_views': sum(p['view_count'] or 0 for p in forum_posts)
        }
        
        cursor.close()
        connection.close()
        
        return render_template('my_resources.html', 
                             user=current_user, 
                             forum_posts=forum_posts or [],
                             feedback_list=feedback_list or [], 
                             stats=stats)
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error fetching user data: {e}")
        print(f"Full traceback: {error_details}")
        flash('Error fetching data, showing default data.', 'warning')
        stats = {'total_posts': 0, 'approved_posts': 0, 'pending_posts': 0, 'rejected_posts': 0, 
                'total_feedback': 0, 'responded_feedback': 0, 'pending_feedback': 0, 'total_views': 0}
        return render_template('my_resources.html', 
                             user=current_user, 
                             forum_posts=[],
                             feedback_list=[], 
                             stats=stats)

@app.route('/my_resources/post/<int:post_id>')
def get_post_details(post_id):
    """Get detailed information for a specific post (for rejected post preview)"""
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
            
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get post details - only allow user to view their own posts
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content, fp.category, fp.approval_status, 
                   fp.view_count, fp.reply_count, fp.created_at, fp.rejection_reason,
                   fp.cover_image, fp.additional_images, fp.reviewed_at,
                   reviewer.username as reviewer_name
            FROM forum_posts fp
            LEFT JOIN users reviewer ON fp.reviewed_by = reviewer.id
            WHERE fp.id = %s AND fp.user_id = %s
        """, (post_id, current_user.id))
        
        post_data = cursor.fetchone()
        
        if not post_data:
            cursor.close()
            connection.close()
            return jsonify({'success': False, 'error': 'Post not found or access denied'}), 404
        
        # Parse additional images if they exist
        additional_images = []
        if post_data.get('additional_images'):
            additional_images = [img.strip() for img in post_data['additional_images'].split(',') if img.strip()]
        
        # Create response data
        post = {
            'id': post_data['id'],
            'title': post_data['title'],
            'content': post_data['content'],
            'category': post_data['category'],
            'approval_status': post_data['approval_status'],
            'view_count': post_data['view_count'],
            'reply_count': post_data['reply_count'],
            'created_at': post_data['created_at'].isoformat() if post_data['created_at'] else None,
            'rejection_reason': post_data.get('rejection_reason'),
            'cover_image': post_data.get('cover_image'),
            'additional_images': additional_images,
            'reviewed_at': post_data['reviewed_at'].isoformat() if post_data.get('reviewed_at') else None,
            'reviewer_name': post_data.get('reviewer_name')
        }
        
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'post': post})
        
    except Exception as e:
        print(f"Error fetching post details: {e}")
        return jsonify({'success': False, 'error': 'Failed to load post details'}), 500

@app.route('/admin/resources/new', methods=['GET', 'POST'])
@admin_required
def admin_new_resource():
    """Admin create new resource page"""
    if request.method == 'POST':
        try:
            title = request.form.get('title', '').strip()
            content = request.form.get('content', '').strip()
            education_level = request.form.get('education_level', '').strip()
            category = request.form.get('category', 'experience').strip()  # Default to experience
            
            if not all([title, content, education_level]):
                flash('Title, content, and education level are required.', 'error')
                return render_template('admin/new_resource.html')
            
            # Handle cover image (URL or file upload)
            cover_image_path = None
            cover_image_url = request.form.get('cover_image_url', '').strip()
            
            if cover_image_url:
                cover_image_path = cover_image_url
            elif 'cover_image' in request.files:
                cover_file = request.files['cover_image']
                if cover_file and cover_file.filename:
                    # Generate unique filename with timestamp
                    file_ext = cover_file.filename.rsplit('.', 1)[1].lower()
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    unique_id = str(uuid.uuid4())[:8]
                    filename = f"resource_cover_{timestamp}_{unique_id}.{file_ext}"
                    
                    # Create uploads directory with date structure
                    date_folder = datetime.now().strftime('%Y/%m')
                    
                    # Check if we're on server (detect by /image directory existence)
                    if os.path.exists('/image'):
                        # Server environment - use /image/resources/
                        uploads_dir = os.path.join('/image/resources', date_folder)
                        os.makedirs(uploads_dir, exist_ok=True)
                        upload_path = os.path.join(uploads_dir, filename)
                        cover_file.save(upload_path)
                        # Store relative path for server
                        cover_image_path = f'resources/{date_folder}/{filename}'
                    else:
                        # Local environment - use static/uploads/resources/
                        static_dir = os.path.join(os.getcwd(), 'static')
                        uploads_dir = os.path.join(static_dir, 'uploads', 'resources', date_folder)
                        os.makedirs(uploads_dir, exist_ok=True)
                        upload_path = os.path.join(uploads_dir, filename)
                        cover_file.save(upload_path)
                        # Store relative path for local
                        cover_image_path = f'uploads/resources/{date_folder}/{filename}'
            
            # Handle additional images (using Forum's approach)
            additional_images_paths = []
            additional_images = request.files.getlist('additional_images')
            
            
            for img_file in additional_images:
                if img_file.filename:
                    # Generate unique filename with timestamp
                    file_ext = img_file.filename.rsplit('.', 1)[1].lower()
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    unique_id = str(uuid.uuid4())[:8]
                    filename = f"resource_additional_{timestamp}_{unique_id}.{file_ext}"
                    
                    # Create uploads directory with date structure
                    date_folder = datetime.now().strftime('%Y/%m')
                    
                    # Check if we're on server (detect by /image directory existence)
                    if os.path.exists('/image'):
                        # Server environment - use /image/resources/
                        uploads_dir = os.path.join('/image/resources', date_folder)
                        os.makedirs(uploads_dir, exist_ok=True)
                        upload_path = os.path.join(uploads_dir, filename)
                        img_file.save(upload_path)
                        # Store relative path for server
                        resource_path = f'resources/{date_folder}/{filename}'
                    else:
                        # Local environment - use static/uploads/resources/
                        static_dir = os.path.join(os.getcwd(), 'static')
                        uploads_dir = os.path.join(static_dir, 'uploads', 'resources', date_folder)
                        os.makedirs(uploads_dir, exist_ok=True)
                        upload_path = os.path.join(uploads_dir, filename)
                        img_file.save(upload_path)
                        # Store relative path for local
                        resource_path = f'uploads/resources/{date_folder}/{filename}'
                    additional_images_paths.append(resource_path)
                else:
                    pass
            
            # Save to database
            connection = get_db_connection()
            cursor = connection.cursor()
            
            # Map education_level to category
            # Get additional form fields
            subject = request.form.get('subject', 'general').strip()
            resource_type = request.form.get('resource_type', 'notes').strip()
            difficulty_level = request.form.get('difficulty_level', 'intermediate').strip()
            description = request.form.get('description', '').strip()
            
            # Map education_level to resources table category values (updated for new structure)
            # Note: Keep category values short to fit database field constraints
            category_mapping = {
                'igcse': 'IGCSE',
                'alevel': 'A-LEVEL', 
                'ap': 'BPHO',  # Map AP to BPHO for now
                'competition': 'PHYSICS_BOWL',
                'university': 'UNIVERSITY_RESOURCES'
            }
            resource_category = category_mapping.get(education_level, 'IGCSE')
            
            # Debug output
            additional_images_string = ','.join(additional_images_paths) if additional_images_paths else None
            
            # Insert resource into resources table (admin resources are automatically active)
            cursor.execute("""
                INSERT INTO resources (user_id, title, description, content, category, subject, 
                                     resource_type, difficulty_level, cover_image, additional_images, 
                                     status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
            """, (
                current_user.id, title, description or title, content, resource_category, subject,
                resource_type, difficulty_level, cover_image_path, 
                additional_images_string, get_beijing_now()
            ))
            
            connection.commit()
            cursor.close()
            connection.close()
            
            flash('Resource published successfully!', 'success')
            return redirect(url_for('admin_resources'))
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"Error creating resource: {e}")
            print(f"Full traceback: {error_details}")
            flash(f'Error creating resource: {str(e)}', 'error')
            return render_template('admin/new_resource.html')
    
    return render_template('admin/new_resource.html')

@app.route('/profile')
def profile():
    """User profile page"""
    # 检查用户是否已登录
    if not current_user.is_authenticated:
        flash('Please log in to access your profile.', 'warning')
        return redirect(url_for('login'))
    
    return render_template('profile.html', user=current_user)

@app.route('/change_password', methods=['POST'])
def change_password():
    """Change user password"""
    try:
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not all([current_password, new_password, confirm_password]):
            flash('All password fields are required.', 'error')
            return redirect(url_for('profile'))
        
        # Get current user's password hash from database
        connection = get_db_connection()
        cursor = connection.cursor()
        
        cursor.execute("SELECT password_hash FROM users WHERE id = %s", (current_user.id,))
        user_data = cursor.fetchone()
        
        if not user_data:
            flash('User not found.', 'error')
            return redirect(url_for('profile'))
        
        stored_password_hash = user_data[0]
        
        # Check current password using bcrypt (consistent with reset_password)
        if not bcrypt.checkpw(current_password.encode('utf-8'), stored_password_hash.encode('utf-8')):
            flash('Current password is incorrect.', 'error')
            return redirect(url_for('profile'))
        
        # Check new password confirmation
        if new_password != confirm_password:
            flash('New passwords do not match.', 'error')
            return redirect(url_for('profile'))
        
        # Validate new password strength
        if len(new_password) < 6:
            flash('New password must be at least 6 characters long.', 'error')
            return redirect(url_for('profile'))
        
        # Hash new password using bcrypt (consistent with reset_password)
        new_password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # Update password in database with correct field name
        cursor.execute("""
            UPDATE users SET password_hash = %s 
            WHERE id = %s
        """, (new_password_hash, current_user.id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        flash('Password successfully updated!', 'success')
        return redirect(url_for('profile'))
        
    except Exception as e:
        print(f"Error changing password: {e}")
        flash('An error occurred while changing password.', 'error')
        return redirect(url_for('profile'))

# Admin routes
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if verify_admin_credentials(username, password):
            session['admin_logged_in'] = True
            flash('Admin login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid admin credentials!', 'error')
    
    return render_template('admin/login.html')

@app.route('/admin/logout')
@admin_required
def admin_logout():
    """Admin logout"""
    session.pop('admin_logged_in', None)
    flash('Admin logged out successfully.', 'info')
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin():
    """Admin root redirect to dashboard"""
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get pending registrations
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.school, u.grade_level, u.created_at
            FROM users u
            WHERE u.registration_status = 'pending'
            ORDER BY u.created_at DESC
        """)
        pending_users = cursor.fetchall()
        
        # Get pending forum posts for quick approval
        cursor.execute("""
            SELECT fp.id, fp.title, fp.category, fp.created_at,
                   u.username as author_name
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            WHERE fp.approval_status = 'pending'
            ORDER BY fp.created_at DESC
        """)
        pending_posts = cursor.fetchall()
        
        # Get comprehensive statistics
        cursor.execute("SELECT COUNT(*) as count FROM users")
        total_users = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM resources")
        total_resources = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM forum_posts")
        total_posts = cursor.fetchone()['count']
        
        # Get forum post statistics by approval status
        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN approval_status = 'pending' THEN 1 END) as pending_posts,
                COUNT(CASE WHEN approval_status = 'approved' THEN 1 END) as approved_posts,
                COUNT(CASE WHEN approval_status = 'rejected' THEN 1 END) as rejected_posts
            FROM forum_posts
        """)
        forum_stats = cursor.fetchone()
        
        # Get user activity statistics (last 30 days)
        cursor.execute("""
            SELECT COUNT(DISTINCT user_id) as active_users
            FROM user_activity_logs 
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        """)
        active_users_result = cursor.fetchone()
        active_users = active_users_result['active_users'] if active_users_result else 0
        
        # Get today's statistics
        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN DATE(created_at) = CURDATE() THEN 1 END) as today_posts,
                COUNT(CASE WHEN DATE(created_at) = CURDATE() AND approval_status = 'pending' THEN 1 END) as today_pending
            FROM forum_posts
        """)
        today_stats = cursor.fetchone()
        
        # Get user feedback count
        cursor.execute("SELECT COUNT(*) as count FROM user_feedback WHERE status != 'closed'")
        open_feedback = cursor.fetchone()['count']
        
        stats = {
            'users': total_users,
            'resources': total_resources,
            'posts': total_posts,
            'pending_users': len(pending_users),
            'pending_resources': 0,  # No longer showing pending resources
            'pending_posts': forum_stats['pending_posts'],
            'approved_posts': forum_stats['approved_posts'],
            'rejected_posts': forum_stats['rejected_posts'],
            'active_users': active_users,
            'today_posts': today_stats['today_posts'],
            'today_pending': today_stats['today_pending'],
            'open_feedback': open_feedback
        }
        
        cursor.close()
        connection.close()
        
        return render_template('admin/dashboard.html',
                             pending_users=pending_users,
                             pending_posts=pending_posts,
                             stats=stats)
        
    except Exception as e:
        print(f"Error loading admin dashboard: {e}")
        return render_template('admin/dashboard.html',
                             pending_users=[],
                             pending_posts=[],
                             stats={'users': 0, 'resources': 0, 'posts': 0, 
                                   'pending_users': 0, 'pending_resources': 0})

@app.route('/admin/resources')
@admin_required
def admin_resources():
    """Admin resource management page"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get all resources from resources table
        cursor.execute("""
            SELECT r.id, r.title, r.description, r.category as subject, r.category, 
                   r.resource_type, r.difficulty_level, r.status, r.view_count, 
                   r.download_count, r.like_count, u.username as author_name, 
                   r.created_at, r.updated_at
            FROM resources r
            JOIN users u ON r.user_id = u.id
            ORDER BY r.created_at DESC
        """)
        
        resources = cursor.fetchall()
        cursor.close()
        connection.close()
        
        return render_template('admin/resources.html', resources=resources)
        
    except Exception as e:
        print(f"Error loading admin resources: {e}")
        flash('Error loading resources', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/approve_resource/<int:resource_id>', methods=['POST'])
@admin_required
def approve_resource(resource_id):
    """Approve a resource"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        cursor.execute("""
            UPDATE resources 
            SET status = 'active', updated_at = %s 
            WHERE id = %s
        """, (get_beijing_now(), resource_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Resource approved successfully'})
        
    except Exception as e:
        print(f"Error approving resource: {e}")
        return jsonify({'success': False, 'message': 'Database error'}), 500

@app.route('/admin/reject_resource/<int:resource_id>', methods=['POST'])
@admin_required
def reject_resource(resource_id):
    """Reject a resource"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        cursor.execute("""
            UPDATE resources 
            SET status = 'archived', updated_at = %s 
            WHERE id = %s
        """, (get_beijing_now(), resource_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Resource rejected successfully'})
        
    except Exception as e:
        print(f"Error rejecting resource: {e}")
        return jsonify({'success': False, 'message': 'Database error'}), 500

@app.route('/admin/delete_resource/<int:resource_id>', methods=['DELETE'])
@admin_required
def delete_resource(resource_id):
    """Delete a resource with image cleanup"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # First, get the resource data to retrieve image paths
        cursor.execute("""
            SELECT cover_image, additional_images 
            FROM resources 
            WHERE id = %s
        """, (resource_id,))
        
        resource_data = cursor.fetchone()
        if not resource_data:
            cursor.close()
            connection.close()
            return jsonify({'success': False, 'message': 'Resource not found'}), 404
        
        # Clean up images before deleting from database
        cleanup_results = cleanup_post_images(
            resource_data.get('cover_image'), 
            resource_data.get('additional_images')
        )
        
        print(f"🧹 Resource {resource_id} 图片清理结果: {cleanup_results}")
        
        # Delete the resource (foreign key constraints will handle cleanup)
        cursor.execute("""
            DELETE FROM resources 
            WHERE id = %s
        """, (resource_id,))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        # Prepare response message
        message = 'Resource deleted successfully'
        if cleanup_results['errors']:
            message += f" (Some images could not be deleted: {len(cleanup_results['errors'])} errors)"
        
        return jsonify({
            'success': True, 
            'message': message,
            'cleanup_results': cleanup_results
        })
        
    except Exception as e:
        print(f"Error deleting resource: {e}")
        return jsonify({'success': False, 'message': 'Database error'}), 500

@app.route('/admin/archive_resource/<int:resource_id>', methods=['POST'])
@admin_required
def archive_resource(resource_id):
    """Archive a resource"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        cursor.execute("""
            UPDATE resources 
            SET status = 'archived', updated_at = %s 
            WHERE id = %s
        """, (get_beijing_now(), resource_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Resource archived successfully'})
        
    except Exception as e:
        print(f"Error archiving resource: {e}")
        return jsonify({'success': False, 'message': 'Database error'}), 500

@app.route('/admin/activate_resource/<int:resource_id>', methods=['POST'])
@admin_required
def activate_resource(resource_id):
    """Activate a resource"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        cursor.execute("""
            UPDATE resources 
            SET status = 'active', updated_at = %s 
            WHERE id = %s
        """, (get_beijing_now(), resource_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Resource activated successfully'})
        
    except Exception as e:
        print(f"Error activating resource: {e}")
        return jsonify({'success': False, 'message': 'Database error'}), 500

# Duplicate delete_resource function removed - using the earlier one that uses forum_posts table

@app.route('/view_resource/<int:resource_id>')
def view_resource(resource_id):
    """View individual resource details"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get resource from resources table
        cursor.execute("""
            SELECT r.id, r.title, r.description, r.content, r.category, r.subject, r.resource_type,
                   r.difficulty_level, r.status, r.cover_image, r.additional_images, 
                   r.view_count, r.download_count, r.like_count, r.created_at, r.updated_at,
                   u.username as author_name
            FROM resources r
            JOIN users u ON r.user_id = u.id
            WHERE r.id = %s AND r.status = 'active'
        """, (resource_id,))
        
        resource = cursor.fetchone()
        
        if not resource:
            flash('Resource not found', 'error')
            return redirect(url_for('index'))
            
        # Increment view count
        cursor.execute("""
            UPDATE resources SET view_count = view_count + 1 
            WHERE id = %s
        """, (resource_id,))
        connection.commit()
            
        cursor.close()
        connection.close()
        
        return render_template('resource_detail.html', resource=resource)
        
    except Exception as e:
        print(f"Error loading resource: {e}")
        flash('Error loading resource', 'error')
        return redirect(url_for('index'))

@app.route('/download/resource/<int:resource_id>')
def download_resource(resource_id):
    """Download resource by ID"""
    try:
        # Get resource info from database
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        cursor.execute("""
            SELECT r.id, r.title, r.cover_image, r.additional_images 
            FROM resources r
            WHERE r.id = %s AND r.status = 'active'
        """, (resource_id,))
        resource = cursor.fetchone()
        
        if not resource:
            flash('Resource not found', 'error')
            return redirect(url_for('subjects_overview'))
        
        # Increment download count
        cursor.execute("""
            UPDATE resources SET download_count = download_count + 1 
            WHERE id = %s
        """, (resource_id,))
        connection.commit()
        
        cursor.close()
        connection.close()
        
        # For now, redirect to resource detail page
        # In a real implementation, you'd serve the actual file
        flash('Download initiated! (Feature in development)', 'info')
        return redirect(url_for('view_resource', resource_id=resource_id))
        
    except Exception as e:
        print(f"Error downloading resource: {e}")
        flash('Error downloading resource', 'error')
        return redirect(url_for('subjects_overview'))

# Resources Category Routes
@app.route('/subjects/<subject>')
def subjects_category(subject):
    """Display resources for a specific subject"""
    try:
        # Valid subjects
        valid_subjects = ['math', 'physics', 'chemistry', 'biology']
        
        if subject.lower() not in valid_subjects:
            flash('Invalid subject', 'error')
            return redirect(url_for('subjects_overview'))
        
        subject = subject.lower()
        
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get resources for this subject grouped by education level
        cursor.execute("""
            SELECT r.id, r.title, r.description, r.category, r.subject, r.resource_type,
                   r.difficulty_level, r.view_count, r.download_count, r.like_count,
                   r.cover_image, r.created_at, u.username as author_name
            FROM resources r
            JOIN users u ON r.user_id = u.id
            WHERE r.subject = %s AND r.status = 'active'
            ORDER BY r.category, r.view_count DESC, r.created_at DESC
        """, (subject,))
        
        all_resources = cursor.fetchall()
        
        # Group resources by education level
        resources_by_level = {}
        for resource in all_resources:
            level = resource['category']
            if level not in resources_by_level:
                resources_by_level[level] = []
            resources_by_level[level].append(resource)
        
        cursor.close()
        connection.close()
        
        # Display names
        subject_names = {
            'math': 'Mathematics',
            'physics': 'Physics', 
            'chemistry': 'Chemistry',
            'biology': 'Biology'
        }
        
        subject_title = subject_names.get(subject, subject.title())
        
        return render_template('subjects_category.html', 
                             all_resources=all_resources,
                             resources_by_level=resources_by_level,
                             subject=subject,
                             subject_title=subject_title,
                             page_title=f"{subject_title} Resources")
        
    except Exception as e:
        print(f"Error loading subject resources: {e}")
        flash('Error loading resources', 'error')
        return redirect(url_for('subjects_overview'))

@app.route('/subjects')
def subjects_overview():
    """Overview page showing all subjects (Maths, Physics, Chemistry, Biology)"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get resource counts by subject
        cursor.execute("""
            SELECT subject, COUNT(*) as count
            FROM resources 
            WHERE status = 'active'
            GROUP BY subject
        """)
        
        subject_counts = {row['subject']: row['count'] for row in cursor.fetchall()}
        
        # Get featured resources from each subject
        featured_resources = {}
        subjects = ['math', 'physics', 'chemistry', 'biology']
        
        for subject in subjects:
            cursor.execute("""
                SELECT r.id, r.title, r.description, r.category, r.resource_type,
                       r.view_count, r.created_at, u.username as author_name
                FROM resources r
                JOIN users u ON r.user_id = u.id
                WHERE r.subject = %s AND r.status = 'active'
                ORDER BY r.view_count DESC, r.created_at DESC
                LIMIT 3
            """, (subject,))
            featured_resources[subject] = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        return render_template('subjects_overview.html', 
                             subject_counts=subject_counts,
                             featured_resources=featured_resources)
        
    except Exception as e:
        print(f"Error loading subjects overview: {e}")
        flash('Error loading subjects overview', 'error')
        return redirect(url_for('index'))

# New route for specific subject and education level
@app.route('/subjects/<subject>/<education_level>')
def subject_level_resources(subject, education_level):
    """Display resources for a specific subject and education level"""
    try:
        # Valid subjects and education levels
        valid_subjects = ['math', 'physics', 'chemistry', 'biology']
        valid_levels = ['igcse', 'alevel', 'ap', 'competition', 'university']
        
        if subject.lower() not in valid_subjects or education_level.lower() not in valid_levels:
            flash('Invalid subject or education level', 'error')
            return redirect(url_for('subjects_overview'))
        
        subject = subject.lower()
        education_level = education_level.lower()
        
        # Map education_level to database category
        level_mapping = {
            'igcse': 'IGCSE',
            'alevel': 'A-LEVEL',
            'ap': 'AP',
            'competition': 'COMP',
            'university': 'UNIV'
        }
        
        db_category = level_mapping[education_level]
        
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get resources for this subject and education level
        cursor.execute("""
            SELECT r.id, r.title, r.description, r.category, r.subject, r.resource_type,
                   r.difficulty_level, r.view_count, r.download_count, r.like_count,
                   r.cover_image, r.created_at, u.username as author_name
            FROM resources r
            JOIN users u ON r.user_id = u.id
            WHERE r.subject = %s AND r.category = %s AND r.status = 'active'
            ORDER BY r.view_count DESC, r.created_at DESC
        """, (subject, db_category))
        
        resources = cursor.fetchall()
        cursor.close()
        connection.close()
        
        # Display names
        subject_names = {
            'math': 'Mathematics',
            'physics': 'Physics', 
            'chemistry': 'Chemistry',
            'biology': 'Biology'
        }
        
        level_names = {
            'igcse': 'IGCSE',
            'alevel': 'A-Level',
            'ap': 'AP',
            'competition': 'Competition',
            'university': 'University'
        }
        
        subject_title = subject_names.get(subject, subject.title())
        level_title = level_names.get(education_level, education_level.title())
        
        return render_template('subject_level_resources.html', 
                             resources=resources,
                             subject=subject,
                             education_level=education_level,
                             subject_title=subject_title,
                             level_title=level_title,
                             page_title=f"{subject_title} - {level_title}")
        
    except Exception as e:
        print(f"Error loading subject level resources: {e}")
        flash('Error loading resources', 'error')
        return redirect(url_for('subjects_overview'))

@app.route('/subjects/<subject>/<education_level>/<category>')
def subject_level_category_resources(subject, education_level, category):
    """Display resources for a specific subject, education level, and category (three-tier filtering)"""
    try:
        # Valid subjects, education levels, and categories
        valid_subjects = ['math', 'physics', 'chemistry', 'biology']
        valid_levels = ['igcse', 'alevel', 'ap', 'competition', 'university']
        valid_categories = ['books', 'homework', 'tests', 'notes', 'questions']
        
        if (subject.lower() not in valid_subjects or 
            education_level.lower() not in valid_levels or 
            category.lower() not in valid_categories):
            flash('Invalid subject, education level, or category', 'error')
            return redirect(url_for('subjects_overview'))
        
        subject = subject.lower()
        education_level = education_level.lower()
        category = category.lower()
        
        # Map education_level to database category
        level_mapping = {
            'igcse': 'IGCSE',
            'alevel': 'A-LEVEL',
            'ap': 'AP',
            'competition': 'BPHO',  # Competition maps to BPHO
            'university': 'UNIVERSITY_RESOURCES'
        }
        
        db_category = level_mapping[education_level]
        
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # For forum posts (questions category), get from forum_posts table
        if category == 'questions':
            cursor.execute("""
                SELECT fp.id, fp.title, fp.content as description, fp.category, fp.topic,
                       fp.view_count, fp.reply_count, fp.created_at, fp.cover_image,
                       u.username as author_name, 'question' as resource_type
                FROM forum_posts fp
                JOIN users u ON fp.user_id = u.id
                WHERE fp.topic LIKE %s AND fp.category = %s AND fp.status = 'active'
                ORDER BY fp.view_count DESC, fp.created_at DESC
            """, (f"%{subject}%", category))
            
            resources = cursor.fetchall()
            # Add resource_type for template consistency
            for resource in resources:
                resource['resource_type'] = 'question'
                resource['difficulty_level'] = 'intermediate'  # Default for questions
                resource['like_count'] = 0  # Questions don't have likes yet
                resource['download_count'] = 0
        else:
            # For regular resources, get from resources table with category filter
            # Map category to resource_type for filtering
            category_to_resource_type = {
                'books': 'reference',
                'homework': 'notes', 
                'tests': 'past_paper',
                'notes': 'notes'
            }
            
            # Get resources matching all three criteria
            if category in ['books', 'homework', 'tests', 'notes']:
                # Use broader search for resource content category
                cursor.execute("""
                    SELECT r.id, r.title, r.description, r.category, r.subject, r.resource_type,
                           r.difficulty_level, r.view_count, r.download_count, r.like_count,
                           r.cover_image, r.created_at, u.username as author_name
                    FROM resources r
                    JOIN users u ON r.user_id = u.id
                    WHERE r.subject = %s AND r.category = %s
                    AND (r.resource_type = %s OR r.title LIKE %s OR r.description LIKE %s)
                    AND r.status = 'active'
                    ORDER BY r.view_count DESC, r.created_at DESC
                """, (subject, db_category, category_to_resource_type.get(category, category), 
                      f"%{category}%", f"%{category}%"))
            else:
                cursor.execute("""
                    SELECT r.id, r.title, r.description, r.category, r.subject, r.resource_type,
                           r.difficulty_level, r.view_count, r.download_count, r.like_count,
                           r.cover_image, r.created_at, u.username as author_name
                    FROM resources r
                    JOIN users u ON r.user_id = u.id
                    WHERE r.subject = %s AND r.category = %s AND r.status = 'active'
                    ORDER BY r.view_count DESC, r.created_at DESC
                """, (subject, db_category))
            
            resources = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        # Display names
        subject_names = {
            'math': 'Mathematics',
            'physics': 'Physics', 
            'chemistry': 'Chemistry',
            'biology': 'Biology'
        }
        
        level_names = {
            'igcse': 'IGCSE',
            'alevel': 'A-Level',
            'ap': 'AP (Advanced Placement)',
            'competition': 'Competition',
            'university': 'University'
        }
        
        category_names = {
            'books': 'Books',
            'homework': 'Homework',
            'tests': 'Tests',
            'notes': 'Notes',
            'questions': 'Questions'
        }
        
        subject_title = subject_names.get(subject, subject.title())
        level_title = level_names.get(education_level, education_level.title())
        category_title = category_names.get(category, category.title())
        
        return render_template('subject_level_category_resources.html', 
                             resources=resources,
                             subject=subject,
                             education_level=education_level,
                             category=category,
                             subject_title=subject_title,
                             level_title=level_title,
                             category_title=category_title,
                             page_title=f"{subject_title} - {level_title} - {category_title}")
        
    except Exception as e:
        print(f"Error loading subject level category resources: {e}")
        flash('Error loading resources', 'error')
        return redirect(url_for('subjects_overview'))

# Resource Interaction Routes
@app.route('/like_resource/<int:resource_id>', methods=['POST'])


def like_resource(resource_id):
    """Toggle like status for a resource"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Check if user has already liked this resource
        cursor.execute("""
            SELECT id FROM resource_likes 
            WHERE user_id = %s AND resource_id = %s
        """, (current_user.id, resource_id))
        
        existing_like = cursor.fetchone()
        
        if existing_like:
            # Unlike - remove the like
            cursor.execute("""
                DELETE FROM resource_likes 
                WHERE user_id = %s AND resource_id = %s
            """, (current_user.id, resource_id))
            
            # Decrease like count
            cursor.execute("""
                UPDATE resources 
                SET like_count = GREATEST(like_count - 1, 0)
                WHERE id = %s
            """, (resource_id,))
            
            liked = False
        else:
            # Like - add the like
            cursor.execute("""
                INSERT INTO resource_likes (user_id, resource_id, created_at)
                VALUES (%s, %s, %s)
            """, (current_user.id, resource_id, get_beijing_now()))
            
            # Increase like count
            cursor.execute("""
                UPDATE resources 
                SET like_count = like_count + 1
                WHERE id = %s
            """, (resource_id,))
            
            liked = True
        
        # Get updated like count
        cursor.execute("SELECT like_count FROM resources WHERE id = %s", (resource_id,))
        result = cursor.fetchone()
        like_count = result['like_count'] if result else 0
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'liked': liked,
            'like_count': like_count
        })
        
    except Exception as e:
        print(f"Error toggling like: {e}")
        return jsonify({'success': False, 'error': 'Failed to update like status'}), 500

# Download functionality removed as requested by user

@app.route('/get_resource_status/<int:resource_id>')


def get_resource_status(resource_id):
    """Get user's interaction status with a resource (liked, downloaded, etc.)"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Check if user has liked this resource
        cursor.execute("""
            SELECT id FROM resource_likes 
            WHERE user_id = %s AND resource_id = %s
        """, (current_user.id, resource_id))
        
        liked = cursor.fetchone() is not None
        
        # Get resource stats (download functionality removed)
        cursor.execute("""
            SELECT view_count, like_count 
            FROM resources WHERE id = %s
        """, (resource_id,))
        
        stats = cursor.fetchone()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'liked': liked,
            'view_count': stats['view_count'] if stats else 0,
            'like_count': stats['like_count'] if stats else 0
        })
        
    except Exception as e:
        print(f"Error getting resource status: {e}")
        return jsonify({'success': False, 'error': 'Failed to get resource status'}), 500

# Admin Forum Management Routes
@app.route('/admin/forum')
@admin_required
def admin_forum_management():
    """Admin forum management page"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get pending posts
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content, fp.category, fp.created_at,
                   u.username as author_name
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            WHERE fp.approval_status = 'pending'
            ORDER BY fp.created_at DESC
        """)
        pending_posts = cursor.fetchall()
        
        # Get approved posts
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content, fp.category, fp.view_count, fp.created_at,
                   u.username as author_name
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            WHERE fp.approval_status = 'approved'
            ORDER BY fp.created_at DESC
            LIMIT 20
        """)
        approved_posts = cursor.fetchall()
        
        # Get rejected posts
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content, fp.category, fp.created_at,
                   fp.rejection_reason, u.username as author_name
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            WHERE fp.approval_status = 'rejected'
            ORDER BY fp.created_at DESC
            LIMIT 20
        """)
        rejected_posts = cursor.fetchall()
        
        # Get statistics
        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN approval_status = 'pending' THEN 1 END) as pending,
                COUNT(CASE WHEN approval_status = 'approved' THEN 1 END) as approved,
                COUNT(CASE WHEN approval_status = 'rejected' THEN 1 END) as rejected,
                COUNT(*) as total
            FROM forum_posts
        """)
        stats = cursor.fetchone()
        
        cursor.close()
        connection.close()
        
        return render_template('admin/forum_management.html',
                             pending_posts=pending_posts,
                             approved_posts=approved_posts,
                             rejected_posts=rejected_posts,
                             stats=stats)
        
    except Exception as e:
        print(f"Error loading forum management: {e}")
        flash('Error loading forum management data', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/forum/approve/<int:post_id>', methods=['POST'])
@admin_required
def admin_approve_forum_post(post_id):
    """Approve a forum post"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Update post approval status
        cursor.execute("""
            UPDATE forum_posts 
            SET approval_status = 'approved', 
                reviewed_by = %s, 
                reviewed_at = %s,
                rejection_reason = NULL
            WHERE id = %s
        """, (current_user.id, get_beijing_now(), post_id))
        
        # Log the review action
        cursor.execute("""
            INSERT INTO forum_post_reviews (post_id, reviewer_id, old_status, new_status, reviewed_at)
            VALUES (%s, %s, 'pending', 'approved', %s)
        """, (post_id, current_user.id, get_beijing_now()))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Post approved successfully'})
        
    except Exception as e:
        print(f"Error approving forum post: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/forum/reject/<int:post_id>', methods=['POST'])
@admin_required
def admin_reject_forum_post(post_id):
    """Reject a forum post"""
    try:
        data = request.get_json()
        reason = data.get('reason', '').strip()
        
        if not reason:
            return jsonify({'success': False, 'error': 'Rejection reason is required'}), 400
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Update post approval status
        cursor.execute("""
            UPDATE forum_posts 
            SET approval_status = 'rejected', 
                reviewed_by = %s, 
                reviewed_at = %s,
                rejection_reason = %s
            WHERE id = %s
        """, (current_user.id, get_beijing_now(), reason, post_id))
        
        # Log the review action
        cursor.execute("""
            INSERT INTO forum_post_reviews (post_id, reviewer_id, old_status, new_status, comment, reviewed_at)
            VALUES (%s, %s, 'pending', 'rejected', %s, %s)
        """, (post_id, current_user.id, reason, get_beijing_now()))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Post rejected successfully'})
        
    except Exception as e:
        print(f"Error rejecting forum post: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/forum/delete/<int:post_id>', methods=['DELETE'])
@admin_required
def admin_delete_forum_post(post_id):
    """Delete a forum post permanently with image cleanup"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # First, get the post data to retrieve image paths
        cursor.execute("""
            SELECT cover_image 
            FROM forum_posts 
            WHERE id = %s
        """, (post_id,))
        
        post_data = cursor.fetchone()
        if not post_data:
            cursor.close()
            connection.close()
            return jsonify({'success': False, 'error': 'Post not found'}), 404
        
        # Skip image cleanup for faster deletion - images will be cleaned up by background job
        # cleanup_results = cleanup_post_images(
        #     post_data.get('cover_image'), 
        #     None  # No additional_images field in forum posts
        # )
        
        print(f"🗑️ Forum帖子 {post_id} 快速删除 - 跳过图片清理")
        
        # Get attachment file paths before deleting from database
        cursor.execute("SELECT file_path FROM forum_attachments WHERE post_id = %s", (post_id,))
        attachment_files = cursor.fetchall()
        
        # Delete attachment files from filesystem
        deleted_files = 0
        static_dir = os.path.join(os.getcwd(), 'static')  # Define static_dir
        
        for attachment in attachment_files:
            file_path = attachment['file_path']
            if file_path:
                # Handle both relative and absolute paths
                if not file_path.startswith('/'):
                    full_path = os.path.join(static_dir, file_path)
                else:
                    full_path = file_path
                
                try:
                    if os.path.exists(full_path):
                        os.remove(full_path)
                        deleted_files += 1
                        print(f"✅ 删除附件文件: {full_path}")
                    else:
                        print(f"⚠️ 附件文件不存在: {full_path}")
                except Exception as e:
                    print(f"❌ 删除附件文件失败: {full_path} - {e}")
        
        print(f"🗂️ 删除了 {deleted_files} 个附件文件")
        
        # Delete all attachments for this post from database
        cursor.execute("DELETE FROM forum_attachments WHERE post_id = %s", (post_id,))
        
        # Delete all replies/comments for this post
        cursor.execute("DELETE FROM forum_replies WHERE post_id = %s", (post_id,))
        
        # Then delete the post itself
        cursor.execute("DELETE FROM forum_posts WHERE id = %s", (post_id,))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        # Prepare response message
        message = 'Question deleted successfully'
        
        return jsonify({
            'success': True, 
            'message': message
        })
        
    except Exception as e:
        print(f"Error deleting forum post: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/forum/post/<int:post_id>')
@admin_required
def admin_forum_post_detail(post_id):
    """Admin view of forum post details with comments"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get post details (including unapproved posts)
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content, fp.category, fp.view_count, fp.reply_count,
                   fp.created_at, fp.cover_image, fp.additional_images, fp.approval_status,
                   fp.rejection_reason, fp.reviewed_at,
                   u.username as author_name, u.user_role, u.created_at as author_joined,
                   reviewer.username as reviewer_name
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            LEFT JOIN users reviewer ON fp.reviewed_by = reviewer.id
            WHERE fp.id = %s
        """, (post_id,))
        
        post_data = cursor.fetchone()
        
        if not post_data:
            flash('Post not found', 'error')
            return redirect(url_for('admin_forum_management'))
        
        # Parse additional images if they exist
        additional_images = []
        if post_data.get('additional_images'):
            additional_images = [img.strip() for img in post_data['additional_images'].split(',') if img.strip()]
        
        # Create post object for template
        post = {
            'id': post_data['id'],
            'title': post_data['title'],
            'content': post_data['content'],
            'category': post_data['category'],
            'view_count': post_data['view_count'],
            'reply_count': post_data['reply_count'],
            'created_at': post_data['created_at'],
            'cover_image': post_data.get('cover_image'),
            'additional_images': additional_images,
            'approval_status': post_data['approval_status'],
            'rejection_reason': post_data.get('rejection_reason'),
            'reviewed_at': post_data.get('reviewed_at'),
            'reviewer_name': post_data.get('reviewer_name'),
            'author': {
                'username': post_data['author_name'],
                'user_role': post_data['user_role'],
                'created_at': post_data['author_joined']
            }
        }
        
        # Get comments/replies for this post
        cursor.execute("""
            SELECT fr.id, fr.content, fr.created_at, fr.like_count,
                   u.username, u.user_role
            FROM forum_replies fr
            JOIN users u ON fr.user_id = u.id
            WHERE fr.post_id = %s
            ORDER BY fr.like_count DESC, fr.created_at ASC
        """, (post_id,))
        comments = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        return render_template('admin/forum_post_detail.html', post=post, comments=comments)
        
    except Exception as e:
        print(f"Error loading admin forum post detail: {e}")
        flash('Error loading post details', 'error')
        return redirect(url_for('admin_forum_management'))

@app.route('/admin/forum/delete-comment/<int:comment_id>', methods=['DELETE'])
@admin_required
def admin_delete_comment(comment_id):
    """Delete a forum comment"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Get comment details for logging
        cursor.execute("""
            SELECT fr.post_id, fr.user_id, fr.content
            FROM forum_replies fr
            WHERE fr.id = %s
        """, (comment_id,))
        
        comment_data = cursor.fetchone()
        if not comment_data:
            return jsonify({'success': False, 'error': 'Comment not found'}), 404
        
        post_id, user_id, content = comment_data
        
        # Log the moderation action
        cursor.execute("""
            INSERT INTO comment_moderation (comment_id, post_id, moderator_id, action, reason, moderated_at)
            VALUES (%s, %s, %s, 'deleted', 'Deleted by administrator', %s)
        """, (comment_id, post_id, current_user.id, get_beijing_now()))
        
        # Delete the comment
        cursor.execute("DELETE FROM forum_replies WHERE id = %s", (comment_id,))
        
        # Update post reply count
        cursor.execute("""
            UPDATE forum_posts SET reply_count = reply_count - 1 WHERE id = %s AND reply_count > 0
        """, (post_id,))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Comment deleted successfully'})
        
    except Exception as e:
        print(f"Error deleting comment: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/statistics')
@admin_required
def admin_statistics():
    """Admin statistics dashboard"""
    try:
        # Track admin statistics page view
        user_id = current_user.id if current_user.is_authenticated else None
        track_page_view('admin_statistics', None, user_id)
        
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Basic counts
        cursor.execute("SELECT COUNT(*) as count FROM users")
        total_users = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM forum_posts")
        total_posts = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM forum_replies")
        total_comments = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM user_feedback")
        total_feedback = cursor.fetchone()['count']
        
        # User registration statistics (last 30 days)
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM users 
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        """)
        user_registrations = cursor.fetchall()
        
        # Forum activity statistics (last 30 days)
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM forum_posts 
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        """)
        forum_activity = cursor.fetchall()
        
        # Top active users (by posts and comments)
        cursor.execute("""
            SELECT u.username, u.user_role,
                   COUNT(DISTINCT fp.id) as post_count,
                   COUNT(DISTINCT fr.id) as comment_count,
                   (COUNT(DISTINCT fp.id) + COUNT(DISTINCT fr.id)) as total_activity
            FROM users u
            LEFT JOIN forum_posts fp ON u.id = fp.user_id
            LEFT JOIN forum_replies fr ON u.id = fr.user_id
            WHERE u.user_role != 'admin'
            GROUP BY u.id, u.username, u.user_role
            HAVING total_activity > 0
            ORDER BY total_activity DESC
            LIMIT 10
        """)
        top_users = cursor.fetchall()
        
        # Category statistics
        cursor.execute("""
            SELECT category, COUNT(*) as count
            FROM forum_posts
            WHERE approval_status = 'approved'
            GROUP BY category
            ORDER BY count DESC
        """)
        category_stats = cursor.fetchall()
        
        # Monthly statistics for the last 12 months
        cursor.execute("""
            SELECT 
                DATE_FORMAT(created_at, '%Y-%m') as month,
                COUNT(DISTINCT CASE WHEN table_name = 'users' THEN id END) as new_users,
                COUNT(DISTINCT CASE WHEN table_name = 'posts' THEN id END) as new_posts,
                COUNT(DISTINCT CASE WHEN table_name = 'comments' THEN id END) as new_comments
            FROM (
                SELECT id, created_at, 'users' as table_name FROM users WHERE created_at >= DATE_SUB(NOW(), INTERVAL 12 MONTH)
                UNION ALL
                SELECT id, created_at, 'posts' as table_name FROM forum_posts WHERE created_at >= DATE_SUB(NOW(), INTERVAL 12 MONTH)
                UNION ALL
                SELECT id, created_at, 'comments' as table_name FROM forum_replies WHERE created_at >= DATE_SUB(NOW(), INTERVAL 12 MONTH)
            ) combined
            GROUP BY month
            ORDER BY month DESC
            LIMIT 12
        """)
        monthly_stats = cursor.fetchall()
        
        # User activity logs statistics (if available)
        cursor.execute("""
            SELECT activity_type, COUNT(*) as count
            FROM user_activity_logs
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY activity_type
            ORDER BY count DESC
        """)
        activity_stats = cursor.fetchall()
        
        # Real page view statistics from page_views table
        cursor.execute("""
            SELECT COUNT(*) as total_views,
                   COUNT(DISTINCT COALESCE(user_id, ip_address)) as unique_visitors,
                   COUNT(DISTINCT page_type) as page_types
            FROM page_views
        """)
        views_result = cursor.fetchone()
        total_views = views_result['total_views'] if views_result['total_views'] else 0
        unique_visitors = views_result['unique_visitors'] if views_result['unique_visitors'] else 0
        
        # Get views by page type
        cursor.execute("""
            SELECT page_type,
                   COUNT(*) as views,
                   COUNT(DISTINCT COALESCE(user_id, ip_address)) as unique_viewers
            FROM page_views
            GROUP BY page_type
            ORDER BY views DESC
        """)
        page_type_views = cursor.fetchall()
        
        # Get today's statistics
        cursor.execute("""
            SELECT COUNT(*) as today_views,
                   COUNT(DISTINCT COALESCE(user_id, ip_address)) as today_unique_visitors
            FROM page_views
            WHERE DATE(created_at) = CURDATE()
        """)
        today_stats = cursor.fetchone()
        today_views = today_stats['today_views'] if today_stats['today_views'] else 0
        today_unique = today_stats['today_unique_visitors'] if today_stats['today_unique_visitors'] else 0
        
        stats = {
            'totals': {
                'users': total_users,
                'posts': total_posts,
                'comments': total_comments,
                'feedback': total_feedback,
                'views': total_views,
                'unique_visitors': unique_visitors,
                'today_views': today_views,
                'today_unique': today_unique
            },
            'user_registrations': user_registrations,
            'forum_activity': forum_activity,
            'top_users': top_users,
            'category_stats': category_stats,
            'monthly_stats': monthly_stats,
            'activity_stats': activity_stats,
            'page_type_views': page_type_views
        }
        
        cursor.close()
        connection.close()
        
        return render_template('admin/statistics.html', stats=stats)
        
    except Exception as e:
        print(f"Error loading statistics: {e}")
        flash('Error loading statistics data', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/users')
@admin_required
def admin_user_management():
    """Admin user management page"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get all users with their activity stats
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.school, u.student_id, u.user_role, u.registration_status, u.created_at,
                   COUNT(DISTINCT fp.id) as post_count,
                   COUNT(DISTINCT fr.id) as comment_count,
                   MAX(fp.created_at) as last_post,
                   MAX(fr.created_at) as last_comment
            FROM users u
            LEFT JOIN forum_posts fp ON u.id = fp.user_id
            LEFT JOIN forum_replies fr ON u.id = fr.user_id
            GROUP BY u.id, u.username, u.email, u.school, u.student_id, u.user_role, u.registration_status, u.created_at
            ORDER BY u.created_at DESC
        """)
        users = cursor.fetchall()
        
        # Calculate last activity for each user
        for user in users:
            last_activities = []
            if user['last_post']:
                last_activities.append(user['last_post'])
            if user['last_comment']:
                last_activities.append(user['last_comment'])
            
            user['last_activity'] = max(last_activities) if last_activities else None
            user['total_activity'] = (user['post_count'] or 0) + (user['comment_count'] or 0)
        
        # Get user role statistics
        cursor.execute("""
            SELECT user_role, COUNT(*) as count
            FROM users
            GROUP BY user_role
            ORDER BY count DESC
        """)
        role_stats = cursor.fetchall()
        
        # Get registration status statistics
        cursor.execute("""
            SELECT registration_status, COUNT(*) as count
            FROM users
            GROUP BY registration_status
            ORDER BY count DESC
        """)
        status_stats = cursor.fetchall()
        
        stats = {
            'total_users': len(users),
            'role_stats': role_stats,
            'status_stats': status_stats
        }
        
        cursor.close()
        connection.close()
        
        return render_template('admin/user_management.html', users=users, stats=stats)
        
    except Exception as e:
        print(f"Error loading user management: {e}")
        flash('Error loading user management data', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def admin_reset_user_password(user_id):
    """Reset user password"""
    try:
        data = request.get_json()
        new_password = data.get('password', '').strip()
        
        if not new_password or len(new_password) < 6:
            return jsonify({'success': False, 'error': 'Password must be at least 6 characters long'}), 400
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Check if user exists
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        if not user_data:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        username = user_data[0]
        
        # Hash new password
        password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # Update password
        cursor.execute("""
            UPDATE users SET password_hash = %s WHERE id = %s
        """, (password_hash, user_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': f'Password reset successfully for user {username}'})
        
    except Exception as e:
        print(f"Error resetting user password: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/users/<int:user_id>/change-role', methods=['POST'])
@admin_required
def admin_change_user_role(user_id):
    """Change user role"""
    try:
        data = request.get_json()
        new_role = data.get('role', '').strip()
        
        if new_role not in ['student', 'moderator', 'admin']:
            return jsonify({'success': False, 'error': 'Invalid role'}), 400
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Check if user exists
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        if not user_data:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        username = user_data[0]
        
        # Update user role
        cursor.execute("""
            UPDATE users SET user_role = %s WHERE id = %s
        """, (new_role, user_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': f'Role changed to {new_role} for user {username}'})
        
    except Exception as e:
        print(f"Error changing user role: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/users/<int:user_id>/change-status', methods=['POST'])
@admin_required
def admin_change_user_status(user_id):
    """Change user registration status"""
    try:
        data = request.get_json()
        new_status = data.get('status', '').strip()
        
        if new_status not in ['pending', 'approved', 'rejected']:
            return jsonify({'success': False, 'error': 'Invalid status'}), 400
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Check if user exists
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        if not user_data:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        username = user_data[0]
        
        # Update user status
        cursor.execute("""
            UPDATE users SET registration_status = %s WHERE id = %s
        """, (new_status, user_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': f'Status changed to {new_status} for user {username}'})
        
    except Exception as e:
        print(f"Error changing user status: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/feedback')
@admin_required
def admin_feedback_management():
    """Admin feedback management page"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get all feedback with user information
        cursor.execute("""
            SELECT f.id, f.feedback_type, f.title, f.description, f.status, f.priority,
                   f.admin_response, f.created_at, f.updated_at, f.responded_at,
                   u.username as user_name, u.email as user_email,
                   admin.username as admin_name
            FROM user_feedback f
            JOIN users u ON f.user_id = u.id
            LEFT JOIN users admin ON f.responded_by = admin.id
            ORDER BY 
                CASE f.priority 
                    WHEN 'urgent' THEN 1 
                    WHEN 'high' THEN 2 
                    WHEN 'medium' THEN 3 
                    WHEN 'low' THEN 4 
                END,
                f.created_at DESC
        """)
        feedback_list = cursor.fetchall()
        
        # Get feedback statistics
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN status = 'open' THEN 1 END) as open_count,
                COUNT(CASE WHEN status = 'in_progress' THEN 1 END) as in_progress_count,
                COUNT(CASE WHEN status = 'resolved' THEN 1 END) as resolved_count,
                COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_count,
                COUNT(CASE WHEN feedback_type = 'bug' THEN 1 END) as bug_count,
                COUNT(CASE WHEN feedback_type = 'suggestion' THEN 1 END) as suggestion_count,
                COUNT(CASE WHEN feedback_type = 'complaint' THEN 1 END) as complaint_count,
                COUNT(CASE WHEN priority = 'urgent' THEN 1 END) as urgent_count,
                COUNT(CASE WHEN priority = 'high' THEN 1 END) as high_count
            FROM user_feedback
        """)
        stats = cursor.fetchone()
        
        cursor.close()
        connection.close()
        
        return render_template('admin/feedback_management.html', 
                             feedback_list=feedback_list, 
                             stats=stats)
        
    except Exception as e:
        print(f"Error loading feedback management: {e}")
        flash('Error loading feedback data', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/feedback/<int:feedback_id>/respond', methods=['POST'])
@admin_required
def admin_respond_feedback(feedback_id):
    """Respond to user feedback"""
    try:
        data = request.get_json()
        response = data.get('response', '').strip()
        status = data.get('status', 'in_progress').strip()
        
        if not response:
            return jsonify({'success': False, 'error': 'Response cannot be empty'}), 400
        
        if status not in ['open', 'in_progress', 'resolved', 'closed']:
            return jsonify({'success': False, 'error': 'Invalid status'}), 400
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Check if feedback exists
        cursor.execute("SELECT id FROM user_feedback WHERE id = %s", (feedback_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'error': 'Feedback not found'}), 404
        
        # Update feedback with admin response
        cursor.execute("""
            UPDATE user_feedback 
            SET admin_response = %s, 
                status = %s,
                responded_by = %s,
                responded_at = %s,
                updated_at = %s
            WHERE id = %s
        """, (response, status, current_user.id, get_beijing_now(), get_beijing_now(), feedback_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Response added successfully'})
        
    except Exception as e:
        print(f"Error responding to feedback: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/feedback/<int:feedback_id>/status', methods=['POST'])
@admin_required
def admin_change_feedback_status(feedback_id):
    """Change feedback status"""
    try:
        data = request.get_json()
        new_status = data.get('status', '').strip()
        
        if new_status not in ['open', 'in_progress', 'resolved', 'closed']:
            return jsonify({'success': False, 'error': 'Invalid status'}), 400
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Check if feedback exists
        cursor.execute("SELECT id FROM user_feedback WHERE id = %s", (feedback_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'error': 'Feedback not found'}), 404
        
        # Update feedback status
        cursor.execute("""
            UPDATE user_feedback 
            SET status = %s, updated_at = %s
            WHERE id = %s
        """, (new_status, get_beijing_now(), feedback_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': f'Status changed to {new_status}'})
        
    except Exception as e:
        print(f"Error changing feedback status: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/feedback', methods=['GET', 'POST'])


def user_feedback():
    """User feedback submission page"""
    if request.method == 'POST':
        try:
            feedback_type = request.form.get('feedback_type', '').strip()
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            priority = request.form.get('priority', 'medium').strip()
            
            # Validate input
            if not feedback_type or feedback_type not in ['bug', 'suggestion', 'complaint', 'other']:
                flash('Please select a valid feedback type', 'error')
                return render_template('feedback.html')
            
            if not title or len(title) < 5:
                flash('Title must be at least 5 characters long', 'error')
                return render_template('feedback.html')
            
            if not description or len(description) < 10:
                flash('Description must be at least 10 characters long', 'error')
                return render_template('feedback.html')
            
            if priority not in ['low', 'medium', 'high', 'urgent']:
                priority = 'medium'
            
            connection = get_db_connection()
            cursor = connection.cursor()
            
            # Insert feedback
            cursor.execute("""
                INSERT INTO user_feedback (user_id, feedback_type, title, description, priority, status, created_at)
                VALUES (%s, %s, %s, %s, %s, 'open', %s)
            """, (current_user.id, feedback_type, title, description, priority, get_beijing_now()))
            
            connection.commit()
            cursor.close()
            connection.close()
            
            flash('Thank you for your feedback! We will review it and respond as soon as possible.', 'success')
            return redirect(url_for('user_feedback'))
            
        except Exception as e:
            print(f"Error submitting feedback: {e}")
            flash('Error submitting feedback. Please try again.', 'error')
    
    return render_template('feedback.html')

# Category routes (books, homework, experience, activities, questions)
@app.route('/category/<category_name>')
def category_resources(category_name):
    """Display resources by category (books, homework, experience, activities, questions)"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Valid categories from database enum
        valid_categories = ['books', 'homework', 'experience', 'activities', 'questions']
        
        if category_name not in valid_categories:
            flash('Category not found', 'error')
            return redirect(url_for('dashboard'))
        
        # Query from forum_posts table by category
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content as description, fp.category, 
                   fp.status, fp.view_count, u.username as author_name, 
                   fp.created_at, fp.updated_at, fp.education_level
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            WHERE fp.category = %s AND fp.status = 'active' AND fp.approval_status = 'approved'
            ORDER BY fp.view_count DESC, fp.created_at DESC
        """, (category_name,))
        
        resources = cursor.fetchall()
        cursor.close()
        connection.close()
        
        # Create display title
        category_titles = {
            'books': 'Books & Reading Materials',
            'homework': 'Homework Help & Solutions', 
            'experience': 'Study Experiences & Tips',
            'activities': 'Activities & Projects',
            'questions': 'Questions & Discussions'
        }
        
        page_title = category_titles.get(category_name, category_name.title())
        
        return render_template('subjects_category.html', 
                             resources=resources,
                             category_name=category_name,
                             page_title=page_title,
                             category=category_name,
                             category_title=page_title)
        
    except Exception as e:
        print(f"Error loading category resources: {e}")
        flash('Error loading category resources', 'error')
        return redirect(url_for('dashboard'))

# Education level routes (IGCSE, A-Level)
@app.route('/education/<education_type>')
def education_resources(education_type):
    """Display resources by education type (IGCSE, A-Level)"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Map education types to education levels
        education_level_mapping = {
            'igcse': 'igcse',
            'alevel': 'alevel',
            'university': 'university',
            'other': 'other'
        }
        
        if education_type not in education_level_mapping:
            flash('Education type not found', 'error')
            return redirect(url_for('dashboard'))
        
        education_level = education_level_mapping[education_type]
        
        # Query from forum_posts table with experience category using education_level
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content as description, fp.category, 
                   fp.status, fp.view_count, u.username as author_name, 
                   fp.created_at, fp.updated_at, fp.education_level
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            WHERE fp.category = 'experience' AND fp.status = 'active' 
                  AND fp.education_level = %s
            ORDER BY fp.view_count DESC, fp.created_at DESC
        """, (education_level,))
        
        resources = cursor.fetchall()
        cursor.close()
        connection.close()
        
        return render_template('subjects_category.html', 
                             resources=resources,
                             page_title=f'{education_type.upper()} Education Resources',
                             category=education_type.upper(),
                             category_title=f'{education_type.upper()} Education Resources')
        
    except Exception as e:
        print(f"Error loading education resources: {e}")
        flash('Error loading education resources', 'error')
        return redirect(url_for('index'))

@app.route('/competitions/<competition_type>')
def competition_resources(competition_type):
    """Display resources by competition type (BPHO, Physics Bowl, etc.)"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Map competition types to education levels
        competition_mapping = {
            'BPHO': 'bpho',
            'physics-bowl': 'physics_bowl'
        }
        
        if competition_type not in competition_mapping:
            flash('Competition type not found', 'error')
            return redirect(url_for('dashboard'))
        
        education_level = competition_mapping[competition_type]
        competition_display_name = 'BPHO' if competition_type == 'BPHO' else 'Physics Bowl'
        
        # Query from resources table using category
        cursor.execute("""
            SELECT r.id, r.title, r.description, r.category, 
                   r.status, r.view_count, u.username as author_name, 
                   r.created_at, r.updated_at, r.cover_image, r.resource_type
            FROM resources r
            JOIN users u ON r.user_id = u.id
            WHERE r.status = 'active' AND r.category = %s
            ORDER BY r.view_count DESC, r.created_at DESC
        """, (education_level.upper(),))
        
        resources = cursor.fetchall()
        cursor.close()
        connection.close()
        
        return render_template('subjects_category.html', 
                             resources=resources, 
                             competition_type=competition_display_name,
                             page_title=f'{competition_display_name} Competition Resources',
                             category=competition_name.upper(),
                             category_title=f'{competition_display_name} Competition Resources')
        
    except Exception as e:
        print(f"Error loading competition resources: {e}")
        flash('Error loading competition resources', 'error')
        return redirect(url_for('index'))

@app.route('/university')
def university_resources():
    """Display university-related resources"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Query from resources table using category
        cursor.execute("""
            SELECT r.id, r.title, r.description, r.category, 
                   r.status, r.view_count, u.username as author_name, 
                   r.created_at, r.updated_at, r.cover_image, r.resource_type
            FROM resources r
            JOIN users u ON r.user_id = u.id
            WHERE r.status = 'active' AND r.category = 'UNIVERSITY_RESOURCES'
            ORDER BY r.view_count DESC, r.created_at DESC
        """)
        
        resources = cursor.fetchall()
        cursor.close()
        connection.close()
        
        return render_template('subjects_category.html', 
                             resources=resources, 
                             page_title='University Resources',
                             category='UNIVERSITY_RESOURCES',
                             category_title='University Resources')
        
    except Exception as e:
        print(f"Error loading university resources: {e}")
        flash('Error loading university resources', 'error')
        return redirect(url_for('dashboard'))

@app.route('/other')
def other_resources():
    """Display other educational resources"""
    # Redirect to dashboard as 'other' category no longer exists
    flash('Other resources page has been migrated. Please use the specific category pages.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/university/resources')
def all_university_resources():
    """Display all university-related resources"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Search for university-related resources
        university_keywords = ['university', 'college', 'admission', 'application', 'scholarship', 'interview']
        
        # Build query with multiple LIKE conditions for forum_posts
        conditions = []
        params = []
        for keyword in university_keywords:
            conditions.append("(fp.title LIKE %s OR fp.content LIKE %s)")
            params.extend([f'%{keyword}%', f'%{keyword}%'])
        
        query = f"""
            SELECT fp.id, fp.title, fp.content as description, fp.category, 
                   fp.status, fp.view_count, u.username as author_name, 
                   fp.created_at, fp.updated_at
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            WHERE fp.category = 'educational_resource' AND fp.status = 'active' 
                  AND ({' OR '.join(conditions)})
            ORDER BY fp.view_count DESC, fp.created_at DESC
        """
        
        cursor.execute(query, params)
        resources = cursor.fetchall()
        cursor.close()
        connection.close()
        
        return render_template('subjects_category.html', 
                             resources=resources, 
                             university_type='All University',
                             page_title='University Resources',
                             category='UNIVERSITY_RESOURCES',
                             category_title='University Resources')
        
    except Exception as e:
        print(f"Error loading university resources: {e}")
        flash('Error loading university resources', 'error')
        return redirect(url_for('index'))

# Forum routes
@app.route('/forum')
def forum():
    """Forum main page - display posts by category"""
    try:
        # Track page view
        user_id = current_user.id if current_user.is_authenticated else None
        track_page_view('forum', None, user_id)
        
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get forum posts with author information and attachment counts (only approved posts)
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content, fp.category, fp.view_count, fp.reply_count,
                   fp.created_at, u.username as author_name, u.user_role, fp.cover_image, fp.topic,
                   COUNT(fa.id) as attachment_count
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            LEFT JOIN forum_attachments fa ON fp.id = fa.post_id
            WHERE fp.status = 'active' AND fp.approval_status = 'approved'
            GROUP BY fp.id
            ORDER BY fp.created_at DESC
            LIMIT 50
        """)
        
        posts = cursor.fetchall()
        
        # For posts without cover images, get their attachment information
        for post in posts:
            if not post.get('cover_image') and post.get('attachment_count', 0) > 0:
                cursor.execute("""
                    SELECT name, size FROM forum_attachments 
                    WHERE post_id = %s 
                    ORDER BY created_at ASC
                    LIMIT 5
                """, (post['id'],))
                post['attachments'] = cursor.fetchall()
            else:
                post['attachments'] = []
        
        # Get forum statistics (only approved posts)
        cursor.execute("SELECT COUNT(*) as total FROM forum_posts WHERE status = 'active' AND approval_status = 'approved'")
        total_posts = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(DISTINCT user_id) as count FROM forum_posts WHERE status = 'active' AND approval_status = 'approved'")
        active_members = cursor.fetchone()['count']
        
        # Get posts from this week
        cursor.execute("""
            SELECT COUNT(*) as count FROM forum_posts 
            WHERE status = 'active' AND approval_status = 'approved' AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        """)
        this_week = cursor.fetchone()['count']
        
        # Get posts from today
        cursor.execute("""
            SELECT COUNT(*) as count FROM forum_posts 
            WHERE status = 'active' AND approval_status = 'approved' AND DATE(created_at) = CURDATE()
        """)
        today = cursor.fetchone()['count']
        
        # Get category statistics
        cursor.execute("""
            SELECT category, COUNT(*) as count 
            FROM forum_posts 
            WHERE status = 'active' AND approval_status = 'approved'
            GROUP BY category
        """)
        category_stats_raw = cursor.fetchall()
        
        # Create category statistics dictionary
        category_stats = {
            'books': 0,
            'homework': 0,  
            'experience': 0,
            'activities': 0,
            'questions': 0
        }
        
        for stat in category_stats_raw:
            if stat['category'] in category_stats:
                category_stats[stat['category']] = stat['count']
        
        stats = {
            'total_posts': total_posts,
            'active_members': active_members,
            'this_week': this_week,
            'today': today,
            'category_stats': category_stats
        }
        
        cursor.close()
        connection.close()
        
        return render_template('forum.html', posts=posts, stats=stats)
        
    except Exception as e:
        print(f"Error loading forum: {e}")
        flash('Error loading forum data', 'warning')
        # Return template with empty data on error
        return render_template('forum.html', 
                             posts=[], 
                             stats={
                                 'total_posts': 0, 
                                 'active_members': 0, 
                                 'this_week': 0, 
                                 'today': 0,
                                 'category_stats': {
                                     'books': 0, 'homework': 0, 'experience': 0,
                                     'activities': 0, 'questions': 0
                                 }
                             })

@app.route('/forum/new-post', methods=['GET'])


def forum_new_post():
    """Display new post creation form"""
    return render_template('forum_new_post.html')

@app.route('/forum/post/<int:post_id>')
def forum_post_detail(post_id):
    """Display forum post details"""
    try:
        # Track page view for specific forum post
        user_id = current_user.id if current_user.is_authenticated else None
        track_page_view('forum_post', post_id, user_id)
        
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get post details with author information (only approved posts)
        cursor.execute("""
            SELECT fp.id, fp.title, fp.content, fp.category, fp.view_count, fp.reply_count,
                   fp.created_at, fp.cover_image, fp.topic,
                   u.username as author_name, u.user_role, u.created_at as author_joined
            FROM forum_posts fp
            JOIN users u ON fp.user_id = u.id
            WHERE fp.id = %s AND fp.status = 'active' AND fp.approval_status = 'approved'
        """, (post_id,))
        
        post_data = cursor.fetchone()
        
        if not post_data:
            flash('Post not found or has been removed.', 'error')
            return redirect(url_for('forum'))
        
        # Get attachments for this post
        cursor.execute("""
            SELECT id, name, file_path, size FROM forum_attachments 
            WHERE post_id = %s 
            ORDER BY created_at ASC
        """, (post_id,))
        attachments = cursor.fetchall()
        
        # Create post object for template
        post = {
            'id': post_data['id'],
            'title': post_data['title'],
            'content': post_data['content'],
            'category': post_data['category'],
            'topic': post_data.get('topic'),
            'view_count': post_data['view_count'],
            'reply_count': post_data['reply_count'],
            'created_at': post_data['created_at'],
            'cover_image': post_data.get('cover_image'),
            'attachments': attachments,
            'author': {
                'username': post_data['author_name'],
                'user_role': post_data['user_role'],
                'created_at': post_data['author_joined']
            }
        }
        
        # Get comments/replies for this post, ordered by like_count DESC, then created_at ASC
        cursor.execute("""
            SELECT fr.id, fr.content, fr.created_at, fr.like_count,
                   u.username as author_name, u.user_role, u.created_at as author_joined
            FROM forum_replies fr
            JOIN users u ON fr.user_id = u.id
            WHERE fr.post_id = %s
            ORDER BY fr.like_count DESC, fr.created_at ASC
        """, (post_id,))
        
        comments_data = cursor.fetchall()
        comments = []
        
        for comment_data in comments_data:
            # Check if current user has liked this comment (only for authenticated users)
            user_liked = False
            if current_user.is_authenticated:
                cursor.execute("""
                    SELECT id FROM comment_likes 
                    WHERE user_id = %s AND reply_id = %s
                """, (current_user.id, comment_data['id']))
                user_liked = cursor.fetchone() is not None
            
            comments.append({
                'id': comment_data['id'],
                'content': comment_data['content'],
                'created_at': comment_data['created_at'],
                'like_count': comment_data['like_count'] or 0,
                'user_liked': user_liked,
                'author': {
                    'username': comment_data['author_name'],
                    'user_role': comment_data['user_role'],
                    'created_at': comment_data['author_joined']
                }
            })
        
        post['comments'] = comments
        
        # Increment view count
        cursor.execute("UPDATE forum_posts SET view_count = view_count + 1 WHERE id = %s", (post_id,))
        connection.commit()
        
        cursor.close()
        connection.close()
        
        return render_template('forum_post_detail.html', post=post)
        
    except Exception as e:
        print(f"Error loading post details: {e}")
        flash('Error loading post details', 'error')
        return redirect(url_for('forum'))

@app.route('/forum/create-post', methods=['POST'])


def create_forum_post():
    """Create a new forum post with topic and attachments support"""
    try:
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        category = request.form.get('category', '').strip()
        topic = request.form.get('topic', '').strip()
        
        # Validate required fields
        if not title or not content or not category or not topic:
            flash('Please fill in all required fields', 'error')
            return redirect(url_for('forum_new_post'))
        
        if len(title) > 200:
            flash('Title is too long (maximum 200 characters)', 'error')
            return redirect(url_for('forum_new_post'))
            
        if len(topic) > 100:
            flash('Topic is too long (maximum 100 characters)', 'error')
            return redirect(url_for('forum_new_post'))
        
        if len(content) > 5000:
            flash('Content is too long (maximum 5000 characters)', 'error')
            return redirect(url_for('forum_new_post'))
        
        # Validate category
        valid_categories = ['books', 'homework', 'tests', 'notes', 'questions']
        if category not in valid_categories:
            flash('Invalid category selected', 'error')
            return redirect(url_for('forum_new_post'))
        
        # Handle cover image upload (optional)
        cover_image_path = None
        cover_image = request.files.get('cover_image')
        if cover_image and cover_image.filename:
            cover_result = save_forum_image(cover_image, 'cover')
            if cover_result['success']:
                cover_image_path = cover_result['path']
            else:
                flash(f"Cover image error: {cover_result['error']}", 'error')
                return redirect(url_for('forum_new_post'))
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Insert the new post with topic and cover image
        # 为匿名用户使用真实的匿名用户ID：125
        user_id = current_user.id if current_user.is_authenticated else 125
        
        cursor.execute("""
            INSERT INTO forum_posts (user_id, title, content, category, topic, cover_image, view_count, reply_count, created_at, status, approval_status)
            VALUES (%s, %s, %s, %s, %s, %s, 0, 0, %s, 'active', 'approved')
        """, (user_id, title, content, category, topic, cover_image_path, get_beijing_now()))
        
        post_id = cursor.lastrowid
        
        # Handle attachments
        attachment_files = request.files.getlist('attachments')
        for attachment_file in attachment_files:
            if attachment_file and attachment_file.filename:
                attachment_result = save_forum_attachment(attachment_file, post_id)
                if attachment_result['success']:
                    # Insert attachment into database
                    cursor.execute("""
                        INSERT INTO forum_attachments (post_id, name, file_path, size, created_at)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (post_id, attachment_result['original_name'], attachment_result['path'], 
                          attachment_result['size'], get_beijing_now()))
                else:
                    flash(f"Attachment error: {attachment_result['error']}", 'warning')
        
        connection.commit()
        cursor.close()
        connection.close()
        
        flash('Question created successfully and is now live!', 'success')
        return redirect(url_for('forum'))
        
    except Exception as e:
        print(f"Error creating forum post: {e}")
        flash('Error creating question. Please try again.', 'error')
        return redirect(url_for('forum_new_post'))

@app.route('/download/<int:attachment_id>')
def download_attachment(attachment_id):
    """Download forum attachment by ID"""
    try:
        # Get attachment info from database
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT name, file_path FROM forum_attachments WHERE id = %s", (attachment_id,))
        attachment = cursor.fetchone()
        cursor.close()
        connection.close()
        
        if not attachment:
            flash('Attachment not found', 'error')
            return redirect(url_for('forum'))
        
        # Construct full file path based on environment
        file_path_from_db = attachment['file_path']
        
        # Handle both server and local paths
        if file_path_from_db.startswith('image/uploads/'):
            # Server environment - path starts with image/uploads/
            file_path = f"/{file_path_from_db}"
        elif file_path_from_db.startswith('uploads/'):
            # Local environment - path starts with uploads/
            static_dir = os.path.join(os.getcwd(), 'static')
            file_path = os.path.join(static_dir, file_path_from_db)
        else:
            # Fallback to local static directory
            static_dir = os.path.join(os.getcwd(), 'static')
            file_path = os.path.join(static_dir, file_path_from_db)
        
        if not os.path.exists(file_path):
            print(f"⚠️ 附件文件不存在: {file_path}")
            flash('File not found on server', 'error')
            return redirect(url_for('forum'))
        
        return send_file(file_path, as_attachment=True, download_name=attachment['name'])
        
    except Exception as e:
        print(f"Error downloading attachment: {e}")
        flash('Error downloading file', 'error')
        return redirect(url_for('forum'))

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serve uploaded files"""
    uploads_dir = os.path.join(os.getcwd(), 'static', 'uploads')
    return send_from_directory(uploads_dir, filename)

@app.route('/forum/post/<int:post_id>/comment', methods=['POST'])


def add_comment(post_id):
    """Add a new comment to a forum post"""
    try:
        content = request.form.get('content', '').strip()
        
        if not content:
            flash('Comment content cannot be empty', 'error')
            return redirect(url_for('forum_post_detail', post_id=post_id))
        
        if len(content) > 1000:
            flash('Comment is too long (maximum 1000 characters)', 'error')
            return redirect(url_for('forum_post_detail', post_id=post_id))
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Check if post exists
        cursor.execute("SELECT id FROM forum_posts WHERE id = %s AND status = 'active'", (post_id,))
        if not cursor.fetchone():
            flash('Post not found', 'error')
            return redirect(url_for('forum'))
        
        # Insert the new comment
        # 为匿名用户使用真实的匿名用户ID：125
        user_id = current_user.id if current_user.is_authenticated else 125
        
        cursor.execute("""
            INSERT INTO forum_replies (post_id, user_id, content, created_at)
            VALUES (%s, %s, %s, %s)
        """, (post_id, user_id, content, get_beijing_now()))
        
        # Update reply count for the post
        cursor.execute("""
            UPDATE forum_posts SET reply_count = reply_count + 1 WHERE id = %s
        """, (post_id,))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        flash('Comment posted successfully!', 'success')
        return redirect(url_for('forum_post_detail', post_id=post_id))
        
    except Exception as e:
        print(f"Error adding comment: {e}")
        flash('Error posting comment. Please try again.', 'error')
        return redirect(url_for('forum_post_detail', post_id=post_id))

@app.route('/forum/comment/<int:comment_id>/like', methods=['POST'])


def toggle_comment_like(comment_id):
    """Toggle like for a comment"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Check if comment exists
        cursor.execute("SELECT id, post_id FROM forum_replies WHERE id = %s", (comment_id,))
        comment = cursor.fetchone()
        if not comment:
            return jsonify({'success': False, 'error': 'Comment not found'}), 404
        
        # Check if user already liked this comment
        cursor.execute("""
            SELECT id FROM comment_likes 
            WHERE user_id = %s AND reply_id = %s
        """, (current_user.id, comment_id))
        
        existing_like = cursor.fetchone()
        
        if existing_like:
            # Unlike - remove the like
            cursor.execute("""
                DELETE FROM comment_likes 
                WHERE user_id = %s AND reply_id = %s
            """, (current_user.id, comment_id))
            liked = False
        else:
            # Like - add the like
            cursor.execute("""
                INSERT INTO comment_likes (user_id, reply_id) 
                VALUES (%s, %s)
            """, (current_user.id, comment_id))
            liked = True
        
        # Update like count in forum_replies
        cursor.execute("""
            UPDATE forum_replies 
            SET like_count = (SELECT COUNT(*) FROM comment_likes WHERE reply_id = %s)
            WHERE id = %s
        """, (comment_id, comment_id))
        
        # Get updated like count
        cursor.execute("SELECT like_count FROM forum_replies WHERE id = %s", (comment_id,))
        like_count = cursor.fetchone()['like_count']
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True, 
            'liked': liked, 
            'like_count': like_count
        })
        
    except Exception as e:
        print(f"Error toggling comment like: {e}")
        return jsonify({'success': False, 'error': 'Failed to toggle like'}), 500

@app.route('/image/<path:filename>')
def serve_image(filename):
    """Serve images from the /image directory with fallback for old paths"""
    try:
        # 减少调试日志，提高性能
        # print(f"🖼️ 图片请求: {filename}")
        
        # 首先尝试直接访问（新格式）
        primary_path = f"/image/{filename}"
        
        if os.path.exists(primary_path):
            # 直接发送文件，添加缓存头
            response = send_from_directory('/image', filename)
            # 设置强缓存，避免重复请求
            response.headers['Cache-Control'] = 'public, max-age=2592000, immutable'  # 30天
            response.headers['ETag'] = f'"{filename}"'
            return response
        
        # 如果文件不存在，尝试旧格式的回退路径
        # 对于旧数据，路径可能缺少 forum_images/ 前缀
        if not filename.startswith('forum_images/') and not filename.startswith('resources/'):
            # 尝试添加 forum_images/ 前缀（论坛图片）
            fallback_filename = f"forum_images/{filename}"
            fallback_path = f"/image/{fallback_filename}"
            
            if os.path.exists(fallback_path):
                response = send_from_directory('/image', fallback_filename)
                response.headers['Cache-Control'] = 'public, max-age=2592000, immutable'
                response.headers['ETag'] = f'"{fallback_filename}"'
                return response
        
        # 对于resources路径，也尝试旧格式回退
        if filename.startswith('resources/') and '/' in filename[10:]:  # resources/xxx 但不是 resources/2025/08/xxx
            # 检查是否是新格式路径但文件不存在，尝试旧格式
            old_filename_part = filename.split('/')[-1]  # 获取最后的文件名部分
            old_format_path = f"/image/resources/{old_filename_part}"
            
            if os.path.exists(old_format_path):
                response = send_from_directory('/image', f"resources/{old_filename_part}")
                response.headers['Cache-Control'] = 'public, max-age=2592000, immutable'
                response.headers['ETag'] = f'"resources/{old_filename_part}"'
                return response
        
        # 所有路径都不存在，快速返回404
        print(f"❌ 图片文件不存在: {filename}")
        return "Image not found", 404
        
    except Exception as e:
        print(f"❌ 图片服务错误: {e}")
        return "Image service error", 404

@app.route('/test-image-speed/<path:filename>')
def test_image_speed(filename):
    """测试图片加载速度的简化版本"""
    import time
    start_time = time.time()
    
    try:
        primary_path = f"/image/{filename}"
        
        if os.path.exists(primary_path):
            load_time = time.time() - start_time
            print(f"⚡ 图片加载耗时: {load_time:.3f}秒 - {filename}")
            
            response = send_from_directory('/image', filename)
            response.headers['Cache-Control'] = 'public, max-age=2592000, immutable'
            return response
        else:
            return "Image not found", 404
            
    except Exception as e:
        load_time = time.time() - start_time
        print(f"❌ 图片加载失败，耗时: {load_time:.3f}秒 - {e}")
        return "Error", 500

@app.route('/debug/images')
def debug_images():
    """调试路由：显示图片目录信息"""
    try:
        import os
        debug_info = {
            'image_dir_exists': os.path.exists('/image'),
            'forum_images_exists': os.path.exists('/image/forum_images'),
            'resources_exists': os.path.exists('/image/resources'),
            'current_working_dir': os.getcwd(),
            'current_user': os.getenv('USER', 'unknown'),
            'uid_gid': f"UID:{os.getuid()} GID:{os.getgid()}" if hasattr(os, 'getuid') else 'Windows'
        }
        
        # 检查权限
        if os.path.exists('/image'):
            try:
                debug_info['image_permissions'] = oct(os.stat('/image').st_mode)[-3:]
                debug_info['image_writable'] = os.access('/image', os.W_OK)
                debug_info['image_contents'] = os.listdir('/image')
            except Exception as e:
                debug_info['image_error'] = str(e)
        
        if os.path.exists('/image/forum_images'):
            try:
                debug_info['forum_images_contents'] = []
                for root, dirs, files in os.walk('/image/forum_images'):
                    for file in files:
                        rel_path = os.path.relpath(os.path.join(root, file), '/image')
                        debug_info['forum_images_contents'].append(rel_path)
            except Exception as e:
                debug_info['forum_images_error'] = str(e)
        
        return f"<pre>{str(debug_info)}</pre>"
    except Exception as e:
        return f"调试错误: {e}", 500

@app.route('/debug/test-delete')
@admin_required
def debug_test_delete():
    """测试文件删除功能"""
    try:
        test_path = request.args.get('path', 'forum_images/test/nonexistent.jpg')
        
        print(f"\n🧪 开始删除测试 - 路径: {test_path}")
        test_result = delete_image_file(test_path)
        print(f"🧪 删除测试结果: {test_result}")
        
        return f"<pre>测试删除结果: {test_result}\n路径: {test_path}\n\n请查看服务器日志获取详细信息</pre>"
    except Exception as e:
        return f"测试删除错误: {e}", 500

@app.route('/debug/test-delete-real')
@admin_required
def debug_test_delete_real():
    """测试删除实际存在的文件"""
    try:
        # 检查是否有实际的图片文件可以测试
        import glob
        
        possible_test_paths = []
        base_paths = ['/image/forum_images', '/image/resources']
        
        for base_path in base_paths:
            if os.path.exists(base_path):
                for root, dirs, files in os.walk(base_path):
                    for file in files[:3]:  # 只取前3个文件进行测试
                        relative_path = os.path.relpath(os.path.join(root, file), '/image')
                        possible_test_paths.append(relative_path)
        
        if not possible_test_paths:
            return "<pre>没有找到可测试的图片文件</pre>"
        
        # 只显示可用的测试路径，不实际删除
        test_links = []
        for path in possible_test_paths[:5]:  # 只显示前5个
            test_links.append(f'<a href="/debug/test-delete?path={path}">测试删除: {path}</a>')
        
        return f"""<pre>可测试的图片文件:

{chr(10).join(test_links)}

⚠️ 警告: 点击链接将真实删除文件，请谨慎操作！</pre>"""
        
    except Exception as e:
        return f"查找测试文件错误: {e}", 500

# Admin API routes for dashboard quick actions
@app.route('/admin/api/approve_user/<int:user_id>', methods=['POST'])
@admin_required
def admin_api_approve_user(user_id):
    """API endpoint to approve a user from dashboard"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Update user registration status to approved
        cursor.execute("""
            UPDATE users 
            SET registration_status = 'approved', updated_at = %s 
            WHERE id = %s
        """, (datetime.now(), user_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'User approved successfully. User may need to re-login to access full features.'})
        
    except Exception as e:
        print(f"Error approving user: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/api/reject_user/<int:user_id>', methods=['POST'])
@admin_required
def admin_api_reject_user(user_id):
    """API endpoint to reject a user from dashboard"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Update user registration status to rejected
        cursor.execute("""
            UPDATE users 
            SET registration_status = 'rejected', updated_at = %s 
            WHERE id = %s
        """, (datetime.now(), user_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'User rejected successfully'})
        
    except Exception as e:
        print(f"Error rejecting user: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/api/approve_resource/<int:resource_id>', methods=['POST'])
@admin_required
def admin_api_approve_resource(resource_id):
    """API endpoint to approve a resource from dashboard"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Update resource status to active
        cursor.execute("""
            UPDATE resources 
            SET status = 'active', updated_at = %s 
            WHERE id = %s
        """, (datetime.now(), resource_id))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Resource approved successfully'})
        
    except Exception as e:
        print(f"Error approving resource: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/admin/api/reject_resource/<int:resource_id>', methods=['POST'])
@admin_required
def admin_api_reject_resource(resource_id):
    """API endpoint to reject a resource from dashboard"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Get resource data for cleanup before deletion
        cursor.execute("""
            SELECT cover_image, additional_images 
            FROM resources 
            WHERE id = %s
        """, (resource_id,))
        
        resource_data = cursor.fetchone()
        if resource_data:
            # Clean up images
            cleanup_results = cleanup_post_images(resource_data[0], resource_data[1])
            print(f"Resource {resource_id} image cleanup results: {cleanup_results}")
        
        # Delete the resource
        cursor.execute("DELETE FROM resources WHERE id = %s", (resource_id,))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({'success': True, 'message': 'Resource rejected and deleted successfully'})
        
    except Exception as e:
        print(f"Error rejecting resource: {e}")
        return jsonify({'success': False, 'error': 'Database error'}), 500

@app.route('/submit-resource')
def submit_resource_page():
    """Show the resource submission form"""
    print("DEBUG: submit_resource_page() called")
    print(f"DEBUG: current_user.is_authenticated = {current_user.is_authenticated if 'current_user' in globals() else 'N/A'}")
    return render_template('submit_resource.html')

@app.route('/submit-resource', methods=['POST'])
def submit_resource():
    """Handle user resource submission"""
    try:
        # Get form data
        title = request.form.get('title', '').strip()
        subject = request.form.get('subject', '').strip()
        education_level = request.form.get('education_level', '').strip()
        resource_type = request.form.get('resource_type', 'notes')
        difficulty_level = request.form.get('difficulty_level', 'intermediate')
        description = request.form.get('description', '').strip()
        content = request.form.get('content', '').strip()
        cover_image_url = request.form.get('cover_image_url', '').strip()
        
        # Validate required fields
        if not title or not subject or not education_level or not content:
            flash('Please fill in all required fields', 'error')
            return redirect(url_for('submit_resource_page'))
        
        # Validate field lengths
        if len(title) > 200:
            flash('Title is too long (maximum 200 characters)', 'error')
            return redirect(url_for('submit_resource_page'))
            
        if len(description) > 500:
            flash('Description is too long (maximum 500 characters)', 'error')
            return redirect(url_for('submit_resource_page'))
        
        if len(content) > 10000:
            flash('Content is too long (maximum 10000 characters)', 'error')
            return redirect(url_for('submit_resource_page'))
        
        # Validate subject and education level
        valid_subjects = ['math', 'physics', 'chemistry', 'biology']
        valid_education_levels = ['igcse', 'alevel', 'ap', 'competition', 'university']
        valid_resource_types = ['books', 'homework', 'tests', 'notes', 'reference', 'past_paper']
        valid_difficulty_levels = ['beginner', 'intermediate', 'advanced']
        
        if subject not in valid_subjects:
            flash('Invalid subject selected', 'error')
            return redirect(url_for('submit_resource_page'))
            
        if education_level not in valid_education_levels:
            flash('Invalid education level selected', 'error')
            return redirect(url_for('submit_resource_page'))
            
        if resource_type not in valid_resource_types:
            flash('Invalid resource type selected', 'error')
            return redirect(url_for('submit_resource_page'))
            
        if difficulty_level not in valid_difficulty_levels:
            flash('Invalid difficulty level selected', 'error')
            return redirect(url_for('submit_resource_page'))

        # Handle cover image upload (optional)
        cover_image_path = None
        cover_image = request.files.get('cover_image')
        if cover_image and cover_image.filename:
            # Use the same image saving function as forum posts
            cover_result = save_forum_image(cover_image, 'resource_cover')
            if cover_result['success']:
                cover_image_path = cover_result['path']
            else:
                flash(f"Cover image error: {cover_result['error']}", 'error')
                return redirect(url_for('submit_resource_page'))
        elif cover_image_url:
            # If URL is provided, use it
            cover_image_path = cover_image_url
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Map the form fields to the database structure
        # Convert education_level to category for database compatibility
        category_mapping = {
            'igcse': 'IGCSE',
            'alevel': 'A-LEVEL',
            'ap': 'AP',
            'competition': 'BPHO',  # Using BPHO as competition category
            'university': 'UNIVERSITY_RESOURCES'
        }
        
        db_category = category_mapping.get(education_level, 'IGCSE')
        
        # 映射resource_type到数据库期望的值
        resource_type_mapping = {
            'books': 'reference',
            'homework': 'notes', 
            'tests': 'past_paper',
            'notes': 'notes',
            'reference': 'reference',
            'past_paper': 'past_paper'
        }
        db_resource_type = resource_type_mapping.get(resource_type, 'notes')
        
        # Insert the resource with 'active' status for direct publication
        # 为匿名用户使用真实的匿名用户ID：125
        user_id = current_user.id if current_user.is_authenticated else 125
        
        cursor.execute("""
            INSERT INTO resources (
                user_id, title, description, subject, category, resource_type, difficulty_level, 
                content, cover_image, status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
        """, (
            user_id, title, description, subject, db_category, db_resource_type, difficulty_level,
            content, cover_image_path, get_beijing_now()
        ))
        
        resource_id = cursor.lastrowid
        
        # Handle additional images (optional)
        additional_images = request.files.getlist('additional_images')
        image_count = 0
        for additional_image in additional_images:
            if additional_image and additional_image.filename and image_count < 3:
                image_result = save_forum_image(additional_image, f'resource_additional_{image_count}')
                if image_result['success']:
                    # Insert additional image info into resource_images table (if it exists)
                    try:
                        cursor.execute("""
                            INSERT INTO resource_images (resource_id, image_path, image_order, created_at)
                            VALUES (%s, %s, %s, %s)
                        """, (resource_id, image_result['path'], image_count + 1, get_beijing_now()))
                    except Exception as img_e:
                        print(f"Note: Could not save additional image to resource_images table: {img_e}")
                        # This is not critical, continue processing
                    image_count += 1
                else:
                    flash(f"Additional image error: {image_result['error']}", 'warning')
        
        connection.commit()
        cursor.close()
        connection.close()
        
        flash('Resource submitted and published successfully!', 'success')
        return redirect(url_for('subjects_overview'))
        
    except Exception as e:
        print(f"Error submitting resource: {e}")
        flash('Error submitting resource. Please try again.', 'error')
        return redirect(url_for('submit_resource_page'))

# Test route for JavaScript
@app.route('/test-js')
def test_js():
    return render_template('test_js.html')

if __name__ == '__main__':
    import os
    
    # 确保图片目录存在
    os.makedirs('/image/forum_images', exist_ok=True)
    os.makedirs('/image/resources', exist_ok=True)
    
    # Zeabur部署配置
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)