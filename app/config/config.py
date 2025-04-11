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
    # MySQL
    MYSQL_DATABASE_URI = os.environ.get('DEV_MYSQL_URL') or \
        'mysql://root:password@localhost/flask_dev'
    # MongoDB 开发数据库
    MONGO_URI = os.environ.get('DEV_MONGO_URI') or 'mongodb://localhost:27017/flask_dev'

class TestingConfig(Config):
    TESTING = True
    # MySQL
    MYSQL_DATABASE_URI = os.environ.get('TEST_MYSQL_URL') or \
        'mysql://root:password@localhost/flask_test'
    # MongoDB 测试数据库
    MONGO_URI = os.environ.get('TEST_MONGO_URI') or 'mongodb://localhost:27017/flask_test'

class ProductionConfig(Config):
    # MySQL
    MYSQL_DATABASE_URI = os.environ.get('MYSQL_URL') or \
        'mysql://user:password@localhost/flask_prod'
    # MongoDB 生产数据库
    MONGO_URI = os.environ.get('MONGO_URI') or 'mongodb://localhost:27017/flask_prod'

config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
} 