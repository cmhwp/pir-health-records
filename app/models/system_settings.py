from . import db
from datetime import datetime
from enum import Enum

class SystemSetting(db.Model):
    """系统设置模型"""
    __tablename__ = 'system_settings'
    
    # 主键
    id = db.Column(db.Integer, primary_key=True)
    # 键
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    # 值
    value = db.Column(db.Text, nullable=False)
    # 值类型
    value_type = db.Column(db.String(20), nullable=False, default='string')
    # 描述
    description = db.Column(db.String(255))
    
    # 权限控制
    is_public = db.Column(db.Boolean, default=False)
    
    # 创建和修改信息
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    
    def __repr__(self):
        return f'<SystemSetting {self.key}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'value': self.value,
            'value_type': self.value_type,
            'description': self.description,
            'is_public': self.is_public,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @staticmethod
    def get_setting(key, default=None):
        """获取设置值，如果不存在则返回默认值"""
        setting = SystemSetting.query.filter_by(key=key).first()
        if not setting:
            return default
            
        try:
            if setting.value_type == 'json':
                import json
                return json.loads(setting.value)
            elif setting.value_type == 'int':
                return int(setting.value)
            elif setting.value_type == 'float':
                return float(setting.value)
            elif setting.value_type == 'bool':
                return setting.value.lower() in ('true', 'yes', '1')
            else:
                return setting.value
        except:
            return setting.value
    
    @staticmethod
    def set_setting(key, value, value_type=None, description=None, user_id=None):
        """设置配置项，如果不存在则创建"""
        setting = SystemSetting.query.filter_by(key=key).first()
        
        # 确定值类型
        if value_type is None:
            value_type = 'string'
            if isinstance(value, bool):
                value_type = 'bool'
                value = str(value).lower()
            elif isinstance(value, int):
                value_type = 'int'
                value = str(value)
            elif isinstance(value, float):
                value_type = 'float'
                value = str(value)
            elif isinstance(value, (dict, list)):
                value_type = 'json'
                import json
                value = json.dumps(value)
        
        if setting:
            # 更新现有设置
            setting.value = value
            setting.value_type = value_type
            
            if description:
                setting.description = description
                
            if user_id:
                setting.updated_by = user_id
                
            setting.updated_at = datetime.now()
        else:
            # 创建新设置
            setting = SystemSetting(
                key=key,
                value=value,
                value_type=value_type,
                description=description,
                created_by=user_id,
                updated_by=user_id
            )
            db.session.add(setting)
            
        db.session.commit()
        return setting 