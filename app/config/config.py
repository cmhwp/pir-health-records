import os
import secrets
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 基础配置
    SECRET_KEY = os.environ.get('SECRET_KEY') or secrets.token_hex(16)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # MongoDB配置
    MONGO_URI = os.environ.get('MONGO_URI') or 'mongodb://localhost:27017/pir_health_records'
    
    # Redis配置(未使用)
    REDIS_URL = os.environ.get('REDIS_URL') or 'redis://localhost:6379/0'
    
    # PIR配置
    PIR_ENABLE_OBFUSCATION = True  # 启用查询混淆
    PIR_NOISE_QUERY_COUNT = 3      # 噪声查询数量
    
    # JWT配置(登录认证)
    JWT_EXPIRATION_DELTA = 24 * 60 * 60  # 24小时
    
    # 上传文件配置(限制上传文件大小)
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    
    # 默认管理员账户配置
    DEFAULT_ADMIN_USERNAME = os.environ.get('DEFAULT_ADMIN_USERNAME') or 'admin'
    DEFAULT_ADMIN_PASSWORD = os.environ.get('DEFAULT_ADMIN_PASSWORD') or 'admin123456'
    DEFAULT_ADMIN_EMAIL = os.environ.get('DEFAULT_ADMIN_EMAIL') or 'admin@example.com'

class DevelopmentConfig(Config):
    DEBUG = True
    # MySQL 数据库
    SQLALCHEMY_DATABASE_URI = os.environ.get('DEV_MYSQL_URL') or \
        'mysql://root:123456@localhost/pir_health_dev'
    
    # 开发环境MongoDB
    MONGO_URI = os.environ.get('DEV_MONGO_URI') or 'mongodb://localhost:27017/pir_health_records_dev'

class TestingConfig(Config):
    TESTING = True
    # MySQL 数据库
    SQLALCHEMY_DATABASE_URI = os.environ.get('TEST_MYSQL_URL') or \
        'mysql://root:123456@localhost/pir_health_test'
    
    # 测试环境MongoDB
    MONGO_URI = os.environ.get('TEST_MONGO_URI') or 'mongodb://localhost:27017/pir_health_records_test'
    
    # 禁用CSRF保护，方便测试
    WTF_CSRF_ENABLED = False

class ProductionConfig(Config):
    # MySQL 数据库
    SQLALCHEMY_DATABASE_URI = os.environ.get('MYSQL_URL') or \
        'mysql://root:123456@localhost/pir_health'
    
    # 生产环境MongoDB
    MONGO_URI = os.environ.get('MONGO_URI') or 'mongodb://localhost:27017/pir_health_records_prod'
    
    # 生产环境安全设置
    SSL_REDIRECT = True

config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
} 