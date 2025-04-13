from . import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
import enum

class Role(enum.Enum):
    PATIENT = "patient"  # 患者
    DOCTOR = "doctor"    # 医生
    RESEARCHER = "researcher"  # 研究人员
    ADMIN = "admin"      # 管理员

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, index=True)
    email = db.Column(db.String(120), unique=True, index=True)
    password_hash = db.Column(db.String(255))
    role = db.Column(db.Enum(Role), default=Role.PATIENT)
    full_name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    avatar = db.Column(db.String(255), default='default.png')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    last_login_at = db.Column(db.DateTime, nullable=True)
    
    # 患者特有信息
    patient_info = db.relationship('PatientInfo', backref='user', uselist=False, lazy='joined', cascade='all, delete-orphan')
    
    # 医生特有信息
    doctor_info = db.relationship('DoctorInfo', backref='user', uselist=False, lazy='joined', cascade='all, delete-orphan')
    
    # 研究人员特有信息
    researcher_info = db.relationship('ResearcherInfo', backref='user', uselist=False, lazy='joined', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<User {self.username}>'
    
    @property
    def password(self):
        raise AttributeError('密码不是可读属性')
        
    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def has_role(self, role):
        if isinstance(role, str):
            return self.role.value == role
        return self.role == role
    
    def update_last_login(self):
        """更新用户的最后登录时间"""
        self.last_login_at = datetime.now()
        db.session.commit()
    
    def to_dict(self):
        data = {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role.value,
            'full_name': self.full_name,
            'phone': self.phone,
            'avatar': self.avatar,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None
        }
        
        # 根据角色添加额外信息
        if self.role == Role.PATIENT and self.patient_info:
            data['patient_info'] = self.patient_info.to_dict()
        elif self.role == Role.DOCTOR and self.doctor_info:
            data['doctor_info'] = self.doctor_info.to_dict()
        elif self.role == Role.RESEARCHER and self.researcher_info:
            data['researcher_info'] = self.researcher_info.to_dict()
            
        return data 