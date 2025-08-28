"""
Forum Image Upload Handler
Handles image upload for forum posts including cover images and additional images
"""

import os
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from PIL import Image
import hashlib

class ForumImageHandler:
    def __init__(self, upload_folder='/image/forum_images'):
        self.upload_folder = upload_folder
        self.allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        self.max_file_size = 5 * 1024 * 1024  # 5MB
        self.cover_image_size = (800, 600)  # Max dimensions for cover images
        self.additional_image_size = (600, 450)  # Max dimensions for additional images
        
        # Ensure upload directory exists
        os.makedirs(upload_folder, exist_ok=True)
    
    def allowed_file(self, filename):
        """Check if file has allowed extension"""
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in self.allowed_extensions
    
    def get_file_hash(self, file_path):
        """Generate MD5 hash for file to detect duplicates"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def optimize_image(self, image_path, max_size, quality=85):
        """Optimize image size and quality"""
        try:
            with Image.open(image_path) as img:
                # Convert RGBA to RGB if needed
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = background
                
                # Resize if needed
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                
                # Save optimized image
                img.save(image_path, 'JPEG', quality=quality, optimize=True)
                
        except Exception as e:
            print(f"Error optimizing image {image_path}: {e}")
            return False
        return True
    
    def save_forum_image(self, file, image_type='cover', post_id=None):
        """
        Save forum image to local storage
        
        Args:
            file: FileStorage object from Flask request
            image_type: 'cover' or 'additional'
            post_id: Optional post ID for organizing files
        
        Returns:
            dict: {'success': bool, 'filename': str, 'path': str, 'error': str}
        """
        if not file or file.filename == '':
            return {'success': False, 'error': 'No file provided'}
        
        if not self.allowed_file(file.filename):
            return {'success': False, 'error': 'File type not allowed'}
        
        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)  # Reset file pointer
        
        if file_size > self.max_file_size:
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
            save_folder = os.path.join(self.upload_folder, date_folder)
            os.makedirs(save_folder, exist_ok=True)
            
            # Full file path
            file_path = os.path.join(save_folder, filename)
            
            # Save file
            file.save(file_path)
            
            # Optimize image based on type
            if image_type == 'cover':
                self.optimize_image(file_path, self.cover_image_size)
            else:
                self.optimize_image(file_path, self.additional_image_size)
            
            # Return relative path for database storage
            relative_path = file_path.replace('static/', '/')
            
            return {
                'success': True,
                'filename': filename,
                'path': relative_path,
                'size': os.path.getsize(file_path),
                'hash': self.get_file_hash(file_path)
            }
            
        except Exception as e:
            return {'success': False, 'error': f'Failed to save file: {str(e)}'}
    
    def delete_image(self, image_path):
        """Delete image file from storage"""
        try:
            full_path = os.path.join('static', image_path.lstrip('/'))
            if os.path.exists(full_path):
                os.remove(full_path)
                return True
        except Exception as e:
            print(f"Error deleting image {image_path}: {e}")
        return False
    
    def cleanup_orphaned_images(self, valid_image_paths):
        """Remove images that are no longer referenced in database"""
        try:
            for root, dirs, files in os.walk(self.upload_folder):
                for file in files:
                    if file == '.gitkeep':
                        continue
                    
                    file_path = os.path.join(root, file)
                    relative_path = file_path.replace('static/', '/')
                    
                    if relative_path not in valid_image_paths:
                        # File not in database, remove it
                        os.remove(file_path)
                        print(f"Removed orphaned image: {relative_path}")
        except Exception as e:
            print(f"Error during cleanup: {e}")

# Example usage in Flask routes:
"""
from werkzeug.utils import secure_filename
from image_upload_handler import ForumImageHandler

image_handler = ForumImageHandler()

@app.route('/forum/create-post', methods=['POST'])

def create_forum_post():
    title = request.form.get('title')
    content = request.form.get('content')
    category = request.form.get('category')
    
    # Handle cover image
    cover_image = request.files.get('cover_image')
    cover_result = image_handler.save_forum_image(cover_image, 'cover')
    
    if not cover_result['success']:
        flash(f"Cover image error: {cover_result['error']}", 'error')
        return redirect(url_for('forum_new_post'))
    
    # Handle additional images
    additional_images = request.files.getlist('additional_images')
    additional_image_paths = []
    
    for img_file in additional_images:
        if img_file.filename:  # Only process if file was selected
            result = image_handler.save_forum_image(img_file, 'additional')
            if result['success']:
                additional_image_paths.append(result['path'])
            else:
                flash(f"Additional image error: {result['error']}", 'warning')
    
    # Save to database
    try:
        cursor = mysql.connection.cursor()
        cursor.execute('''
            INSERT INTO forum_posts (user_id, title, content, category, cover_image, additional_images)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (
            current_user.id,
            title,
            content,
            category,
            cover_result['path'],
            ','.join(additional_image_paths) if additional_image_paths else None
        ))
        mysql.connection.commit()
        cursor.close()
        
        flash('Post created successfully!', 'success')
        return redirect(url_for('forum'))
        
    except Exception as e:
        # Cleanup uploaded images if database save fails
        image_handler.delete_image(cover_result['path'])
        for img_path in additional_image_paths:
            image_handler.delete_image(img_path)
        
        flash('Error creating post. Please try again.', 'error')
        return redirect(url_for('forum_new_post'))

@app.route('/forum/new-post')

def forum_new_post():
    return render_template('forum_new_post.html')

@app.route('/forum/post/<int:post_id>')
def forum_post_detail(post_id):
    cursor = mysql.connection.cursor()
    cursor.execute('''
        SELECT fp.*, u.username, u.user_role, u.created_at as user_created
        FROM forum_posts fp
        JOIN users u ON fp.user_id = u.id
        WHERE fp.id = %s
    ''', (post_id,))
    
    post_data = cursor.fetchone()
    if not post_data:
        flash('Post not found.', 'error')
        return redirect(url_for('forum'))
    
    # Parse additional images
    additional_images = []
    if post_data[6]:  # additional_images field
        additional_images = post_data[6].split(',')
    
    post = {
        'id': post_data[0],
        'title': post_data[2],
        'content': post_data[3],
        'category': post_data[4],
        'cover_image': post_data[5],
        'additional_images': additional_images,
        'created_at': post_data[7],
        'view_count': post_data[8],
        'author': {
            'username': post_data[9],
            'user_role': post_data[10],
            'created_at': post_data[11]
        }
    }
    
    # Increment view count
    cursor.execute('UPDATE forum_posts SET view_count = view_count + 1 WHERE id = %s', (post_id,))
    mysql.connection.commit()
    cursor.close()
    
    return render_template('forum_post_detail.html', post=post)
"""