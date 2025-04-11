from flask import Flask
from flask_cors import CORS
from .config.config import config
from .models import db, login_manager
from .utils.mongo_utils import init_mongo, mongo
from .utils.redis_utils import init_redis, close_redis

def create_app(config_name="development"):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # 初始化扩展
    db.init_app(app)
    login_manager.init_app(app)
    init_mongo(app)
    init_redis(app)
    CORS(app)
    
    # 注册销毁函数
    app.teardown_appcontext(close_redis)
    
    # 创建SQLAlchemy数据库表
    with app.app_context():
        db.create_all()
    
    # 注册蓝图
    from .routers.main import main_bp
    from .routers.auth import auth_bp
    from .routers.admin import admin_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    
    return app 