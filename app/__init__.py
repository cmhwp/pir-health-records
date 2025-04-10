from flask import Flask
from flask_cors import CORS
from app.config import config
from app.models.mysql import db
from app.models.mongo import mongo_client
from app.models.redis import redis_client
from app.pir.pir_service import pir_system

def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # 初始化扩展
    CORS(app)
    db.init_app(app)
    
    # 连接Redis和MongoDB
    with app.app_context():
        redis_client.init_app(app)
        mongo_client.init_app(app)
    
    # 注册蓝图
    from app.api.auth import auth_bp
    from app.api.health_records import health_records_bp
    from app.api.query import query_bp
    
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(health_records_bp, url_prefix='/api/health-records')
    app.register_blueprint(query_bp, url_prefix='/api/query')
    
    # 创建数据库表
    @app.before_first_request
    def create_tables():
        db.create_all()
    
    return app 