#!/usr/bin/env python3
"""
批量注册102个学生账户到数据库中
用于测试数据生成
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import get_db_connection
import bcrypt
import random
from faker import Faker

# 初始化faker用于生成随机数据
fake = Faker(['en_US'])

def generate_student_data():
    """生成学生数据"""
    students = []
    
    for i in range(102):
        # 生成英文姓名
        first_name = fake.first_name()
        last_name = fake.last_name()
        username = f"{first_name} {last_name}"
        
        # 生成邮箱
        email = f"{first_name.lower()}.{last_name.lower()}{i+1}@student.edu"
        
        # 默认密码
        password = "student123"
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        students.append({
            'username': username,
            'email': email,
            'password_hash': password_hash
        })
    
    return students

def batch_register_students():
    """批量注册学生"""
    print("开始生成学生数据...")
    students = generate_student_data()
    
    print("连接数据库...")
    connection = get_db_connection()
    if not connection:
        print("数据库连接失败，退出程序")
        return
    
    cursor = connection.cursor()
    
    try:
        print(f"开始批量插入 {len(students)} 个学生账户...")
        
        # 批量插入SQL
        insert_sql = """
            INSERT INTO users (username, email, password_hash, registration_status, user_role)
            VALUES (%s, %s, %s, 'approved', 'student')
        """
        
        # 准备数据
        student_data = []
        for student in students:
            student_data.append((
                student['username'],
                student['email'],
                student['password_hash']
            ))
        
        # 分批执行插入，避免超时
        batch_size = 10
        total_inserted = 0
        
        for i in range(0, len(student_data), batch_size):
            batch = student_data[i:i+batch_size]
            cursor.executemany(insert_sql, batch)
            connection.commit()
            total_inserted += len(batch)
            print(f"已插入 {total_inserted}/{len(student_data)} 个账户...")
        
        print(f"成功注册 {len(students)} 个学生账户!")
        print("\n账户信息摘要:")
        print("用户名格式: [FirstName LastName]")
        print("邮箱格式: firstname.lastname[数字]@student.edu")
        print("默认密码: student123")
        
        print("\n前5个账户示例:")
        for i, student in enumerate(students[:5]):
            print(f"{i+1}. 用户名: {student['username']}")
            print(f"   邮箱: {student['email']}")
            print()
        
    except Exception as e:
        print(f"批量插入失败: {e}")
        connection.rollback()
    
    finally:
        cursor.close()
        connection.close()
        print("数据库连接已关闭")

if __name__ == "__main__":
    print("=" * 50)
    print("批量学生注册脚本")
    print("=" * 50)
    
    confirmation = input("确认要注册102个学生账户吗? (y/N): ")
    if confirmation.lower() in ['y', 'yes']:
        batch_register_students()
    else:
        print("操作已取消")