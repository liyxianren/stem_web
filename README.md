# STEM学术资源平台

一个基于Flask的学术资源分享和论坛讨论平台。

## 功能特点

- 📚 学术资源分享 (IGCSE, A-Level, BPHO, Physics Bowl, University)
- 💬 论坛讨论系统
- 👥 用户管理和权限控制
- 📊 管理员后台
- 🎨 响应式设计

## 部署到Zeabur

### 1. 项目检测

Zeabur会自动检测这是一个Python Flask项目（基于`requirements.txt`和`app.py`）。

### 2. 数据库配置

应用使用MySQL数据库，数据库连接信息在app.py中硬编码：

```python
host='sha1.clusters.zeabur.com'
port=31890
user='root' 
database='zeabur'
```

### 3. 文件结构

```
/
├── app.py              # 主应用文件
├── requirements.txt    # Python依赖
├── image_upload_handler.py  # 图片上传处理
├── static/            # 静态文件
│   └── uploads/       # 用户上传文件
└── templates/         # HTML模板
    ├── admin/         # 管理员页面
    └── ...           # 其他页面
```

### 4. 部署步骤

1. 将代码推送到Git仓库
2. 在Zeabur创建新项目
3. 连接Git仓库
4. Zeabur自动检测为Python项目并部署

## 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
python app.py
```

## 技术栈

- Backend: Flask, Flask-Login
- Database: MySQL (PyMySQL)
- Frontend: HTML5, CSS3, JavaScript, Bootstrap
- Image Processing: Pillow
- Deployment: Docker, Zeabur