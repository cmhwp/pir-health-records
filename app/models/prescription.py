from enum import Enum
from datetime import datetime
from . import db

class PrescriptionStatus(Enum):
    PENDING = "PENDING"      # 待确认/处理
    ACTIVE = "ACTIVE"        # 已激活/有效
    COMPLETED = "COMPLETED"  # 已完成/已使用
    EXPIRED = "EXPIRED"      # 已过期
    REVOKED = "REVOKED"      # 已撤销/拒绝

class Prescription(db.Model):
    __tablename__ = 'prescriptions'
    # 主键
    id = db.Column(db.Integer, primary_key=True)
    # 患者ID
    patient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # 医生ID
    doctor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # 患者症状描述
    symptoms = db.Column(db.Text, nullable=True)
    # 诊断
    diagnosis = db.Column(db.String(500), nullable=False)
    # 用药说明
    instructions = db.Column(db.Text)
    # 状态
    status = db.Column(db.Enum(PrescriptionStatus), default=PrescriptionStatus.ACTIVE)
    # 有效期
    valid_until = db.Column(db.DateTime)
    # 创建时间
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # Relationships
    items = db.relationship('PrescriptionItem', backref='prescription', lazy=True, cascade="all, delete-orphan")
    
    def __repr__(self):
        return f'<Prescription {self.id}>'

class PrescriptionItem(db.Model):
    __tablename__ = 'prescription_items'
    # 主键
    id = db.Column(db.Integer, primary_key=True)
    # 处方ID
    prescription_id = db.Column(db.Integer, db.ForeignKey('prescriptions.id'), nullable=False)
    # 药品名称
    medicine_name = db.Column(db.String(255), nullable=False)
    # 剂量
    dosage = db.Column(db.String(100), nullable=False)
    # 频率  
    frequency = db.Column(db.String(100))
    # 疗程
    duration = db.Column(db.String(100))
    # 备注
    notes = db.Column(db.Text)
    # 创建时间
    created_at = db.Column(db.DateTime, default=datetime.now)
    # 更新时间
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def __repr__(self):
        return f'<PrescriptionItem {self.id}>' 