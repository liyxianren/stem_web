#!/usr/bin/env python3
"""
测试匿名用户是否能正常访问网站内容
"""

import requests
import sys

# 测试URL列表
test_urls = [
    'http://localhost:5000/',  # 首页
    'http://localhost:5000/forum',  # 论坛
    'http://localhost:5000/subjects',  # 资源总览
    'http://localhost:5000/subjects/igcse',  # IGCSE资源
    'http://localhost:5000/subjects/alevel',  # A-Level资源
    'http://localhost:5000/competitions/BPHO',  # BPHO资源
    'http://localhost:5000/university',  # 大学资源
]

def test_anonymous_access():
    """测试匿名访问"""
    print("=" * 50)
    print("测试匿名用户访问权限")
    print("=" * 50)
    
    # 使用session来模拟匿名用户
    session = requests.Session()
    
    success_count = 0
    total_count = len(test_urls)
    
    for url in test_urls:
        try:
            print(f"\n测试: {url}")
            response = session.get(url, timeout=10)
            
            if response.status_code == 200:
                print(f"✓ 成功访问 (状态码: {response.status_code})")
                success_count += 1
                
                # 检查是否被重定向到登录页面
                if '/login' in response.url:
                    print("✗ 被重定向到登录页面")
                    success_count -= 1
                elif 'Login Required' in response.text:
                    print("⚠ 页面包含'Login Required'")
                else:
                    print("✓ 内容正常加载")
                    
            elif response.status_code == 302:
                print(f"✗ 重定向 (状态码: {response.status_code})")
                if 'Location' in response.headers:
                    print(f"  重定向到: {response.headers['Location']}")
            else:
                print(f"✗ 访问失败 (状态码: {response.status_code})")
                
        except requests.exceptions.ConnectionError:
            print("✗ 连接失败 - 请确保服务器运行在 localhost:5000")
        except Exception as e:
            print(f"✗ 错误: {e}")
    
    print(f"\n" + "=" * 50)
    print(f"测试结果: {success_count}/{total_count} 个页面可正常访问")
    print("=" * 50)
    
    return success_count == total_count

if __name__ == "__main__":
    try:
        success = test_anonymous_access()
        if success:
            print("\n🎉 所有测试通过！匿名用户可以正常访问网站内容。")
            sys.exit(0)
        else:
            print("\n❌ 部分测试失败，请检查配置。")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
        sys.exit(1)