from . import db
from datetime import datetime

class Institution(db.Model):
    """医疗机构模型"""
    __tablename__ = 'institutions'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)  # 机构名称
    code = db.Column(db.String(50), nullable=True)  # 机构代码
    address = db.Column(db.String(200), nullable=True)  # 地址
    phone = db.Column(db.String(20), nullable=True)  # 联系电话
    email = db.Column(db.String(100), nullable=True)  # 联系邮箱
    website = db.Column(db.String(100), nullable=True)  # 网站
    description = db.Column(db.Text, nullable=True)  # 描述
    logo_url = db.Column(db.String(255), nullable=True)  # Logo URL
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'code': self.code,
            'address': self.address,
            'phone': self.phone,
            'email': self.email,
            'website': self.website,
            'description': self.description,
            'logo_url': self.logo_url,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class CustomRecordType(db.Model):
    """自定义记录类型模型"""
    __tablename__ = 'custom_record_types'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, unique=True)  # 类型代码
    name = db.Column(db.String(100), nullable=False)  # 类型名称
    description = db.Column(db.Text, nullable=True)  # 描述
    color = db.Column(db.String(20), nullable=True)  # 显示颜色
    icon = db.Column(db.String(50), nullable=True)  # 图标
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'description': self.description,
            'color': self.color,
            'icon': self.icon,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        } 