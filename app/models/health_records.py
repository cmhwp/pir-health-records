from . import db
from datetime import datetime
import enum
from sqlalchemy.dialects.mysql import JSON
from bson import ObjectId
from flask import current_app

class RecordType(enum.Enum):
    MEDICAL_HISTORY = "medical_history"  # 病历
    EXAMINATION = "examination"          # 检查报告
    MEDICATION = "medication"            # 用药记录
    VITAL_SIGNS = "vital_signs"          # 生命体征
    TREATMENT = "treatment"              # 治疗记录
    SURGERY = "surgery"                  # 手术记录
    OTHER = "other"                      # 其他

class RecordVisibility(enum.Enum):
    PRIVATE = "private"        # 仅患者可见
    DOCTOR = "doctor"          # 医生可见
    RESEARCHER = "researcher"  # 研究人员可见
    PUBLIC = "public"          # 公开

# =========================== SQLAlchemy模型 ===========================

class HealthRecord(db.Model):
    """健康记录基本模型 (SQL数据库)"""
    __tablename__ = 'health_records'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    record_type = db.Column(db.Enum(RecordType), nullable=False)  # 记录类型
    title = db.Column(db.String(100), nullable=False)  # 记录标题
    description = db.Column(db.Text, nullable=True)  # 描述
    record_date = db.Column(db.DateTime, nullable=False)  # 记录日期
    institution = db.Column(db.String(100), nullable=True)  # 医疗机构
    doctor_name = db.Column(db.String(50), nullable=True)  # 医生姓名
    visibility = db.Column(db.Enum(RecordVisibility), default=RecordVisibility.PRIVATE)  # 可见性
    tags = db.Column(db.String(200), nullable=True)  # 标签，用逗号分隔
    data = db.Column(JSON, nullable=True)  # 其他数据，存储为JSON
    files = db.relationship('RecordFile', backref='record', cascade='all, delete-orphan')  # 关联文件
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 病人查询历史记录，用于匿名查询分析
    query_history = db.relationship('QueryHistory', backref='record', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'patient_id': self.patient_id,
            'record_type': self.record_type.value,
            'title': self.title,
            'description': self.description,
            'record_date': self.record_date.isoformat() if self.record_date else None,
            'institution': self.institution,
            'doctor_name': self.doctor_name,
            'visibility': self.visibility.value,
            'tags': self.tags,
            'data': self.data,
            'files': [file.to_dict() for file in self.files],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @staticmethod
    def from_mongo_doc(mongo_doc):
        """从MongoDB文档创建SQLAlchemy对象"""
        if not mongo_doc:
            return None
            
        # 转换记录类型和可见性
        try:
            record_type = RecordType(mongo_doc.get('record_type'))
        except (ValueError, TypeError):
            record_type = RecordType.OTHER
            
        try:
            visibility = RecordVisibility(mongo_doc.get('visibility'))
        except (ValueError, TypeError):
            visibility = RecordVisibility.PRIVATE
            
        # 创建健康记录
        record = HealthRecord(
            patient_id=mongo_doc.get('patient_id'),
            record_type=record_type,
            title=mongo_doc.get('title', ''),
            description=mongo_doc.get('description'),
            record_date=mongo_doc.get('record_date') if isinstance(mongo_doc.get('record_date'), datetime) else datetime.now(),
            institution=mongo_doc.get('institution'),
            doctor_name=mongo_doc.get('doctor_name'),
            visibility=visibility,
            tags=mongo_doc.get('tags'),
            data=mongo_doc.get('data')
        )
        
        # 设置MongoDB ID作为外部参考
        record.mongo_id = str(mongo_doc.get('_id'))
        
        return record


class RecordFile(db.Model):
    """记录相关文件 (SQL数据库)"""
    __tablename__ = 'record_files'
    
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey('health_records.id'), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)  # 文件名
    file_path = db.Column(db.String(255), nullable=False)  # 文件路径
    file_type = db.Column(db.String(50), nullable=False)  # 文件类型
    file_size = db.Column(db.Integer, nullable=False)  # 文件大小（字节）
    description = db.Column(db.String(255), nullable=True)  # 文件描述
    uploaded_at = db.Column(db.DateTime, default=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'record_id': self.record_id,
            'file_name': self.file_name,
            'file_type': self.file_type,
            'file_size': self.file_size,
            'description': self.description,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None
        }


class MedicationRecord(db.Model):
    """用药记录，与健康记录关联 (SQL数据库)"""
    __tablename__ = 'medication_records'
    
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey('health_records.id'), nullable=False)
    medication_name = db.Column(db.String(100), nullable=False)  # 药品名称
    dosage = db.Column(db.String(50), nullable=True)  # 剂量
    frequency = db.Column(db.String(50), nullable=True)  # 频率
    start_date = db.Column(db.Date, nullable=True)  # 开始日期
    end_date = db.Column(db.Date, nullable=True)  # 结束日期
    instructions = db.Column(db.Text, nullable=True)  # 用药说明
    side_effects = db.Column(db.Text, nullable=True)  # 副作用
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'record_id': self.record_id,
            'medication_name': self.medication_name,
            'dosage': self.dosage,
            'frequency': self.frequency,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'instructions': self.instructions,
            'side_effects': self.side_effects,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class VitalSign(db.Model):
    """生命体征记录 (SQL数据库)"""
    __tablename__ = 'vital_signs'
    
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey('health_records.id'), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # 类型（血压、体温、心率等）
    value = db.Column(db.Float, nullable=False)  # 值
    unit = db.Column(db.String(20), nullable=True)  # 单位
    measured_at = db.Column(db.DateTime, nullable=False)  # 测量时间
    notes = db.Column(db.Text, nullable=True)  # 备注
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'record_id': self.record_id,
            'type': self.type,
            'value': self.value,
            'unit': self.unit,
            'measured_at': self.measured_at.isoformat() if self.measured_at else None,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class QueryHistory(db.Model):
    """查询历史，用于匿名查询分析 (SQL数据库)"""
    __tablename__ = 'query_history'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 查询用户
    record_id = db.Column(db.Integer, db.ForeignKey('health_records.id'), nullable=True)  # 查询的记录
    query_type = db.Column(db.String(50), nullable=False)  # 查询类型
    query_params = db.Column(JSON, nullable=True)  # 查询参数
    is_anonymous = db.Column(db.Boolean, default=False)  # 是否匿名查询
    query_time = db.Column(db.DateTime, default=datetime.now)  # 查询时间
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'record_id': self.record_id,
            'query_type': self.query_type,
            'query_params': self.query_params,
            'is_anonymous': self.is_anonymous,
            'query_time': self.query_time.isoformat() if self.query_time else None
        }


# =========================== MongoDB相关功能 ===========================

def mongo_health_record_to_dict(mongo_record):
    """将MongoDB中的健康记录转换为字典格式"""
    if not mongo_record:
        return None
        
    # 确保_id是字符串
    if '_id' in mongo_record:
        mongo_record['_id'] = str(mongo_record['_id'])
        
    # 处理日期字段
    for date_field in ['record_date', 'created_at', 'updated_at']:
        if date_field in mongo_record and mongo_record[date_field] and isinstance(mongo_record[date_field], datetime):
            mongo_record[date_field] = mongo_record[date_field].isoformat()
            
    # 处理嵌套的用药记录
    if 'medication' in mongo_record and mongo_record['medication']:
        for date_field in ['start_date', 'end_date']:
            if date_field in mongo_record['medication'] and mongo_record['medication'][date_field] and isinstance(mongo_record['medication'][date_field], datetime):
                mongo_record['medication'][date_field] = mongo_record['medication'][date_field].isoformat()
                
    # 处理生命体征记录
    if 'vital_signs' in mongo_record and mongo_record['vital_signs']:
        for vital_sign in mongo_record['vital_signs']:
            if 'measured_at' in vital_sign and vital_sign['measured_at'] and isinstance(vital_sign['measured_at'], datetime):
                vital_sign['measured_at'] = vital_sign['measured_at'].isoformat()
                
    return mongo_record

# =========================== 记录共享功能 ===========================

class SharePermission(enum.Enum):
    VIEW = "view"         # 仅查看权限
    ANNOTATE = "annotate" # 允许添加注释
    FULL = "full"         # 完全访问（可下载文件等）

class SharedRecord(db.Model):
    """健康记录共享 (SQL数据库)"""
    __tablename__ = 'shared_records'
    
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.String(50), nullable=False)  # MongoDB中的记录ID
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 记录所有者
    shared_with = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 共享给的用户
    permission = db.Column(db.Enum(SharePermission), default=SharePermission.VIEW)  # 共享权限
    created_at = db.Column(db.DateTime, default=datetime.now)
    expires_at = db.Column(db.DateTime, nullable=True)  # 共享过期时间，NULL表示永不过期
    access_count = db.Column(db.Integer, default=0)  # 访问计数
    last_accessed = db.Column(db.DateTime, nullable=True)  # 最后访问时间
    access_key = db.Column(db.String(100), nullable=False)  # 访问密钥，用于验证
    
    def to_dict(self):
        return {
            'id': self.id,
            'record_id': self.record_id,
            'owner_id': self.owner_id,
            'shared_with': self.shared_with,
            'permission': self.permission.value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'access_count': self.access_count,
            'last_accessed': self.last_accessed.isoformat() if self.last_accessed else None
        }
    
    def is_valid(self):
        """检查共享是否有效（未过期）"""
        if not self.expires_at:
            return True
        return datetime.now() < self.expires_at
    
    def record_access(self):
        """记录一次访问"""
        self.access_count += 1
        self.last_accessed = datetime.now()

def format_mongo_id(id_value):
    """将字符串ID格式化为ObjectId"""
    if isinstance(id_value, str):
        try:
            return ObjectId(id_value)
        except:
            current_app.logger.error(f"Invalid ObjectId: {id_value}")
            return None
    return id_value 