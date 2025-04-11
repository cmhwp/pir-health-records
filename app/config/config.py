import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'hard-to-guess-string'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # MongoDB 配置
    MONGO_URI = os.environ.get('MONGO_URI') or 'mongodb://localhost:27017/flask_app'
    
    # Redis 配置
    REDIS_URL = os.environ.get('REDIS_URL') or 'redis://localhost:6379/0'
    CACHE_TYPE = 'redis'
    CACHE_REDIS_URL = os.environ.get('REDIS_URL') or 'redis://localhost:6379/0'
    CACHE_DEFAULT_TIMEOUT = 300

class DevelopmentConfig(Config):
    DEBUG = True
    # SQLite 或 MySQL
    SQLALCHEMY_DATABASE_URI = os.environ.get('DEV_DATABASE_URL') or os.environ.get('DEV_MYSQL_URL') or \
        'sqlite:///pir_health.db'
    # MongoDB 开发数据库
    MONGO_URI = os.environ.get('DEV_MONGO_URI') or 'mongodb://localhost:27017/pir_health'

class TestingConfig(Config):
    TESTING = True
    # SQLite 或 MySQL
    SQLALCHEMY_DATABASE_URI = os.environ.get('TEST_DATABASE_URL') or os.environ.get('TEST_MYSQL_URL') or \
        'sqlite:///pir_health_test.db'
    # MongoDB 测试数据库
    MONGO_URI = os.environ.get('TEST_MONGO_URI') or 'mongodb://localhost:27017/pir_health_test'

class ProductionConfig(Config):
    # SQLite 或 MySQL
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or os.environ.get('MYSQL_URL') or \
        'sqlite:///pir_health.db'
    # MongoDB 生产数据库
    MONGO_URI = os.environ.get('MONGO_URI') or 'mongodb://localhost:27017/pir_health'

config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
} 