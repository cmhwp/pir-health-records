# 医疗系统 - 角色权限管理

基于Flask构建的医疗系统RBAC（基于角色的访问控制）后端API项目。

## 项目架构

```
.
├── app/
│   ├── config/         # 配置设置
│   ├── models/         # 数据库模型
│   │   ├── user.py     # 用户和角色模型
│   │   └── role_models.py # 角色特定信息模型
│   ├── routers/        # API路由
│   │   ├── auth.py     # 认证相关路由
│   │   ├── admin.py    # 管理员路由
│   │   └── main.py     # 主要路由
│   └── utils/          # 工具函数
├── requirements.txt    # 项目依赖
└── run.py             # 应用入口点
```

## 功能特点

- 完整的RBAC角色管理系统（患者、医生、研究人员、管理员）
- JWT认证和Flask-Login会话管理
- 角色特定的用户资料和数据模型
- 安全的密码加密和验证
- 角色验证装饰器保护敏感路由
- 多数据库支持（MySQL、MongoDB、Redis）

## 支持的角色

- **患者(Patient)**: 可以查看个人医疗记录、预约和处方信息
- **医生(Doctor)**: 可以管理患者、预约和医疗记录
- **研究人员(Researcher)**: 可以访问研究数据和参与研究项目
- **管理员(Admin)**: 系统管理员，具有完全访问权限

## 开始使用

### 前提条件

- Python 3.8 或更高版本
- pip (Python包管理器)
- MySQL（可选）
- MongoDB（可选）
- Redis（可选）

### 安装

1. 克隆仓库:
   ```
   git clone <仓库URL>
   cd <项目文件夹>
   ```

2. 创建虚拟环境:
   ```
   python -m venv venv
   ```

3. 激活虚拟环境:
   - Windows:
     ```
     venv\Scripts\activate
     ```
   - macOS/Linux:
     ```
     source venv/bin/activate
     ```

4. 安装依赖:
   ```
   pip install -r requirements.txt
   ```

### 配置

创建一个`.env`文件在根目录下，包含以下变量:

```
SECRET_KEY=你的密钥
DATABASE_URL=你的数据库连接字符串
MONGO_URI=你的MongoDB连接字符串
REDIS_URL=你的Redis连接字符串
```

### 运行应用

```
python run.py
```

服务器将在 http://localhost:5000 启动

## API 端点

### 认证相关

- `POST /api/auth/register`: 用户注册
- `POST /api/auth/login`: 用户登录
- `POST /api/auth/logout`: 用户登出
- `GET /api/auth/me`: 获取当前用户信息
- `PUT /api/auth/me`: 更新当前用户信息
- `POST /api/auth/change-password`: 修改密码

### 管理员专用

- `GET /api/admin/users`: 获取所有用户列表（分页）
- `GET /api/admin/users/<id>`: 获取单个用户详情
- `POST /api/admin/users`: 创建新用户
- `PUT /api/admin/users/<id>`: 更新用户信息
- `DELETE /api/admin/users/<id>`: 删除用户
- `GET /api/admin/stats`: 获取系统统计数据

### 角色专用

- `GET /api/patient-dashboard`: 患者控制台数据
- `GET /api/doctor-dashboard`: 医生控制台数据
- `GET /api/researcher-dashboard`: 研究人员控制台数据
- `GET /api/admin-dashboard`: 管理员控制台数据 