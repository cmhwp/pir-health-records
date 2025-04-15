from flask import Flask
from flask_cors import CORS
from .config.config import config
from .models import db, login_manager
from .utils.mongo_utils import init_mongo, mongo
from .utils.redis_utils import init_redis, close_redis
from .utils.jwt_utils import init_jwt_loader

def create_app(config_name="development"):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # 设置系统版本
    app.config['SYSTEM_VERSION'] = '1.0.0'
    
    # 初始化扩展
    db.init_app(app)
    login_manager.init_app(app)
    init_mongo(app)
    init_redis(app)
    CORS(app)
    
    # 初始化JWT认证
    init_jwt_loader(app)
    
    # 注册销毁函数
    app.teardown_appcontext(close_redis)
    
    # 创建SQLAlchemy数据库表
    with app.app_context():
        db.create_all()
        
        # 初始化默认管理员账户（如果不存在）
        init_default_admin(app)
        
        # 应用系统设置
        init_system_settings(app)
    
    # 注册蓝图
    from .routers.main import main_bp
    from .routers.auth import auth_bp
    from .routers.admin import admin_bp
    from .routers.health_records import health_bp
    from .routers.notifications import notifications_bp
    from .routers.doctor import doctor_bp
    from .routers.patient import patient_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(doctor_bp)
    app.register_blueprint(patient_bp)
    
    # 确保上传目录存在
    import os
    os.makedirs(os.path.join(app.root_path, 'uploads', 'avatars'), exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'uploads', 'records'), exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'uploads', 'exports'), exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'uploads', 'imports'), exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'uploads', 'encrypted_records'), exist_ok=True)
    
    return app

def init_default_admin(app):
    """初始化默认管理员账户"""
    from .models.user import User, Role
    
    # 检查是否已存在管理员账户
    admin_exists = User.query.filter_by(role=Role.ADMIN).first() is not None
    
    if not admin_exists:
        # 创建默认管理员账户
        admin_username = app.config.get('DEFAULT_ADMIN_USERNAME', 'admin')
        admin_password = app.config.get('DEFAULT_ADMIN_PASSWORD', 'admin123456')
        admin_email = app.config.get('DEFAULT_ADMIN_EMAIL', 'admin@example.com')
        
        admin = User(
            username=admin_username,
            email=admin_email,
            password=admin_password,  # 会自动哈希
            full_name='系统管理员',
            role=Role.ADMIN,
            is_active=True
        )
        
        try:
            db.session.add(admin)
            db.session.commit()
            app.logger.info(f"已创建默认管理员账户: {admin_username}")
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"创建默认管理员失败: {str(e)}")

def init_system_settings(app):
    """初始化系统设置并应用到应用配置"""
    with app.app_context():
        try:
            # 导入这里是为了避免循环导入
            from .utils.settings_utils import apply_settings, SettingsCache
            from .models.system_settings import SystemSetting
            
            # 检查是否需要创建默认系统设置
            settings_count = SystemSetting.query.count()
            
            # 如果没有设置记录，创建默认设置
            if settings_count == 0:
                app.logger.info("创建默认系统设置...")
                create_default_settings()
                
            # 应用所有设置到应用配置
            apply_settings()
            app.logger.info("系统设置已应用")
            
        except Exception as e:
            app.logger.error(f"初始化系统设置失败: {str(e)}")

def create_default_settings():
    """创建默认系统设置"""
    from .models.system_settings import SystemSetting
    from .models import db
    
    # 安全设置
    security_settings = [
        {
            'key': 'password_policy',
            'value': '{"min_length": 6, "require_uppercase": false, "require_lowercase": false, "require_numbers": false, "require_special": false}',
            'value_type': 'json',
            'description': '密码策略设置',
            'is_public': True  # 密码策略是公开的，前端需要
        },
        {
            'key': 'login_attempts',
            'value': '5',
            'value_type': 'int',
            'description': '最大登录尝试次数',
            'is_public': False
        },
        {
            'key': 'session_timeout',
            'value': '30',
            'value_type': 'int',
            'description': '会话超时时间(分钟)',
            'is_public': False
        },
        {
            'key': 'require_email_confirmation',
            'value': 'true',
            'value_type': 'bool',
            'description': '是否需要邮箱确认',
            'is_public': True  # 注册页面需要展示
        }
    ]
    
    # 隐私设置
    privacy_settings = [
        {
            'key': 'pir_enabled',
            'value': 'true',
            'value_type': 'bool',
            'description': '是否启用PIR隐私保护',
            'is_public': True  # 公开隐私保护特性
        },
        {
            'key': 'pir_batch_size',
            'value': '10',
            'value_type': 'int',
            'description': 'PIR批处理大小',
            'is_public': False
        },
        {
            'key': 'pir_noise_query_count',
            'value': '3',
            'value_type': 'int',
            'description': 'PIR噪声查询数量',
            'is_public': False
        },
        {
            'key': 'default_record_visibility',
            'value': 'private',
            'value_type': 'string',
            'description': '默认记录可见性',
            'is_public': True  # 公开默认隐私政策
        },
        {
            'key': 'encryption_enabled',
            'value': 'true',
            'value_type': 'bool',
            'description': '是否启用记录加密功能',
            'is_public': True  # 公开加密特性
        },
        {
            'key': 'encryption_algorithm',
            'value': 'AES-GCM-256',
            'value_type': 'string',
            'description': '默认加密算法',
            'is_public': True
        },
        {
            'key': 'require_integrity_verification',
            'value': 'true',
            'value_type': 'bool',
            'description': '是否要求验证记录完整性',
            'is_public': True
        }
    ]
    
    # 系统设置
    system_settings = [
        {
            'key': 'debug_mode',
            'value': 'false',
            'value_type': 'bool',
            'description': '是否启用调试模式',
            'is_public': False
        },
        {
            'key': 'upload_limit',
            'value': str(16 * 1024 * 1024),  # 16MB
            'value_type': 'int',
            'description': '上传文件大小限制(字节)',
            'is_public': True  # 公开上传限制
        },
        {
            'key': 'max_export_size',
            'value': '1000',
            'value_type': 'int',
            'description': '最大导出记录数',
            'is_public': False
        },
        {
            'key': 'allow_researcher_registration',
            'value': 'false',
            'value_type': 'bool',
            'description': '是否允许研究人员直接注册',
            'is_public': True  # 公开注册选项
        },
        {
            'key': 'registration_enabled',
            'value': 'true',
            'value_type': 'bool',
            'description': '是否开放注册',
            'is_public': True  # 公开注册状态
        }
    ]
    
    # 通知设置
    notification_settings = [
        {
            'key': 'email_notifications',
            'value': 'true',
            'value_type': 'bool',
            'description': '是否启用邮件通知',
            'is_public': False
        },
        {
            'key': 'system_notifications',
            'value': 'true',
            'value_type': 'bool',
            'description': '是否启用系统通知',
            'is_public': False
        },
        {
            'key': 'notification_types',
            'value': '["record_access", "record_share", "system_update"]',
            'value_type': 'json',
            'description': '启用的通知类型',
            'is_public': False
        }
    ]
    
    # 合并所有设置
    all_settings = security_settings + privacy_settings + system_settings + notification_settings
    
    # 创建设置记录
    for setting_data in all_settings:
        setting = SystemSetting(
            key=setting_data['key'],
            value=setting_data['value'],
            value_type=setting_data['value_type'],
            description=setting_data['description'],
            is_public=setting_data['is_public']
        )
        db.session.add(setting)
    
    # 提交所有设置
    db.session.commit() 