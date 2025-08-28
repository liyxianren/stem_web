#!/usr/bin/env python3
"""
图片清理脚本 - 手动清理孤立的图片文件
用于清理数据库中不存在但服务器上仍存在的图片文件

使用方法:
1. 查看模式: python cleanup_orphaned_images.py --dry-run
2. 实际删除: python cleanup_orphaned_images.py --delete
3. 清理特定文件: python cleanup_orphaned_images.py --file "forum_images/2025/08/filename.jpg"
"""

import os
import sys
import argparse
import pymysql
from datetime import datetime

# 数据库配置 - 请根据实际情况修改
DB_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'user': os.getenv('MYSQL_USER', 'root'),
    'password': os.getenv('MYSQL_PASSWORD', ''),
    'database': os.getenv('MYSQL_DB', 'ai_storytelling'),
    'charset': 'utf8mb4'
}

# 图片目录配置
IMAGE_BASE_PATHS = [
    '/image/forum_images',
    '/image/resources'
]

def get_db_connection():
    """获取数据库连接"""
    try:
        return pymysql.connect(**DB_CONFIG)
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return None

def get_valid_image_paths():
    """从数据库获取所有有效的图片路径"""
    connection = get_db_connection()
    if not connection:
        return set()
    
    valid_paths = set()
    
    try:
        cursor = connection.cursor()
        
        # 获取论坛帖子的图片路径
        cursor.execute("""
            SELECT cover_image, additional_images 
            FROM forum_posts 
            WHERE cover_image IS NOT NULL OR additional_images IS NOT NULL
        """)
        
        for row in cursor.fetchall():
            cover_image, additional_images = row
            
            if cover_image:
                valid_paths.add(cover_image.strip())
            
            if additional_images:
                for img in additional_images.split(','):
                    img = img.strip()
                    if img:
                        valid_paths.add(img)
        
        # 获取资源的图片路径
        cursor.execute("""
            SELECT cover_image, additional_images 
            FROM resources 
            WHERE cover_image IS NOT NULL OR additional_images IS NOT NULL
        """)
        
        for row in cursor.fetchall():
            cover_image, additional_images = row
            
            if cover_image:
                valid_paths.add(cover_image.strip())
            
            if additional_images:
                for img in additional_images.split(','):
                    img = img.strip()
                    if img:
                        valid_paths.add(img)
        
        cursor.close()
        connection.close()
        
        print(f"📊 数据库中找到 {len(valid_paths)} 个有效图片路径")
        return valid_paths
        
    except Exception as e:
        print(f"❌ 查询数据库失败: {e}")
        connection.close()
        return set()

def find_all_image_files():
    """查找服务器上的所有图片文件"""
    all_files = []
    
    for base_path in IMAGE_BASE_PATHS:
        if not os.path.exists(base_path):
            print(f"⚠️ 路径不存在: {base_path}")
            continue
        
        print(f"🔍 扫描目录: {base_path}")
        
        for root, dirs, files in os.walk(base_path):
            for file in files:
                # 跳过系统文件
                if file.startswith('.') or file == '.gitkeep':
                    continue
                
                # 只处理图片文件
                if not file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                    continue
                
                full_path = os.path.join(root, file)
                # 计算相对于 /image/ 的路径
                relative_path = os.path.relpath(full_path, '/image')
                
                all_files.append({
                    'full_path': full_path,
                    'relative_path': relative_path,
                    'size': os.path.getsize(full_path) if os.path.exists(full_path) else 0
                })
    
    print(f"📊 服务器上找到 {len(all_files)} 个图片文件")
    return all_files

def delete_file_safely(file_path):
    """安全删除文件"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"✅ 成功删除: {file_path}")
            return True
        else:
            print(f"⚠️ 文件不存在: {file_path}")
            return True  # 文件不存在也算成功
    except PermissionError:
        print(f"❌ 权限不足，无法删除: {file_path}")
        # 尝试使用系统命令
        try:
            import subprocess
            result = subprocess.run(['rm', '-f', file_path], capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✅ 使用系统rm命令成功删除: {file_path}")
                return True
            else:
                print(f"❌ 系统rm命令也失败: {result.stderr}")
                return False
        except Exception as e:
            print(f"❌ 系统rm命令执行失败: {e}")
            return False
    except Exception as e:
        print(f"❌ 删除失败: {file_path}, 错误: {e}")
        return False

def cleanup_orphaned_images(dry_run=True, specific_file=None):
    """清理孤立的图片文件"""
    print(f"🚀 开始图片清理任务 {'(预览模式)' if dry_run else '(实际删除)'}")
    print(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    if specific_file:
        # 清理特定文件
        print(f"🎯 清理特定文件: {specific_file}")
        
        full_path = f"/image/{specific_file}"
        if os.path.exists(full_path):
            if dry_run:
                print(f"🔍 [预览] 将删除文件: {full_path}")
            else:
                if delete_file_safely(full_path):
                    print(f"✅ 成功删除特定文件: {specific_file}")
                else:
                    print(f"❌ 删除特定文件失败: {specific_file}")
        else:
            print(f"⚠️ 特定文件不存在: {full_path}")
        return
    
    # 获取数据库中的有效路径
    valid_paths = get_valid_image_paths()
    if not valid_paths:
        print("❌ 无法获取数据库中的有效路径，停止清理")
        return
    
    # 查找服务器上的所有图片文件
    all_files = find_all_image_files()
    if not all_files:
        print("ℹ️ 没有找到任何图片文件")
        return
    
    # 找出孤立的文件
    orphaned_files = []
    total_orphaned_size = 0
    
    for file_info in all_files:
        if file_info['relative_path'] not in valid_paths:
            orphaned_files.append(file_info)
            total_orphaned_size += file_info['size']
    
    print(f"\n📊 清理统计:")
    print(f"   总文件数: {len(all_files)}")
    print(f"   有效文件数: {len(all_files) - len(orphaned_files)}")
    print(f"   孤立文件数: {len(orphaned_files)}")
    print(f"   孤立文件总大小: {total_orphaned_size / 1024 / 1024:.2f} MB")
    
    if not orphaned_files:
        print("✅ 没有发现孤立的图片文件")
        return
    
    print(f"\n{'🔍 孤立文件列表 (预览):' if dry_run else '🗑️ 开始删除孤立文件:'}")
    
    success_count = 0
    failed_count = 0
    
    for file_info in orphaned_files:
        if dry_run:
            print(f"   📁 {file_info['relative_path']} ({file_info['size']} bytes)")
        else:
            if delete_file_safely(file_info['full_path']):
                success_count += 1
            else:
                failed_count += 1
    
    if not dry_run:
        print(f"\n📊 删除结果:")
        print(f"   成功删除: {success_count} 个文件")
        print(f"   删除失败: {failed_count} 个文件")
        
        if failed_count > 0:
            print(f"\n💡 如果有文件删除失败，可以尝试:")
            print(f"   1. SSH到服务器，手动执行: rm -rf /image/forum_images/指定文件")
            print(f"   2. 检查文件权限和目录权限")
            print(f"   3. 重启应用程序后再次尝试")

def main():
    parser = argparse.ArgumentParser(description='清理孤立的图片文件')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不实际删除文件')
    parser.add_argument('--delete', action='store_true', help='实际删除文件')
    parser.add_argument('--file', type=str, help='删除特定文件 (相对路径，如: forum_images/2025/08/filename.jpg)')
    
    args = parser.parse_args()
    
    if not args.delete and not args.dry_run and not args.file:
        print("请指定操作模式:")
        print("  --dry-run  : 预览模式")
        print("  --delete   : 实际删除")
        print("  --file PATH: 删除特定文件")
        return
    
    if args.delete and args.dry_run:
        print("❌ 不能同时指定 --delete 和 --dry-run")
        return
    
    dry_run = args.dry_run or not args.delete
    
    try:
        cleanup_orphaned_images(dry_run=dry_run, specific_file=args.file)
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断操作")
    except Exception as e:
        print(f"\n❌ 清理过程中发生错误: {e}")

if __name__ == '__main__':
    main()