"""
系统设置工具模块，用于获取和应用系统设置
"""
from flask import current_app
from ..models.system_settings import SystemSetting

class SettingsCache:
    """系统设置缓存，避免频繁查询数据库，使用纯内存缓存"""
    _instance = None
    _settings = {}
    _last_updated = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = SettingsCache()
        return cls._instance
    
    def clear_cache(self):
        """清除缓存"""
        self._settings = {}
        self._last_updated = None
    
    def get_setting(self, key, default=None):
        """获取设置值"""
        # 如果缓存为空，初始化缓存
        if not self._settings:
            self.refresh_cache()
            
        return self._settings.get(key, default)
    
    def refresh_cache(self):
        """刷新设置缓存"""
        import datetime
        settings = SystemSetting.query.all()
        self._settings = {}
        
        for setting in settings:
            try:
                if setting.value_type == 'json':
                    import json
                    self._settings[setting.key] = json.loads(setting.value)
                elif setting.value_type == 'int':
                    self._settings[setting.key] = int(setting.value)
                elif setting.value_type == 'float':
                    self._settings[setting.key] = float(setting.value)
                elif setting.value_type == 'bool':
                    self._settings[setting.key] = setting.value.lower() in ('true', 'yes', '1')
                else:
                    self._settings[setting.key] = setting.value
            except:
                self._settings[setting.key] = setting.value
        
        self._last_updated = datetime.datetime.now()


def get_setting(key, default=None):
    """获取系统设置，使用内存缓存"""
    return SettingsCache.get_instance().get_setting(key, default)


def apply_settings():
    """
    应用系统设置到应用配置
    此函数应在应用启动时调用，以确保应用配置反映最新的系统设置
    """
    # 刷新缓存
    SettingsCache.get_instance().refresh_cache()
    
    # 应用安全设置
    apply_security_settings()
    
    # 应用PIR设置
    apply_pir_settings()
    
    # 应用系统设置
    apply_system_settings()
    
    # 应用通知设置
    apply_notification_settings()


def apply_security_settings():
    """应用安全相关设置"""
    # 获取设置实例
    settings = SettingsCache.get_instance()
    
    # 应用登录尝试次数限制
    login_attempts = settings.get_setting('login_attempts', 5)
    current_app.config['LOGIN_MAX_ATTEMPTS'] = login_attempts
    
    # 应用会话超时设置
    session_timeout = settings.get_setting('session_timeout', 30)  # 单位：分钟
    current_app.config['PERMANENT_SESSION_LIFETIME'] = session_timeout * 60  # 转换为秒
    
    # 应用密码策略
    password_policy = settings.get_setting('password_policy', {
        'min_length': 6,
        'require_uppercase': False,
        'require_lowercase': False,
        'require_numbers': False,
        'require_special': False
    })
    current_app.config['PASSWORD_POLICY'] = password_policy
    
    # 应用电子邮件确认设置
    require_email_confirmation = settings.get_setting('require_email_confirmation', True)
    current_app.config['REQUIRE_EMAIL_CONFIRMATION'] = require_email_confirmation


def apply_pir_settings():
    """应用PIR相关设置"""
    # 获取设置实例
    settings = SettingsCache.get_instance()
    
    # 应用PIR启用状态
    pir_enabled = settings.get_setting('pir_enabled', True)
    current_app.config['PIR_ENABLE_OBFUSCATION'] = pir_enabled
    
    # 应用PIR批处理大小
    pir_batch_size = settings.get_setting('pir_batch_size', 10)
    current_app.config['PIR_BATCH_SIZE'] = pir_batch_size
    
    # 应用PIR噪声查询数量
    pir_noise_query_count = settings.get_setting('pir_noise_query_count', 3)
    current_app.config['PIR_NOISE_QUERY_COUNT'] = pir_noise_query_count
    
    # 应用默认记录可见性
    default_record_visibility = settings.get_setting('default_record_visibility', 'private')
    current_app.config['DEFAULT_RECORD_VISIBILITY'] = default_record_visibility


def apply_system_settings():
    """应用系统相关设置"""
    # 获取设置实例
    settings = SettingsCache.get_instance()
    
    # 应用调试模式设置
    debug_mode = settings.get_setting('debug_mode', False)
    # 注意：一般不在运行时更改DEBUG设置，这里仅作为示例
    # current_app.config['DEBUG'] = debug_mode
    
    # 应用上传限制
    upload_limit = settings.get_setting('upload_limit', 16 * 1024 * 1024)  # 默认16MB
    current_app.config['MAX_CONTENT_LENGTH'] = upload_limit
    
    # 应用导出大小限制
    max_export_size = settings.get_setting('max_export_size', 1000)
    current_app.config['MAX_EXPORT_SIZE'] = max_export_size


def apply_notification_settings():
    """应用通知相关设置"""
    # 获取设置实例
    settings = SettingsCache.get_instance()
    
    # 应用电子邮件通知设置
    email_notifications = settings.get_setting('email_notifications', True)
    current_app.config['EMAIL_NOTIFICATIONS_ENABLED'] = email_notifications
    
    # 应用系统通知设置
    system_notifications = settings.get_setting('system_notifications', True)
    current_app.config['SYSTEM_NOTIFICATIONS_ENABLED'] = system_notifications
    
    # 应用通知类型设置
    notification_types = settings.get_setting('notification_types', [
        'record_access', 'record_share', 'system_update'
    ])
    current_app.config['ENABLED_NOTIFICATION_TYPES'] = notification_types 