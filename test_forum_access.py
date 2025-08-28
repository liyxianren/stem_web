#!/usr/bin/env python3
"""
测试Forum访问的脚本
"""

import requests

def test_forum_access():
    """测试匿名用户访问forum相关页面"""
    base_url = "http://127.0.0.1:5000"
    
    session = requests.Session()
    
    print("=" * 50)
    print("测试Forum访问权限")
    print("=" * 50)
    
    # 测试论坛主页
    print("\n1. 测试 /forum")
    try:
        response = session.get(f"{base_url}/forum")
        print(f"状态码: {response.status_code}")
        if response.status_code == 200:
            print("✓ 论坛主页访问成功")
        else:
            print(f"✗ 论坛主页访问失败: {response.status_code}")
    except Exception as e:
        print(f"✗ 错误: {e}")
    
    # 测试帖子详情页
    print("\n2. 测试 /forum/post/45")
    try:
        response = session.get(f"{base_url}/forum/post/45", allow_redirects=False)
        print(f"状态码: {response.status_code}")
        if response.status_code == 200:
            print("✓ 帖子详情页访问成功")
        elif response.status_code == 302:
            location = response.headers.get('Location', 'Unknown')
            print(f"✗ 被重定向到: {location}")
            
            # 检查重定向原因
            response_with_redirect = session.get(f"{base_url}/forum/post/45")
            if 'Post not found' in response_with_redirect.text:
                print("  原因: 帖子不存在")
            elif 'Error loading post details' in response_with_redirect.text:
                print("  原因: 加载帖子详情时出错")
        else:
            print(f"✗ 帖子详情页访问失败: {response.status_code}")
    except Exception as e:
        print(f"✗ 错误: {e}")
    
    # 测试资源详情页
    print("\n3. 测试 /view_resource/16")
    try:
        response = session.get(f"{base_url}/view_resource/16", allow_redirects=False)
        print(f"状态码: {response.status_code}")
        if response.status_code == 200:
            print("✓ 资源详情页访问成功")
        elif response.status_code == 302:
            location = response.headers.get('Location', 'Unknown')
            print(f"✗ 被重定向到: {location}")
        else:
            print(f"✗ 资源详情页访问失败: {response.status_code}")
    except Exception as e:
        print(f"✗ 错误: {e}")

if __name__ == "__main__":
    test_forum_access()