# 基于隐匿查询的电子健康记录隐私保护查询系统

本项目是一个基于Private Information Retrieval (PIR)技术的电子健康记录隐私保护查询系统，采用Flask构建，支持基于角色的访问控制(RBAC)，确保医疗数据在分析和共享过程中的隐私和安全。

## 项目特色

- **隐私保护查询技术**: 基于PIR(Private Information Retrieval)实现的隐匿查询机制，用户可查询自己的健康记录而不泄露查询意图
- **查询混淆**: 通过加入噪声查询，掩盖用户真实查询意图，增强隐私保护
- **多级数据访问控制**: 精细化的数据可见性设置，支持私有、医生可见、研究人员可见和公开多种级别
- **高效的混合存储**: 采用MongoDB存储健康记录数据，MySQL存储关系型数据，优化查询性能
- **安全的记录共享**: 支持有时效性的健康记录安全共享机制
- **支持完整CRUD**: 全面的健康记录管理，包括创建、读取、更新和删除操作
- **文件上传管理**: 支持各类医疗文件的上传和管理
- **数据导入导出**: 支持健康记录的JSON/CSV格式导入导出
- **实时通知系统**: 记录共享和访问的实时通知机制

## 项目架构

```
.
├── app/
│   ├── config/         # 配置设置
│   ├── models/         # 数据库模型
│   │   ├── user.py     # 用户和角色模型
│   │   ├── health_records.py # 健康记录相关模型
│   │   ├── notification.py # 通知系统模型
│   │   └── role_models.py # 角色特定信息模型
│   ├── routers/        # API路由
│   │   ├── auth.py     # 认证相关路由
│   │   ├── admin.py    # 管理员路由
│   │   ├── health_records.py # 健康记录相关路由
│   │   ├── notifications.py # 通知系统路由
│   │   └── main.py     # 主要路由
│   └── utils/          # 工具函数
│       ├── pir_utils.py # PIR隐匿查询工具
│       ├── mongo_utils.py # MongoDB工具
│       ├── redis_utils.py # Redis缓存工具
│       └── jwt_utils.py # JWT认证工具
├── requirements.txt    # 项目依赖
├── .env                # 环境变量配置
└── run.py             # 应用入口点
```

## 隐私保护机制详解

### PIR (Private Information Retrieval) 实现

本系统采用了以下PIR相关技术确保用户数据查询的隐私：

1. **查询向量**: 用户查询时生成查询向量，服务器只能知道用户获取了数据，但无法得知具体查询内容
2. **查询混淆**: 每次查询自动添加1-3个噪声查询，掩盖真实查询意图
3. **索引加密**: 真实查询索引通过密钥加密，确保只有用户本人能识别真实查询结果
4. **匿名查询选项**: 用户可选择匿名查询模式，进一步增强隐私保护

### 数据保护层次

- **存储加密**: 敏感健康数据在存储时进行加密
- **传输加密**: 数据传输通过HTTPS协议加密
- **访问控制**: 精细的基于角色的访问控制，确保数据只对授权用户可见
- **记录可见性**: 每条健康记录可单独设置可见性级别（私有/医生可见/研究人员可见/公开）
- **记录共享**: 支持有时限的健康记录定向共享，并可随时撤销权限

## 功能特点

- 完整的RBAC角色管理系统（患者、医生、研究人员、管理员）
- JWT认证和Flask-Login会话管理
- 角色特定的用户资料和数据模型
- 安全的密码加密和验证
- 角色验证装饰器保护敏感路由
- 多数据库支持（MySQL、MongoDB、Redis）
- 基于环境变量的灵活配置
- 高级搜索功能，支持多维度筛选和全文搜索
- 健康数据分析和统计功能
- 低延迟的隐匿查询实现

## 支持的角色

- **患者(Patient)**: 可管理个人医疗记录，进行隐匿查询，共享记录给指定人员
- **医生(Doctor)**: 可查看患者共享的医疗记录，管理预约，记录诊疗信息
- **研究人员(Researcher)**: 可访问公开研究数据，获取匿名化健康统计信息
- **管理员(Admin)**: 系统管理员，具有完全访问权限，管理用户和系统设置

## 开始使用

### 前提条件

- Python 3.8 或更高版本
- pip (Python包管理器)
- MySQL (必须)
- MongoDB（必须）
- Redis（可选，用于缓存和会话管理）

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

### 配置环境变量

创建一个`.env`文件在根目录下，包含以下变量:

```
# 基本配置
SECRET_KEY=你的密钥           # 用于加密会话和JWT
FLASK_ENV=development       # 环境类型: development, testing, production
FLASK_DEBUG=true            # 是否开启调试模式
HOST=0.0.0.0                # 主机地址，0.0.0.0表示所有接口
PORT=5000                   # 应用端口

# 数据库配置
# MySQL 配置 (必须)
DEV_MYSQL_URL=mysql://用户名:密码@主机/开发数据库
TEST_MYSQL_URL=mysql://用户名:密码@主机/测试数据库
MYSQL_URL=mysql://用户名:密码@主机/生产数据库

# MongoDB 配置 (必须)
DEV_MONGO_URI=mongodb://主机:端口/开发数据库
TEST_MONGO_URI=mongodb://主机:端口/测试数据库
MONGO_URI=mongodb://主机:端口/生产数据库

# Redis 配置 (可选)
REDIS_URL=redis://主机:端口/数据库号

# PIR配置
PIR_ENABLE_OBFUSCATION=true  # 是否启用查询混淆
PIR_MAX_NOISE_QUERIES=3      # 最大噪声查询数
PIR_ENCRYPTION_STRENGTH=high # 加密强度 (low/medium/high)
```

系统会根据`FLASK_ENV`环境变量自动选择相应的数据库配置。

### 数据库准备

在运行应用前，确保已创建MySQL数据库：

```sql
CREATE DATABASE pir_health_dev;  -- 开发环境
CREATE DATABASE pir_health_test; -- 测试环境
CREATE DATABASE pir_health;      -- 生产环境
```

同时确保MongoDB服务已启动。

### 运行应用

```
python run.py
```

服务器将基于`.env`文件中的配置启动（默认为http://localhost:5000）

## API 端点

### 认证相关

- `POST /api/auth/register`: 用户注册
- `POST /api/auth/login`: 用户登录
- `POST /api/auth/logout`: 用户登出
- `GET /api/auth/me`: 获取当前用户信息
- `PUT /api/auth/me`: 更新当前用户信息
- `POST /api/auth/change-password`: 修改密码
- `POST /api/auth/avatar`: 上传用户头像
- `GET /api/auth/avatar/<filename>`: 获取用户头像

### 健康记录相关

- `POST /api/health/records`: 创建健康记录
- `GET /api/health/records`: 获取健康记录列表
- `GET /api/health/records/<record_id>`: 获取单条健康记录详情
- `PUT /api/health/records/<record_id>`: 更新健康记录
- `DELETE /api/health/records/<record_id>`: 删除健康记录
- `GET /api/health/files/<filename>`: 获取记录相关文件
- `GET /api/health/statistics`: 获取健康数据统计信息

### 隐匿查询相关

- `GET /api/health/pir/records`: 隐匿查询健康记录
- `GET /api/health/pir/statistics`: 获取PIR查询统计信息
- `GET /api/health/pir/history`: 获取PIR查询历史
- `POST /api/health/pir/advanced`: 高级PIR隐匿查询 (使用PIRQuery向量)
- `GET /api/health/pir/settings`: 获取PIR隐私设置
- `PUT /api/health/pir/settings`: 更新PIR隐私设置

### 记录共享相关

- `POST /api/health/records/<record_id>/share`: 共享健康记录
- `GET /api/health/shared/by-me`: 获取我共享的记录列表
- `GET /api/health/shared/with-me`: 获取共享给我的记录列表
- `GET /api/health/shared/records/<shared_id>`: 查看共享记录详情
- `DELETE /api/health/shared/<shared_id>`: 撤销共享

### 高级搜索与数据导入导出

- `POST /api/health/search/advanced`: 高级搜索功能
- `GET /api/health/search/filters`: 获取可用的筛选条件
- `POST /api/health/export`: 导出健康记录
- `GET /api/health/export/download/<filename>`: 下载导出的记录文件
- `POST /api/health/import`: 导入健康记录

### 通知系统

- `GET /api/notifications`: 获取通知列表
- `PUT /api/notifications/<notification_id>/read`: 标记通知为已读
- `PUT /api/notifications/read-all`: 标记所有通知为已读
- `DELETE /api/notifications/<notification_id>`: 删除通知
- `GET /api/notifications/unread-count`: 获取未读通知数量

### 管理员专用

- `GET /api/admin/users`: 获取所有用户列表（分页）
- `GET /api/admin/users/<id>`: 获取单个用户详情
- `POST /api/admin/users`: 创建新用户
- `PUT /api/admin/users/<id>`: 更新用户信息
- `DELETE /api/admin/users/<id>`: 删除用户
- `GET /api/admin/stats`: 获取系统统计数据
- `POST /api/notifications/system`: 创建系统通知

## 隐私安全原则

本系统严格遵循以下隐私安全原则：

1. **数据最小化**: 只收集和处理必要的健康数据
2. **用途明确**: 明确数据使用目的并获得用户同意
3. **访问控制**: 严格基于角色的访问控制和认证
4. **隐私设计**: 隐私保护机制在系统设计阶段即被纳入考量
5. **数据可控**: 用户可随时访问、修改和删除个人数据
6. **匿名优先**: 优先采用匿名化处理机制进行数据共享和分析

## 部署

### 生产环境

对于生产环境，建议修改以下配置：

1. 在`.env`文件中设置:
   ```
   FLASK_ENV=production
   FLASK_DEBUG=false
   PIR_ENCRYPTION_STRENGTH=high
   ```

2. 确保使用强密码和安全的数据库连接字符串
3. 配置HTTPS协议以确保传输加密
4. 使用Gunicorn或uWSGI作为WSGI服务器
5. 配置反向代理（如Nginx）以提高安全性和性能

### Docker部署

项目支持Docker容器化部署，详细配置请参考项目中的Docker相关文件。

## PIR实验API接口

PIR（Private Information Retrieval，隐私信息检索）实验API接口用于研究员进行PIR协议的性能评估和对比实验。

### PIR实验流程

研究员可以通过以下步骤进行PIR实验：

1. 生成模拟健康数据 - 创建测试数据集
2. 配置PIR协议参数 - 设置不同类型的PIR协议和参数
3. 执行隐私查询测试 - 运行实验并获取结果
4. 分析性能指标 - 评估查询时间、通信成本、隐私保护级别等指标
5. 比较不同协议 - 对比不同PIR协议的性能优劣

### PIR实验API端点

#### 1. 生成模拟健康数据
- **URL**: `/api/researcher/experiment/generate-mock-data`
- **方法**: `POST`
- **权限**: 研究员
- **参数**:
  - `count`: 生成的记录数量，默认100，最大1000
  - `structured`: 是否生成结构化数据，默认true
  - `record_types`: 记录类型列表，默认使用所有类型
- **响应**:
  ```json
  {
    "success": true,
    "message": "成功生成模拟健康数据",
    "data": {
      "experiment_id": "实验ID",
      "data_count": 100,
      "sample": [...]  // 样例数据
    }
  }
  ```

#### 2. 配置PIR协议参数
- **URL**: `/api/researcher/experiment/configure-protocol`
- **方法**: `POST`
- **权限**: 研究员
- **参数**:
  - `experiment_id`: 实验ID
  - `protocol_type`: 协议类型，可选值为：
    - `basic`: 基本PIR协议
    - `homomorphic`: 同态加密PIR
    - `hybrid`: 混合PIR协议
    - `onion`: 洋葱路由PIR
  - `params`: 协议特定参数，可选
- **响应**:
  ```json
  {
    "success": true,
    "message": "PIR协议配置成功",
    "data": {
      "experiment_id": "实验ID",
      "protocol_type": "协议类型",
      "protocol_config": {...}  // 协议配置
    }
  }
  ```

#### 3. 执行隐私查询测试
- **URL**: `/api/researcher/experiment/execute-query`
- **方法**: `POST`
- **权限**: 研究员
- **参数**:
  - `experiment_id`: 实验ID
  - `query_count`: 执行查询的次数，默认10，最大50
- **响应**:
  ```json
  {
    "success": true,
    "message": "PIR查询实验执行成功",
    "data": {
      "experiment_id": "实验ID",
      "query_count": 10,
      "results": {
        "results": [...],  // 查询结果
        "metrics": {...},  // 性能指标
        "protocol": {...}  // 使用的协议
      }
    }
  }
  ```

#### 4. 获取性能指标
- **URL**: `/api/researcher/experiment/performance-metrics`
- **方法**: `GET`
- **权限**: 研究员
- **参数**:
  - `experiment_id`: 实验ID
- **响应**:
  ```json
  {
    "success": true,
    "message": "获取性能指标成功",
    "data": {
      "experiment_id": "实验ID",
      "protocol": {...},  // 协议配置
      "metrics": {
        "query_time": 0.123,  // 查询时间
        "accuracy": 1.0,      // 准确率
        "comm_cost": 1024,    // 通信成本
        "server_load": 0.5,   // 服务器负载
        "client_load": 0.2,   // 客户端负载
        "privacy_level": 8    // 隐私保护级别
      },
      "timestamp": "2023-01-01T00:00:00"
    }
  }
  ```

#### 5. 比较多个协议性能
- **URL**: `/api/researcher/experiment/compare-protocols`
- **方法**: `POST`
- **权限**: 研究员
- **参数**:
  - `experiment_ids`: 要比较的实验ID列表，至少需要两个
- **响应**:
  ```json
  {
    "success": true,
    "message": "PIR协议性能比较成功",
    "data": {
      "protocols": [
        {
          "experiment_id": "实验1ID",
          "protocol_type": "basic",
          "metrics": {...}
        },
        {
          "experiment_id": "实验2ID",
          "protocol_type": "homomorphic",
          "metrics": {...}
        }
      ],
      "comparisons": [
        {
          "baseline": {"experiment_id": "实验1ID", "protocol_type": "basic"},
          "current": {"experiment_id": "实验2ID", "protocol_type": "homomorphic"},
          "report": {
            "summary": {...},
            "comparisons": {...},
            "recommendations": [...]
          }
        }
      ]
    }
  }
  ```

#### 6. 获取实验列表
- **URL**: `/api/researcher/experiments`
- **方法**: `GET`
- **权限**: 研究员
- **响应**:
  ```json
  {
    "success": true,
    "message": "获取实验列表成功",
    "data": {
      "experiments": [
        {
          "id": "实验ID",
          "experiment_type": "mock_data_generation",
          "created_at": "2023-01-01T00:00:00",
          "updated_at": "2023-01-01T00:00:00",
          "data_count": 100,
          "protocol_type": "basic",
          "has_results": true,
          "query_time": "2023-01-01T00:00:00",
          "metrics_summary": {
            "query_time": 0.123,
            "privacy_level": 3
          }
        },
        ...
      ]
    }
  }
  ```

#### 7. 获取实验详情
- **URL**: `/api/researcher/experiments/{experiment_id}`
- **方法**: `GET`
- **权限**: 研究员
- **响应**:
  ```json
  {
    "success": true,
    "message": "获取实验详情成功",
    "data": {
      "id": "实验ID",
      "experiment_type": "mock_data_generation",
      "created_at": "2023-01-01T00:00:00",
      "updated_at": "2023-01-01T00:00:00",
      "parameters": {...},
      "data_count": 100,
      "protocol_config": {...},
      "query_time": "2023-01-01T00:00:00",
      "results": {
        "metrics": {...},
        "sample_results": [...]
      },
      "data_samples": [...]
    }
  }
  ```

#### 8. 删除实验
- **URL**: `/api/researcher/experiments/{experiment_id}`
- **方法**: `DELETE`
- **权限**: 研究员
- **响应**:
  ```json
  {
    "success": true,
    "message": "删除实验成功",
    "data": {
      "experiment_id": "实验ID"
    }
  }
  ```

### PIR协议类型

本系统支持以下PIR协议类型：

1. **基本PIR协议 (Basic)** - 简单直接的PIR实现，适合教学和基准测试
2. **同态加密PIR (Homomorphic)** - 使用同态加密技术的PIR，提供高强度安全性
3. **混合PIR协议 (Hybrid)** - 结合多种技术的综合协议，平衡性能和安全性
4. **洋葱路由PIR (Onion)** - 基于洋葱路由的多层加密PIR，提供最高隐私保护

### 性能指标说明

实验结果包含以下性能指标：

1. **查询时间 (query_time)** - 执行查询所需的时间（秒）
2. **准确率 (accuracy)** - 查询结果的准确性（0-1）
3. **通信成本 (comm_cost)** - 查询产生的网络通信量（字节）
4. **服务器负载 (server_load)** - 服务器端计算负担
5. **客户端负载 (client_load)** - 客户端计算负担
6. **隐私保护级别 (privacy_level)** - 协议提供的隐私保护强度（1-10）

### 实验建议

- 针对不同规模的数据集测试PIR协议性能
- 比较不同协议在查询时间和通信量之间的权衡
- 评估各协议在不同网络环境下的表现
- 根据具体应用场景，选择合适的PIR协议参数

## PIR实验实现详情

PIR实验功能的实现通过以下主要组件完成：

### 1. 核心功能类和工具函数

- **PIRProtocolType** - 定义支持的PIR协议类型（基本、同态加密、混合等）
- **PIRPerformanceMetric** - 定义评估PIR性能的关键指标
- **generate_mock_health_data()** - 生成结构化/非结构化模拟健康数据
- **configure_pir_protocol()** - 配置不同类型的PIR协议参数
- **execute_pir_query_experiment()** - 执行PIR查询实验并收集性能指标
- **analyze_experiment_results()** - 分析实验结果并提供优化建议

### 2. API端点

为研究员提供8个专用API端点，覆盖实验全流程：
1. 生成模拟数据
2. 配置协议参数
3. 执行查询测试
4. 收集性能指标
5. 比较协议性能
6. 管理实验记录

### 3. 数据存储

- 实验元数据和结果存储在MongoDB的`pir_experiments`集合中
- 每个实验的模拟数据存储在单独的集合中，命名格式为`experiment_data_{实验ID}`
- 性能指标以结构化格式存储，便于可视化和比较分析

### 4. 性能评估

系统评估6个关键性能指标：
- 查询时间
- 准确率
- 通信成本
- 服务器负载
- 客户端负载
- 隐私保护级别

这些指标全面反映了PIR协议的效率和安全性权衡。

### 5. 协议实现

目前支持的PIR协议包括：
- **基本PIR** - 简单直接的实现，适合作为基准
- **同态加密PIR** - 提供高安全性，但计算开销较大
- **混合PIR** - 结合分区技术和加密，平衡效率和隐私
- **洋葱路由PIR** - 多层加密提供极高隐私保护

### 技术特性

1. **模块化设计** - 便于添加新的PIR协议实现
2. **可配置参数** - 每种协议提供多个可调整参数
3. **实时性能监控** - 详细记录每个查询的执行情况
4. **数据可视化** - 支持将实验结果导出分析
5. **比较分析** - 自动生成协议性能对比报告

### 使用建议

研究员可以通过以下方式充分利用PIR实验功能：

1. 从小规模数据集开始，逐步增加数据量测试扩展性
2. 针对每种协议类型，测试不同参数配置的影响
3. 记录在不同网络条件下的性能差异
4. 根据具体应用场景的重点（查询速度、隐私强度等）选择最适合的协议 