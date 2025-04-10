# 基于隐匿查询的电子健康记录隐私保护查询方案

本系统是基于隐匿查询（Private Information Retrieval，PIR）技术的电子健康记录隐私保护查询系统，旨在在保护用户隐私的前提下，实现对电子健康记录的高效、安全查询。

## 系统架构

系统采用模块化设计，主要包括以下组件：

- 用户认证与授权模块
- 健康记录管理模块
- 隐私保护查询模块
- 隐私策略管理模块

系统使用以下数据库和缓存技术：

- MySQL：存储用户信息、健康记录元数据和隐私策略
- MongoDB：存储加密的健康记录内容和PIR索引
- Redis：缓存查询结果和会话信息

## 技术特点

1. **隐匿查询技术**：采用PIR协议实现隐私保护查询，确保查询过程中不泄露查询内容
2. **多级隐私保护**：支持用户定义不同级别的隐私保护策略
3. **加密存储**：所有健康记录内容采用加密存储，保证数据安全
4. **高效索引**：采用优化的索引结构，提高查询效率
5. **分布式架构**：使用多种数据库技术，实现数据分层存储和处理

## 安装和配置

### 系统要求

- Python 3.8+
- MySQL 8.0+
- MongoDB 4.4+
- Redis 6.0+

### 安装步骤

1. 克隆代码库

```bash
git clone https://github.com/yourusername/pir-health-records.git
cd pir-health-records
```

2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 配置环境变量

复制`.env.example`文件并重命名为`.env`，然后修改其中的配置参数：

```bash
cp .env.example .env
```

4. 初始化数据库

```bash
# 初始化MySQL数据库
mysql -u username -p
> CREATE DATABASE pir_health_dev;
> CREATE DATABASE pir_health_test;
> CREATE DATABASE pir_health;
> exit

# MongoDB和Redis通常不需要预先创建数据库
```

5. 运行应用

```bash
python run.py
```

应用将在`http://localhost:5000`运行。

## API接口

系统提供以下主要API接口：

### 认证相关

- `POST /api/auth/register` - 用户注册
- `POST /api/auth/login` - 用户登录
- `POST /api/auth/logout` - 用户登出
- `GET /api/auth/me` - 获取当前用户信息

### 健康记录相关

- `POST /api/health-records` - 创建健康记录
- `GET /api/health-records/{id}` - 获取指定ID的健康记录
- `PUT /api/health-records/{id}` - 更新健康记录
- `GET /api/health-records/user/{id}` - 获取指定用户的所有健康记录

### 隐私策略相关

- `POST /api/health-records/privacy-policy` - 设置隐私策略
- `GET /api/health-records/privacy-policy` - 获取当前用户的隐私策略

### 查询相关

- `POST /api/query/keyword` - 基于关键词的隐匿查询
- `GET /api/query/results/{id}` - 获取查询结果
- `GET /api/query/status/{id}` - 获取查询状态
- `POST /api/query/pir` - 直接PIR查询（高级用户）

## 系统安全性

本系统采用多层次安全防护机制：

1. 传输层安全：推荐使用HTTPS协议保护数据传输
2. 应用层安全：采用会话管理和身份验证机制
3. 数据层安全：所有敏感数据加密存储
4. 查询安全：使用PIR技术保护查询隐私

## 性能优化

为了提高系统性能，采取了以下措施：

1. 使用Redis缓存频繁访问的数据
2. MongoDB存储大型健康记录内容
3. 优化PIR查询算法，减少计算复杂度
4. 分页处理大型查询结果

## 开发指南

如需扩展系统功能，请参考以下模块化结构：

```
app/
├── api/                # API路由和控制器
│   ├── auth.py         # 认证相关接口
│   ├── health_records.py # 健康记录相关接口
│   └── query.py        # 查询相关接口
├── config/             # 配置信息
├── models/             # 数据模型
│   ├── mongo.py        # MongoDB连接和模型
│   ├── mysql.py        # MySQL模型（SQLAlchemy）
│   └── redis.py        # Redis连接和操作
├── pir/                # PIR实现
│   ├── pir_service.py  # PIR服务
│   └── protocol.py     # PIR协议实现
├── services/           # 业务逻辑服务
└── utils/              # 工具函数
    └── security.py     # 安全相关工具
```

## 许可证

MIT License 