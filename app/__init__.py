from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from app.config import config
from app.models.mysql import db
from app.models.mongo import mongo_client
from app.models.redis import redis_client
from app.pir.pir_service import pir_system
import os
from dotenv import load_dotenv

# 确保环境变量已加载
load_dotenv()

# 初始化JWT管理器
jwt = JWTManager()

def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # 初始化扩展
    CORS(app)
    db.init_app(app)
    jwt.init_app(app)
    
    # JWT令牌黑名单检查
    @jwt.token_in_blocklist_loader
    def check_if_token_is_revoked(jwt_header, jwt_payload):
        jti = jwt_payload["jti"]
        return redis_client.is_token_blocked(jti)
    
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
    with app.app_context():
        db.create_all()
    
    return app 