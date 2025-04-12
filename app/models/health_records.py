from . import db
from datetime import datetime
import enum
from sqlalchemy.dialects.mysql import JSON
from bson import ObjectId
from flask import current_app, g
import pymongo
from sqlalchemy import Column, String
from ..utils.mongo_utils import get_mongo_db, format_mongo_doc

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
    """健康记录基本模型 (SQL数据库) - 主要作为MongoDB记录的元数据索引和关系映射"""
    __tablename__ = 'health_records'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    record_type = db.Column(db.Enum(RecordType), nullable=False)  # 记录类型
    title = db.Column(db.String(100), nullable=False)  # 记录标题
    record_date = db.Column(db.DateTime, nullable=False)  # 记录日期
    visibility = db.Column(db.Enum(RecordVisibility), default=RecordVisibility.PRIVATE)  # 可见性
    mongo_id = db.Column(db.String(24), nullable=True, index=True)  # MongoDB中的记录ID，用于关联
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关联表
    files = db.relationship('RecordFile', backref='record', cascade='all, delete-orphan')  # 关联文件
    query_history = db.relationship('QueryHistory', backref='record', cascade='all, delete-orphan')  # 病人查询历史记录
    
    def to_dict(self, include_mongo_data=True):
        """
        转换为字典表示
        
        Args:
            include_mongo_data: 是否包含MongoDB中的详细数据
        """
        result = {
            'id': self.id,
            'patient_id': self.patient_id,
            'record_type': self.record_type.value,
            'title': self.title,
            'record_date': self.record_date.isoformat() if self.record_date else None,
            'visibility': self.visibility.value,
            'files': [file.to_dict() for file in self.files],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'mongo_id': self.mongo_id
        }
        
        # 如果需要包含MongoDB中的详细数据
        if include_mongo_data and self.mongo_id:
            mongo_data = self.get_mongo_data()
            if mongo_data:
                # 移除已有字段，避免重复
                for key in result.keys():
                    if key in mongo_data and key != 'id' and key != 'mongo_id':
                        del mongo_data[key]
                # 合并MongoDB中的详细数据
                result.update(mongo_data)
                
        return result
    
    def get_mongo_data(self):
        """获取MongoDB中的详细记录数据"""
        if not self.mongo_id:
            return None
            
        try:
            mongo_db = get_mongo_db()
            mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(self.mongo_id)})
            if mongo_record:
                return format_mongo_doc(mongo_record)
            return None
        except Exception as e:
            current_app.logger.error(f"获取MongoDB记录失败: {str(e)}")
            return None
    
    @staticmethod
    def from_mongo_doc(mongo_doc):
        """从MongoDB文档创建SQLAlchemy对象 (仅创建索引记录)"""
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
            
        # 处理记录日期
        if 'record_date' in mongo_doc and isinstance(mongo_doc['record_date'], datetime):
            record_date = mongo_doc['record_date']
        else:
            record_date = datetime.now()
            
        # 创建健康记录索引
        record = HealthRecord(
            patient_id=mongo_doc.get('patient_id'),
            record_type=record_type,
            title=mongo_doc.get('title', ''),
            record_date=record_date,
            visibility=visibility,
            mongo_id=str(mongo_doc.get('_id'))
        )
        
        return record
    
    @staticmethod
    def create_with_mongo(record_data, patient_id, file_info=None):
        """
        创建记录，同时存储到MongoDB和MySQL
        
        Args:
            record_data: 记录数据
            patient_id: 患者ID
            file_info: 文件信息
            
        Returns:
            (HealthRecord, mongo_id)
        """
        from ..utils.pir_utils import store_health_record_mongodb
        
        # 存储到MongoDB
        mongo_id = store_health_record_mongodb(record_data, patient_id, file_info)
        
        # 获取MongoDB中的记录
        mongo_db = get_mongo_db()
        mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(mongo_id)})
        
        # 创建MySQL索引记录
        record = HealthRecord.from_mongo_doc(mongo_record)
        
        # 保存到MySQL
        db.session.add(record)
        db.session.commit()
        
        return record, mongo_id
    
    def update_from_mongo(self):
        """从MongoDB同步更新记录"""
        if not self.mongo_id:
            return False
            
        try:
            mongo_db = get_mongo_db()
            mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(self.mongo_id)})
            
            if not mongo_record:
                return False
                
            # 更新基本字段
            try:
                self.record_type = RecordType(mongo_record.get('record_type'))
            except (ValueError, TypeError):
                pass
                
            self.title = mongo_record.get('title', self.title)
            
            if 'record_date' in mongo_record and isinstance(mongo_record['record_date'], datetime):
                self.record_date = mongo_record['record_date']
                
            try:
                self.visibility = RecordVisibility(mongo_record.get('visibility'))
            except (ValueError, TypeError):
                pass
                
            self.updated_at = datetime.now()
            db.session.commit()
            
            return True
        except Exception as e:
            current_app.logger.error(f"从MongoDB同步记录失败: {str(e)}")
            db.session.rollback()
            return False


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
        
    # 创建新的字典而不是修改原始记录
    result = {}
    
    # 将MongoDB记录复制到结果字典
    for key, value in mongo_record.items():
        result[key] = value
    
    # 确保_id是字符串
    if '_id' in result:
        result['_id'] = str(result['_id'])
        
    # 处理日期字段
    for date_field in ['record_date', 'created_at', 'updated_at']:
        if date_field in result and result[date_field] and isinstance(result[date_field], datetime):
            result[date_field] = result[date_field].isoformat()
            
    # 处理嵌套的用药记录
    if 'medication' in result and result['medication']:
        for date_field in ['start_date', 'end_date']:
            if date_field in result['medication'] and result['medication'][date_field] and isinstance(result['medication'][date_field], datetime):
                result['medication'][date_field] = result['medication'][date_field].isoformat()
                
    # 处理生命体征记录
    if 'vital_signs' in result and result['vital_signs']:
        for vital_sign in result['vital_signs']:
            if 'measured_at' in vital_sign and vital_sign['measured_at'] and isinstance(vital_sign['measured_at'], datetime):
                vital_sign['measured_at'] = vital_sign['measured_at'].isoformat()
                
    return result

# 添加缓存装饰器
def cached_mongo_record(timeout=300):
    """MongoDB记录查询缓存装饰器"""
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def wrapper(record_id, *args, **kwargs):
            # 使用Flask缓存
            cache_key = f"mongo_record_{record_id}"
            cached = current_app.cache.get(cache_key) if hasattr(current_app, 'cache') else None
            
            if cached:
                return cached
                
            result = f(record_id, *args, **kwargs)
            
            if result and hasattr(current_app, 'cache'):
                current_app.cache.set(cache_key, result, timeout=timeout)
                
            return result
        return wrapper
    return decorator

@cached_mongo_record(timeout=300)
def get_mongo_health_record(record_id):
    """
    获取MongoDB中的健康记录（带缓存）
    
    Args:
        record_id: MongoDB中的记录ID
    
    Returns:
        记录字典
    """
    try:
        mongo_db = get_mongo_db()
        mongo_id = format_mongo_id(record_id)
        if not mongo_id:
            return None
            
        record = mongo_db.health_records.find_one({'_id': mongo_id})
        return mongo_health_record_to_dict(record)
    except Exception as e:
        current_app.logger.error(f"获取MongoDB记录失败: {str(e)}")
        return None

# =========================== 记录共享功能 ===========================

class SharePermission(enum.Enum):
    VIEW = "view"         # 仅查看权限
    ANNOTATE = "annotate" # 允许添加注释
    FULL = "full"         # 完全访问（可下载文件等）

class SharedRecord(db.Model):
    """健康记录共享 (SQL数据库)"""
    __tablename__ = 'shared_records'
    
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey('health_records.id'), nullable=False)  # 关联MySQL中的记录ID
    mongo_record_id = db.Column(db.String(24), nullable=True)  # MongoDB中的记录ID
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 记录所有者
    shared_with = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # 共享给的用户
    permission = db.Column(db.Enum(SharePermission), default=SharePermission.VIEW)  # 共享权限
    created_at = db.Column(db.DateTime, default=datetime.now)
    expires_at = db.Column(db.DateTime, nullable=True)  # 共享过期时间，NULL表示永不过期
    access_count = db.Column(db.Integer, default=0)  # 访问计数
    last_accessed = db.Column(db.DateTime, nullable=True)  # 最后访问时间
    access_key = db.Column(db.String(100), nullable=False)  # 访问密钥，用于验证
    
    # 关系
    health_record = db.relationship('HealthRecord', foreign_keys=[record_id], backref='shared_records')
    
    def to_dict(self):
        return {
            'id': self.id,
            'record_id': self.record_id,
            'mongo_record_id': self.mongo_record_id,
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

# =========================== MongoDB批量操作与同步工具 ===========================

def sync_records_from_mongodb(patient_id=None, limit=100):
    """
    将MongoDB中的健康记录同步到MySQL数据库（仅索引信息）
    
    Args:
        patient_id: 如果指定，则只同步该患者的记录
        limit: 最大同步记录数
        
    Returns:
        同步的记录数
    """
    try:
        mongo_db = get_mongo_db()
        # 查询条件
        query = {}
        if patient_id:
            query['patient_id'] = patient_id
            
        # 查询MongoDB中的记录
        mongo_records = list(mongo_db.health_records.find(query).limit(limit))
        
        sync_count = 0
        for mongo_record in mongo_records:
            # 检查记录是否已存在于MySQL
            existing = HealthRecord.query.filter_by(mongo_id=str(mongo_record['_id'])).first()
            
            if existing:
                # 已存在，更新
                existing.update_from_mongo()
                sync_count += 1
            else:
                # 不存在，创建新记录
                new_record = HealthRecord.from_mongo_doc(mongo_record)
                if new_record:
                    db.session.add(new_record)
                    sync_count += 1
        
        db.session.commit()
        return sync_count
    except Exception as e:
        current_app.logger.error(f"同步记录失败: {str(e)}")
        db.session.rollback()
        return 0

def batch_get_mongo_records(mongo_ids):
    """
    批量获取MongoDB中的记录
    
    Args:
        mongo_ids: MongoDB记录ID列表
        
    Returns:
        记录字典的列表
    """
    if not mongo_ids:
        return []
        
    try:
        mongo_db = get_mongo_db()
        object_ids = [format_mongo_id(id) for id in mongo_ids if format_mongo_id(id)]
        
        if not object_ids:
            return []
            
        # 批量查询
        records = list(mongo_db.health_records.find({'_id': {'$in': object_ids}}))
        
        # 转换为字典
        return [mongo_health_record_to_dict(record) for record in records]
    except Exception as e:
        current_app.logger.error(f"批量获取MongoDB记录失败: {str(e)}")
        return []

def bulk_update_visibility(mongo_ids, visibility, patient_id=None):
    """
    批量更新记录可见性
    
    Args:
        mongo_ids: MongoDB记录ID列表
        visibility: 新的可见性设置
        patient_id: 如果指定，则只更新该患者的记录（安全检查）
        
    Returns:
        更新的记录数
    """
    if not mongo_ids or not visibility:
        return 0
        
    try:
        # 验证可见性值
        try:
            visibility_enum = RecordVisibility(visibility)
        except ValueError:
            return 0
        
        mongo_db = get_mongo_db()
        object_ids = [format_mongo_id(id) for id in mongo_ids if format_mongo_id(id)]
        
        if not object_ids:
            return 0
            
        # 构建查询条件
        query = {'_id': {'$in': object_ids}}
        if patient_id:
            query['patient_id'] = patient_id
            
        # 构建更新内容
        update = {
            '$set': {
                'visibility': visibility_enum.value,
                'updated_at': datetime.now()
            }
        }
        
        # 执行批量更新
        result = mongo_db.health_records.update_many(query, update)
        
        # 同步更新MySQL中的记录
        if result.modified_count > 0:
            for mongo_id in mongo_ids:
                record = HealthRecord.query.filter_by(mongo_id=mongo_id).first()
                if record:
                    try:
                        record.visibility = visibility_enum
                        record.updated_at = datetime.now()
                    except:
                        pass
            
            db.session.commit()
        
        return result.modified_count
    except Exception as e:
        current_app.logger.error(f"批量更新记录可见性失败: {str(e)}")
        db.session.rollback()
        return 0 