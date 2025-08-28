#!/usr/bin/env python3
"""
数据库图片路径修复脚本
将旧格式的图片路径更新为新格式
"""
import pymysql
import os
from datetime import datetime

# 数据库配置 - 请根据实际情况修改
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'stem_academic'),
    'charset': 'utf8mb4'
}

def get_db_connection():
    """获取数据库连接"""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        return connection
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return None

def fix_forum_image_paths():
    """修复论坛帖子的图片路径"""
    connection = get_db_connection()
    if not connection:
        return
    
    try:
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # 查询所有有图片的论坛帖子
        cursor.execute("""
            SELECT id, cover_image, additional_images 
            FROM forum_posts 
            WHERE cover_image IS NOT NULL OR additional_images IS NOT NULL
        """)
        
        posts = cursor.fetchall()
        print(f"🔍 找到 {len(posts)} 个包含图片的帖子")
        
        updated_count = 0
        
        for post in posts:
            post_id = post['id']
            cover_image = post['cover_image']
            additional_images = post['additional_images']
            
            updated = False
            new_cover_image = cover_image
            new_additional_images = additional_images
            
            # 处理封面图片路径
            if cover_image and not cover_image.startswith(('http', 'forum_images/', 'resources/')):
                # 旧格式路径，需要添加 forum_images/ 前缀
                if cover_image.startswith('/image/'):
                    # 移除 /image/ 前缀，添加 forum_images/
                    new_cover_image = 'forum_images/' + cover_image[7:]  # 移除 '/image/'
                elif '/' in cover_image:  # 如 '2025/08/filename.jpg'
                    new_cover_image = 'forum_images/' + cover_image
                updated = True
                print(f"📸 帖子 {post_id} 封面图片: {cover_image} -> {new_cover_image}")
            
            # 处理附加图片路径
            if additional_images:
                image_list = [img.strip() for img in additional_images.split(',') if img.strip()]
                new_image_list = []
                
                for img in image_list:
                    if not img.startswith(('http', 'forum_images/', 'resources/')):
                        # 旧格式路径
                        if img.startswith('/image/'):
                            new_img = 'forum_images/' + img[7:]  # 移除 '/image/'
                        elif '/' in img:  # 如 '2025/08/filename.jpg'
                            new_img = 'forum_images/' + img
                        else:
                            new_img = img  # 保持不变
                        new_image_list.append(new_img)
                        print(f"📸 帖子 {post_id} 附加图片: {img} -> {new_img}")
                        updated = True
                    else:
                        new_image_list.append(img)
                
                new_additional_images = ','.join(new_image_list) if new_image_list else None
            
            # 更新数据库
            if updated:
                cursor.execute("""
                    UPDATE forum_posts 
                    SET cover_image = %s, additional_images = %s 
                    WHERE id = %s
                """, (new_cover_image, new_additional_images, post_id))
                updated_count += 1
        
        connection.commit()
        print(f"✅ 成功更新 {updated_count} 个帖子的图片路径")
        
    except Exception as e:
        print(f"❌ 修复过程出错: {e}")
        connection.rollback()
    finally:
        cursor.close()
        connection.close()

def fix_resource_image_paths():
    """修复资源的图片路径"""
    connection = get_db_connection()
    if not connection:
        return
    
    try:
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # 查询所有有图片的资源
        cursor.execute("""
            SELECT id, cover_image, additional_images 
            FROM resources 
            WHERE cover_image IS NOT NULL OR additional_images IS NOT NULL
        """)
        
        resources = cursor.fetchall()
        print(f"🔍 找到 {len(resources)} 个包含图片的资源")
        
        updated_count = 0
        
        for resource in resources:
            resource_id = resource['id']
            cover_image = resource['cover_image']
            additional_images = resource['additional_images']
            
            updated = False
            new_cover_image = cover_image
            new_additional_images = additional_images
            
            # 处理封面图片路径
            if cover_image and not cover_image.startswith(('http', 'resources/')):
                # 可能是旧格式的文件名，添加 resources/ 前缀
                if not '/' in cover_image:  # 纯文件名
                    new_cover_image = 'resources/' + cover_image
                    updated = True
                    print(f"📸 资源 {resource_id} 封面图片: {cover_image} -> {new_cover_image}")
            elif cover_image and cover_image.startswith('resources/') and '/' not in cover_image[10:]:
                # 旧格式：resources/filename.jpg，需要添加日期目录结构
                # 但由于我们不知道原始上传日期，暂时保持不变
                # 这种情况通过图片服务的回退机制来处理
                pass
            
            # 处理附加图片路径（如果有的话）
            if additional_images:
                image_list = [img.strip() for img in additional_images.split(',') if img.strip()]
                new_image_list = []
                
                for img in image_list:
                    if not img.startswith(('http', 'resources/')):
                        if not '/' in img:  # 纯文件名
                            new_img = 'resources/' + img
                            new_image_list.append(new_img)
                            print(f"📸 资源 {resource_id} 附加图片: {img} -> {new_img}")
                            updated = True
                        else:
                            new_image_list.append(img)
                    elif img.startswith('resources/') and '/' not in img[10:]:
                        # 旧格式：resources/filename.jpg，保持不变，通过服务回退机制处理
                        new_image_list.append(img)
                    else:
                        new_image_list.append(img)
                
                new_additional_images = ','.join(new_image_list) if new_image_list else None
            
            # 更新数据库
            if updated:
                cursor.execute("""
                    UPDATE resources 
                    SET cover_image = %s, additional_images = %s 
                    WHERE id = %s
                """, (new_cover_image, new_additional_images, resource_id))
                updated_count += 1
        
        connection.commit()
        print(f"✅ 成功更新 {updated_count} 个资源的图片路径")
        
    except Exception as e:
        print(f"❌ 修复过程出错: {e}")
        connection.rollback()
    finally:
        cursor.close()
        connection.close()

def main():
    print("🔧 图片路径修复工具")
    print("=" * 50)
    print(f"⏰ 开始时间: {datetime.now()}")
    
    print("\n📋 修复论坛帖子图片路径...")
    fix_forum_image_paths()
    
    print("\n📋 修复资源图片路径...")
    fix_resource_image_paths()
    
    print(f"\n✅ 修复完成: {datetime.now()}")
    print("\n💡 建议：")
    print("1. 重新部署应用")
    print("2. 测试图片显示是否正常")
    print("3. 如果正常，可以删除此脚本")

if __name__ == "__main__":
    main()
