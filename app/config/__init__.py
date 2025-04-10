import os
from dotenv import load_dotenv

# 从.env文件加载环境变量
load_dotenv()

class Config:
    # 基本Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev_key_for_testing')
    
    # MySQL (SQLAlchemy) 配置
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Redis配置
    REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
    REDIS_DB = int(os.environ.get('REDIS_DB', 0))
    REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', None)
    
    # MongoDB配置
    MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/pir_health')
    MONGO_DB_NAME = os.environ.get('MONGO_DB_NAME', 'pir_health')
    
    @staticmethod
    def init_app(app):
        pass

class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DEV_DATABASE_URL') or \
        'mysql+pymysql://root:password@localhost/pir_health_dev'
    
class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('TEST_DATABASE_URL') or \
        'mysql+pymysql://root:password@localhost/pir_health_test'

class ProductionConfig(Config):
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'mysql+pymysql://root:password@localhost/pir_health'

config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
} 