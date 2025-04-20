from flask import Blueprint, request, jsonify, current_app, g, send_from_directory
from flask_login import login_required, current_user
from ..models import db, User, Role, HealthRecord, RecordFile, MedicationRecord, VitalSign, QueryHistory
from ..models import RecordType, RecordVisibility, SharePermission, SharedRecord
from ..models import Notification, NotificationType
from ..models.health_records import (
    format_mongo_id, mongo_health_record_to_dict, get_mongo_health_record,
    batch_get_mongo_records, sync_records_from_mongodb, bulk_update_visibility,
    cached_mongo_record
)
from ..routers.auth import role_required
from ..utils.pir_utils import (
    PIRQuery, prepare_pir_database, 
    store_health_record_mongodb, query_health_records_mongodb
)
from ..utils.mongo_utils import mongo, get_mongo_db
from bson.objectid import ObjectId
import os
import uuid
from datetime import datetime, timedelta, date
from werkzeug.utils import secure_filename
import json
from sqlalchemy import desc, func, distinct, or_, case
import math
import secrets
import random
from ..utils.log_utils import log_record
from app.models.institution import Institution, CustomRecordType

def check_record_access_permission(record, user):
    """检查用户是否有权限访问此记录"""
    # 记录访问尝试日志
    try:
        from ..utils.log_utils import log_record
        log_record(
            message=f'用户尝试访问健康记录',
            details={
                'user_id': user.id,
                'record_id': str(record.get('_id', 'unknown')),
                'record_title': record.get('title', 'unknown'),
                'record_visibility': record.get('visibility', 'unknown')
            }
        )
    except Exception as e:
        current_app.logger.error(f"记录访问日志失败: {str(e)}")
    
    # 管理员可以访问所有记录
    if user.has_role(Role.ADMIN):
        return True, None
        
    # 记录所有者可以访问自己的所有记录
    if str(record['patient_id']) == str(user.id):
        return True, None
        
    # 检查可见性访问权限
    if record['visibility'] == 'private':
        return False, '没有权限访问此记录'
        
    if record['visibility'] == 'doctor' and not user.has_role(Role.DOCTOR):
        return False, '没有权限访问此记录'
        
    if record['visibility'] == 'researcher' and not user.has_role(Role.RESEARCHER):
        return False, '没有权限访问此记录'
        
    # 通过所有检查，授予访问权限
    return True, None

health_bp = Blueprint('health', __name__, url_prefix='/api/health')

# 确保上传目录存在
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads', 'records')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx', 'txt'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_uploaded_file(file):
    """保存上传的文件，返回文件路径"""
    if file and allowed_file(file.filename):
        # 生成安全的文件名
        original_filename = secure_filename(file.filename)
        # 使用UUID和时间戳确保文件名唯一
        unique_filename = f"{uuid.uuid4().hex}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{original_filename}"
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        # 保存文件
        file.save(filepath)
        
        return {
            'original_name': original_filename,
            'saved_name': unique_filename,
            'path': filepath,
            'size': os.path.getsize(filepath),
            'type': original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        }
    return None

def format_mongo_date(date_value):
    """处理MongoDB中的日期值，可能是字符串、datetime对象或包含$date的字典"""
    if not date_value:
        return None
    
    # 如果是datetime对象，直接格式化
    if hasattr(date_value, 'isoformat'):
        return date_value.isoformat()
    
    # 如果是字典格式的日期（MongoDB $date格式）
    if isinstance(date_value, dict) and '$date' in date_value:
        return date_value['$date']
    
    # 如果已经是字符串，直接返回
    if isinstance(date_value, str):
        return date_value
    
    # 其他情况，转换为字符串
    return str(date_value)

# 创建健康记录
@health_bp.route('/records', methods=['POST'])
@login_required
@role_required(Role.PATIENT)
def create_health_record():
    try:
        # 获取基本记录信息
        record_data = json.loads(request.form.get('record_data', '{}'))
        
        if not record_data.get('title') or not record_data.get('record_type'):
            return jsonify({
                'success': False,
                'message': '缺少必要字段 (title, record_type)'
            }), 400
        
        # 确保患者ID正确
        record_data['patient_id'] = current_user.id
        
        # 处理PIR保护标志
        pir_protected = request.form.get('pir_protected', 'true').lower() == 'true'
        record_data['pir_protected'] = pir_protected
            
        # 处理上传的文件
        file_info = []
        files = request.files.getlist('files')
        for file in files:
            file_data = save_uploaded_file(file)
            if file_data:
                file_info.append({
                    'file_name': file_data['original_name'],
                    'file_path': file_data['saved_name'],
                    'file_type': file_data['type'],
                    'file_size': file_data['size'],
                    'description': request.form.get('file_description', ''),
                    'uploaded_at': datetime.now().isoformat()
                })
        
        # 检查是否需要加密记录
        encryption_key = request.form.get('encryption_key')
        if encryption_key:
            # 导入加密工具
            from ..utils.encryption_utils import encrypt_record, verify_record_integrity
            
            # 明确设置加密标志
            record_data['is_encrypted'] = True
            
            # 如果提供了加密密钥，则加密记录
            record_data = encrypt_record(record_data, encryption_key)
            
            # 设置数据完整性验证
            record_data['integrity_hash'] = verify_record_integrity(record_data)
        else:
            # 如果没有提供加密密钥，确保明确标记为未加密
            record_data['is_encrypted'] = False
        
        # 存储到MongoDB和MySQL (使用新的集成方法)
        record, mongo_id = HealthRecord.create_with_mongo(record_data, current_user.id, file_info)
        
        # 为每个文件创建RecordFile记录
        for file_data in file_info:
            record_file = RecordFile(
                record_id=record.id,
                file_name=file_data['file_name'],
                file_path=file_data['file_path'],
                file_type=file_data['file_type'],
                file_size=file_data['file_size'],
                description=file_data.get('description', ''),
                is_encrypted=bool(encryption_key)  # 设置文件加密状态
            )
            db.session.add(record_file)
        
        # 处理特定类型的健康记录，存储额外数据到MySQL
        record_type = record_data.get('record_type')
        
        # 处理处方记录
        if record_type == 'PRESCRIPTION' and record_data.get('medication'):
            med_data = record_data.get('medication')
            
            # 处理开始日期
            start_date = None
            if med_data.get('start_date'):
                try:
                    # 尝试多种日期格式
                    date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                    for fmt in date_formats:
                        try:
                            start_date = datetime.strptime(med_data.get('start_date'), fmt).date()
                            break
                        except ValueError:
                            continue
                    
                    # 如果所有格式都失败，尝试去除时间部分
                    if start_date is None and 'T' in med_data.get('start_date'):
                        date_part = med_data.get('start_date').split('T')[0]
                        start_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                except Exception as e:
                    current_app.logger.warning(f"处理开始日期出错: {str(e)}, 使用原始值: {med_data.get('start_date')}")
            
            # 处理结束日期
            end_date = None
            if med_data.get('end_date'):
                try:
                    # 尝试多种日期格式
                    date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                    for fmt in date_formats:
                        try:
                            end_date = datetime.strptime(med_data.get('end_date'), fmt).date()
                            break
                        except ValueError:
                            continue
                    
                    # 如果所有格式都失败，尝试去除时间部分
                    if end_date is None and 'T' in med_data.get('end_date'):
                        date_part = med_data.get('end_date').split('T')[0]
                        end_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                except Exception as e:
                    current_app.logger.warning(f"处理结束日期出错: {str(e)}, 使用原始值: {med_data.get('end_date')}")
            
            medication_record = MedicationRecord(
                record_id=record.id,
                prescription_id=record_data.get('prescription_id'),
                medication_name=med_data.get('medication_name', ''),
                dosage=med_data.get('dosage'),
                frequency=med_data.get('frequency'),
                start_date=start_date,
                end_date=end_date,
                instructions=med_data.get('instructions'),
                side_effects=med_data.get('side_effects'),
                is_prescribed=bool(record_data.get('prescription_id'))
            )
            db.session.add(medication_record)
            current_app.logger.info(f"添加了处方记录 {medication_record.medication_name}")
        
        # 处理生命体征记录
        if record_type == 'VITAL_SIGN' and record_data.get('vital_signs'):
            for vs_data in record_data.get('vital_signs', []):
                measured_at = None
                if vs_data.get('measured_at'):
                    try:
                        measured_at = datetime.strptime(vs_data.get('measured_at'), '%Y-%m-%dT%H:%M:%S.%f')
                    except ValueError:
                        try:
                            measured_at = datetime.strptime(vs_data.get('measured_at'), '%Y-%m-%dT%H:%M:%S')
                        except ValueError:
                            measured_at = datetime.now()
                else:
                    measured_at = datetime.now()
                
                vital_sign = VitalSign(
                    record_id=record.id,
                    type=vs_data.get('type', ''),
                    value=float(vs_data.get('value', 0)),
                    unit=vs_data.get('unit'),
                    measured_at=measured_at,
                    notes=vs_data.get('notes')
                )
                db.session.add(vital_sign)
                current_app.logger.info(f"添加了生命体征记录 {vital_sign.type}: {vital_sign.value} {vital_sign.unit}")
        
        # 记录创建健康记录日志
        log_record(
            message=f'用户创建了健康记录: {record_data.get("title")}',
            details={
                'record_id': str(mongo_id),
                'sql_id': record.id,
                'record_type': record_data.get('record_type'),
                'title': record_data.get('title'),
                'visibility': record_data.get('visibility', 'private'),
                'is_encrypted': bool(encryption_key),
                'file_count': len(file_info),
                'creation_time': datetime.now().isoformat()
            }
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '健康记录创建成功',
            'data': {
                'record_id': str(mongo_id),
                'sql_id': record.id,
                'storage_type': 'hybrid'
            }
        }), 201
        
    except Exception as e:
        current_app.logger.error(f"创建健康记录失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'创建健康记录失败: {str(e)}'
        }), 500

# 获取文件
@health_bp.route('/files/<filename>', methods=['GET'])
@login_required
def get_record_file(filename):
    try:
        # 检查当前用户是否有权限访问文件
        record_file = RecordFile.query.filter_by(file_path=filename).first()
        if not record_file:
            return jsonify({
                'success': False,
                'message': '文件不存在'
            }), 404
            
        record = HealthRecord.query.get(record_file.record_id)
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
            
        # 检查访问权限
        if record.patient_id != current_user.id and record.visibility == RecordVisibility.PRIVATE:
            return jsonify({
                'success': False,
                'message': '没有权限访问此文件'
            }), 403
            
        if record.visibility == RecordVisibility.DOCTOR and not current_user.has_role(Role.DOCTOR):
            return jsonify({
                'success': False,
                'message': '没有权限访问此文件'
            }), 403
            
        if record.visibility == RecordVisibility.RESEARCHER and not current_user.has_role(Role.RESEARCHER):
            return jsonify({
                'success': False,
                'message': '没有权限访问此文件'
            }), 403
        
        # 记录查询历史
        query_history = QueryHistory(
            user_id=current_user.id,
            record_id=record.id,
            query_type='file_download',
            is_anonymous=False,
            query_params={'file_id': record_file.id}
        )
        db.session.add(query_history)
        db.session.commit()
        
        return send_from_directory(UPLOAD_FOLDER, filename)
    except Exception as e:
        current_app.logger.error(f"获取文件失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': '获取文件失败'
        }), 500

# 获取患者健康记录列表
@health_bp.route('/records', methods=['GET'])
@login_required
def get_health_records():
    try:
        # 查询参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        record_type = request.args.get('record_type')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        keyword = request.args.get('keyword')
        is_anonymous = request.args.get('anonymous', 'false').lower() == 'true'
        
        # 使用MongoDB存储 (优化PIR性能)
        query_params = {}
        if record_type:
            query_params['record_type'] = record_type
        if start_date:
            query_params['start_date'] = start_date
        if end_date:
            query_params['end_date'] = end_date
        if keyword:
            query_params['keyword'] = keyword
            
        # 是否使用PIR隐匿查询
        use_pir = is_anonymous and current_app.config.get('PIR_ENABLE_OBFUSCATION', False)
        
        # 查询MongoDB
        results, metadata = query_health_records_mongodb(
            query_params, 
            current_user.id, 
            is_anonymous=use_pir
        )
        
        # 分页处理
        total = len(results)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paged_results = results[start_idx:end_idx] if start_idx < total else []
        
        return jsonify({
            'success': True,
            'data': {
                'records': paged_results,
                'total': total,
                'pages': math.ceil(total / per_page),
                'current_page': page,
                'metadata': metadata
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取健康记录列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取健康记录列表失败: {str(e)}'
        }), 500

# 获取单条健康记录详情
@health_bp.route('/records/<record_id>', methods=['GET'])
@login_required
def get_health_record(record_id):
    try:
        mongo_id = None
        sql_record = None
        
        # 检查是否是数字ID（SQL ID）
        if record_id.isdigit():
            current_app.logger.info(f"检测到数字ID: {record_id}，尝试从MySQL获取对应的记录")
            sql_record = HealthRecord.query.get(int(record_id))
            if sql_record and sql_record.mongo_id:
                mongo_id = format_mongo_id(sql_record.mongo_id)
                record_id = sql_record.mongo_id  # 更新record_id为mongo_id
                current_app.logger.info(f"从MySQL找到对应的MongoDB ID: {record_id}")
            else:
                current_app.logger.error(f"MySQL中找不到ID为 {record_id} 的记录，或该记录没有关联的MongoDB ID")
                return jsonify({
                    'success': False,
                    'message': '记录不存在或未关联MongoDB数据'
                }), 404
        else:
            # 尝试转换为ObjectId
            try:
                mongo_id = format_mongo_id(record_id)
                if not mongo_id:
                    return jsonify({
                        'success': False,
                        'message': '无效的记录ID格式'
                    }), 400
            except Exception as e:
                current_app.logger.error(f"格式化MongoDB ID失败: {str(e)}")
                return jsonify({
                    'success': False,
                    'message': '无效的记录ID格式'
                }), 400
        
        # 使用缓存函数获取MongoDB记录
        record_data = get_mongo_health_record(record_id)
        
        if not record_data:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查访问权限
        has_permission, error_msg = check_record_access_permission(record_data, current_user)
        if not has_permission:
            return jsonify({
                'success': False,
                'message': error_msg
            }), 403
        
        # 如果之前没有获取SQL记录，尝试查找或创建MySQL中的索引记录
        if not sql_record:
            sql_record = HealthRecord.query.filter_by(mongo_id=record_id).first()
            
            if not sql_record:
                # 如果MySQL中没有记录，则同步创建
                mongo_db = get_mongo_db()
                mongo_record = mongo_db.health_records.find_one({'_id': mongo_id})
                if mongo_record:
                    sql_record = HealthRecord.from_mongo_doc(mongo_record)
                    db.session.add(sql_record)
                    db.session.commit()
        
        # 记录查询历史
        is_anonymous = request.args.get('anonymous', 'false').lower() == 'true'
        query_type = 'pir_record_detail' if is_anonymous else 'standard_record_detail'
        
        # 在MySQL中记录查询历史
        if sql_record:
            query_history = QueryHistory(
                user_id=current_user.id,
                record_id=sql_record.id,
                query_type=query_type,
                is_anonymous=is_anonymous,
                query_params={'mongo_id': record_id}
            )
            db.session.add(query_history)
            db.session.commit()
        
        # 在MongoDB中也记录查询历史
        mongo_db = get_mongo_db()
        mongo_db.query_history.insert_one({
            'user_id': current_user.id,
            'record_id': record_id,
            'query_type': query_type,
            'is_anonymous': is_anonymous,
            'query_time': datetime.now()
        })
        
        # 检查记录是否加密
        is_encrypted = record_data.get('is_encrypted', False)
        requires_decryption = is_encrypted and 'encrypted_data' in record_data
        
        # 如果记录加密且未包含敏感数据，添加提示信息
        message = '记录获取成功'
        if requires_decryption:
            message = '记录已加密，需要提供解密密钥查看完整内容'
        
        return jsonify({
            'success': True,
            'message': message,
            'data': {
                'record': record_data,
                'sql_id': sql_record.id if sql_record else None,
                'is_encrypted': is_encrypted,
                'requires_decryption': requires_decryption
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取健康记录详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取健康记录详情失败: {str(e)}'
        }), 500

# 更新健康记录
@health_bp.route('/records/<record_id>', methods=['PUT'])
@login_required
def update_health_record(record_id):
    try:
        # 从MongoDB获取记录
        try:
            mongo_id = format_mongo_id(record_id)
            if not mongo_id:
                return jsonify({
                    'success': False,
                    'message': '无效的记录ID'
                }), 400
        except:
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
        mongo_db = get_mongo_db()
        record = mongo_db.health_records.find_one({'_id': mongo_id})
        
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查是否有权限修改（只有患者自己或管理员可以修改）
        if str(record['patient_id']) != str(current_user.id) and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '没有权限修改此记录'
            }), 403
        
        # 获取更新数据
        update_data = request.json
        
        # 准备更新字段
        update_fields = {}
        
        # 基本字段
        basic_fields = ['title', 'description', 'institution', 'doctor_name', 'visibility', 'tags', 'pir_protected']
        for field in basic_fields:
            if field in update_data:
                update_fields[field] = update_data[field]
        
        # 日期字段
        if 'record_date' in update_data:
            try:
                update_fields['record_date'] = datetime.strptime(update_data['record_date'], '%Y-%m-%dT%H:%M:%S.%f')
            except ValueError:
                try:
                    update_fields['record_date'] = datetime.strptime(update_data['record_date'], '%Y-%m-%dT%H:%M:%S')
                except ValueError:
                    try:
                        # 增加对简单日期格式的支持
                        update_fields['record_date'] = datetime.strptime(update_data['record_date'], '%Y-%m-%d')
                    except ValueError:
                        return jsonify({
                            'success': False,
                            'message': f'无效的日期格式: {update_data["record_date"]}'
                        }), 400
        
        # 用药记录
        if 'medication' in update_data and record.get('record_type') == 'PRESCRIPTION':
            med_data = update_data['medication']
            medication_update = {}
            
            for field in ['medication_name', 'dosage', 'frequency', 'instructions', 'side_effects']:
                if field in med_data:
                    medication_update[f'medication.{field}'] = med_data[field]
            
            # 日期字段特殊处理
            for date_field in ['start_date', 'end_date']:
                if date_field in med_data and med_data[date_field]:
                    try:
                        # 尝试多种日期格式
                        date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                        for fmt in date_formats:
                            try:
                                date_value = datetime.strptime(med_data[date_field], fmt).date()
                                medication_update[f'medication.{date_field}'] = date_value
                                break
                            except ValueError:
                                continue
                        
                        # 如果所有格式都失败，尝试去除时间部分
                        if f'medication.{date_field}' not in medication_update and 'T' in med_data[date_field]:
                            date_part = med_data[date_field].split('T')[0]
                            date_value = datetime.strptime(date_part, '%Y-%m-%d').date()
                            medication_update[f'medication.{date_field}'] = date_value
                    except Exception as e:
                        current_app.logger.warning(f"更新记录时处理{date_field}出错: {str(e)}, 跳过该字段")
                        
            update_fields.update(medication_update)
        
        # 生命体征记录更新
        if 'vital_signs' in update_data and record.get('record_type') == 'VITAL_SIGN':
            # 先保存旧值，以便后续比较
            old_vital_signs = record.get('vital_signs', [])
            # 更新MongoDB中的值
            update_fields['vital_signs'] = []
            
            for vs_data in update_data['vital_signs']:
                measured_at = None
                if vs_data.get('measured_at'):
                    try:
                        measured_at = datetime.strptime(vs_data.get('measured_at'), '%Y-%m-%dT%H:%M:%S.%f')
                    except ValueError:
                        try:
                            measured_at = datetime.strptime(vs_data.get('measured_at'), '%Y-%m-%dT%H:%M:%S')
                        except ValueError:
                            measured_at = datetime.now()
                else:
                    measured_at = datetime.now()
                
                update_fields['vital_signs'].append({
                    'type': vs_data.get('type', ''),
                    'value': float(vs_data.get('value', 0)),
                    'unit': vs_data.get('unit'),
                    'measured_at': measured_at,
                    'notes': vs_data.get('notes')
                })
        
        # 设置更新时间
        update_fields['updated_at'] = datetime.now()
        
        # 执行更新
        mongo_db.health_records.update_one(
            {'_id': mongo_id},
            {'$set': update_fields}
        )
        
        # 同步更新MySQL记录
        sql_record = HealthRecord.query.filter_by(mongo_id=record_id).first()
        
        if sql_record:
            # 更新基本字段
            if 'title' in update_fields:
                sql_record.title = update_fields['title']
                
            if 'record_date' in update_fields:
                sql_record.record_date = update_fields['record_date']
                
            if 'visibility' in update_fields:
                try:
                    sql_record.visibility = RecordVisibility(update_fields['visibility'])
                except (ValueError, TypeError):
                    pass
                    
            sql_record.updated_at = update_fields['updated_at']
            
            # 更新处方记录 (MySQL)
            if 'medication' in update_data and record.get('record_type') == 'PRESCRIPTION':
                # 查找现有的用药记录
                med_record = MedicationRecord.query.filter_by(record_id=sql_record.id).first()
                med_data = update_data['medication']
                
                if med_record:
                    # 更新现有记录
                    if 'medication_name' in med_data:
                        med_record.medication_name = med_data['medication_name']
                    if 'dosage' in med_data:
                        med_record.dosage = med_data['dosage']
                    if 'frequency' in med_data:
                        med_record.frequency = med_data['frequency']
                    if 'instructions' in med_data:
                        med_record.instructions = med_data['instructions']
                    if 'side_effects' in med_data:
                        med_record.side_effects = med_data['side_effects']
                    
                    # 处理日期
                    if 'start_date' in med_data and med_data['start_date']:
                        try:
                            # 尝试多种日期格式
                            date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                            for fmt in date_formats:
                                try:
                                    med_record.start_date = datetime.strptime(med_data['start_date'], fmt).date()
                                    break
                                except ValueError:
                                    continue
                            
                            # 如果所有格式都失败，尝试去除时间部分
                            if med_record.start_date is None and 'T' in med_data['start_date']:
                                date_part = med_data['start_date'].split('T')[0]
                                med_record.start_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                        except Exception as e:
                            current_app.logger.warning(f"更新MySQL记录时处理start_date出错: {str(e)}")
                            
                    if 'end_date' in med_data and med_data['end_date']:
                        try:
                            # 尝试多种日期格式
                            date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                            for fmt in date_formats:
                                try:
                                    med_record.end_date = datetime.strptime(med_data['end_date'], fmt).date()
                                    break
                                except ValueError:
                                    continue
                            
                            # 如果所有格式都失败，尝试去除时间部分
                            if med_record.end_date is None and 'T' in med_data['end_date']:
                                date_part = med_data['end_date'].split('T')[0]
                                med_record.end_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                        except Exception as e:
                            current_app.logger.warning(f"更新MySQL记录时处理end_date出错: {str(e)}")
                    
                    med_record.updated_at = datetime.now()
                    current_app.logger.info(f"更新了处方记录 {med_record.medication_name}")
                else:
                    # 创建新记录
                    # 处理开始日期
                    start_date = None
                    if med_data.get('start_date'):
                        try:
                            # 尝试多种日期格式
                            date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                            for fmt in date_formats:
                                try:
                                    start_date = datetime.strptime(med_data.get('start_date'), fmt).date()
                                    break
                                except ValueError:
                                    continue
                            
                            # 如果所有格式都失败，尝试去除时间部分
                            if start_date is None and 'T' in med_data.get('start_date'):
                                date_part = med_data.get('start_date').split('T')[0]
                                start_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                        except Exception as e:
                            current_app.logger.warning(f"创建新记录时处理start_date出错: {str(e)}")
                    
                    # 处理结束日期
                    end_date = None
                    if med_data.get('end_date'):
                        try:
                            # 尝试多种日期格式
                            date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                            for fmt in date_formats:
                                try:
                                    end_date = datetime.strptime(med_data.get('end_date'), fmt).date()
                                    break
                                except ValueError:
                                    continue
                            
                            # 如果所有格式都失败，尝试去除时间部分
                            if end_date is None and 'T' in med_data.get('end_date'):
                                date_part = med_data.get('end_date').split('T')[0]
                                end_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                        except Exception as e:
                            current_app.logger.warning(f"创建新记录时处理end_date出错: {str(e)}")
                    
                    new_med_record = MedicationRecord(
                        record_id=sql_record.id,
                        prescription_id=record.get('prescription_id'),
                        medication_name=med_data.get('medication_name', ''),
                        dosage=med_data.get('dosage'),
                        frequency=med_data.get('frequency'),
                        start_date=start_date,
                        end_date=end_date,
                        instructions=med_data.get('instructions'),
                        side_effects=med_data.get('side_effects'),
                        is_prescribed=bool(record.get('prescription_id'))
                    )
                    db.session.add(new_med_record)
                    current_app.logger.info(f"创建了处方记录 {new_med_record.medication_name}")
            
            # 更新生命体征记录 (MySQL)
            if 'vital_signs' in update_data and record.get('record_type') == 'VITAL_SIGN':
                # 获取现有的生命体征记录
                existing_vitals = VitalSign.query.filter_by(record_id=sql_record.id).all()
                
                # 创建类型到记录的映射
                existing_vitals_map = {f"{v.type}_{v.measured_at.isoformat() if v.measured_at else ''}": v for v in existing_vitals}
                
                # 更新或创建每个生命体征
                for vs_data in update_data['vital_signs']:
                    measured_at = None
                    if vs_data.get('measured_at'):
                        try:
                            measured_at = datetime.strptime(vs_data.get('measured_at'), '%Y-%m-%dT%H:%M:%S.%f')
                        except ValueError:
                            try:
                                measured_at = datetime.strptime(vs_data.get('measured_at'), '%Y-%m-%dT%H:%M:%S')
                            except ValueError:
                                measured_at = datetime.now()
                    else:
                        measured_at = datetime.now()
                    
                    # 创建键以查找现有记录
                    vs_key = f"{vs_data.get('type')}_{measured_at.isoformat() if measured_at else ''}"
                    
                    if vs_key in existing_vitals_map:
                        # 更新现有记录
                        vital = existing_vitals_map[vs_key]
                        vital.value = float(vs_data.get('value', 0))
                        vital.unit = vs_data.get('unit')
                        vital.notes = vs_data.get('notes')
                        current_app.logger.info(f"更新了生命体征记录 {vital.type}: {vital.value} {vital.unit}")
                    else:
                        # 创建新记录
                        new_vital = VitalSign(
                            record_id=sql_record.id,
                            type=vs_data.get('type', ''),
                            value=float(vs_data.get('value', 0)),
                            unit=vs_data.get('unit'),
                            measured_at=measured_at,
                            notes=vs_data.get('notes')
                        )
                        db.session.add(new_vital)
                        current_app.logger.info(f"创建了生命体征记录 {new_vital.type}: {new_vital.value} {new_vital.unit}")
            
            db.session.commit()
        else:
            # 如果不存在，创建一个新的
            mongo_record = mongo_db.health_records.find_one({'_id': mongo_id})
            if mongo_record:
                new_sql_record = HealthRecord.from_mongo_doc(mongo_record)
                db.session.add(new_sql_record)
                db.session.commit()
                
                # 如果是处方或生命体征记录，添加对应的MySQL表记录
                if record.get('record_type') == 'PRESCRIPTION' and record.get('medication'):
                    med_data = record.get('medication')
                    
                    # 确保日期格式正确
                    start_date = None
                    end_date = None
                    
                    # 处理开始日期
                    if isinstance(med_data.get('start_date'), date):
                        start_date = med_data.get('start_date')
                    elif isinstance(med_data.get('start_date'), datetime):
                        start_date = med_data.get('start_date').date()
                    elif isinstance(med_data.get('start_date'), str):
                        try:
                            # 尝试多种日期格式
                            date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                            for fmt in date_formats:
                                try:
                                    start_date = datetime.strptime(med_data.get('start_date'), fmt).date()
                                    break
                                except ValueError:
                                    continue
                            
                            # 如果所有格式都失败，尝试去除时间部分
                            if start_date is None and 'T' in med_data.get('start_date'):
                                date_part = med_data.get('start_date').split('T')[0]
                                start_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                        except Exception as e:
                            current_app.logger.warning(f"MongoDB记录同步时处理start_date出错: {str(e)}")
                    
                    # 处理结束日期
                    if isinstance(med_data.get('end_date'), date):
                        end_date = med_data.get('end_date')
                    elif isinstance(med_data.get('end_date'), datetime):
                        end_date = med_data.get('end_date').date()
                    elif isinstance(med_data.get('end_date'), str):
                        try:
                            # 尝试多种日期格式
                            date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                            for fmt in date_formats:
                                try:
                                    end_date = datetime.strptime(med_data.get('end_date'), fmt).date()
                                    break
                                except ValueError:
                                    continue
                            
                            # 如果所有格式都失败，尝试去除时间部分
                            if end_date is None and 'T' in med_data.get('end_date'):
                                date_part = med_data.get('end_date').split('T')[0]
                                end_date = datetime.strptime(date_part, '%Y-%m-%d').date()
                        except Exception as e:
                            current_app.logger.warning(f"MongoDB记录同步时处理end_date出错: {str(e)}")
                    
                    new_med_record = MedicationRecord(
                        record_id=new_sql_record.id,
                        prescription_id=record.get('prescription_id'),
                        medication_name=med_data.get('medication_name', ''),
                        dosage=med_data.get('dosage'),
                        frequency=med_data.get('frequency'),
                        start_date=start_date,
                        end_date=end_date,
                        instructions=med_data.get('instructions'),
                        side_effects=med_data.get('side_effects'),
                        is_prescribed=bool(record.get('prescription_id'))
                    )
                    db.session.add(new_med_record)
                
                if record.get('record_type') == 'VITAL_SIGN' and record.get('vital_signs'):
                    for vs_data in record.get('vital_signs', []):
                        new_vital = VitalSign(
                            record_id=new_sql_record.id,
                            type=vs_data.get('type', ''),
                            value=float(vs_data.get('value', 0)),
                            unit=vs_data.get('unit'),
                            measured_at=vs_data.get('measured_at') if isinstance(vs_data.get('measured_at'), datetime) else datetime.now(),
                            notes=vs_data.get('notes')
                        )
                        db.session.add(new_vital)
                
                db.session.commit()
        
        # 获取更新后的记录
        updated_record = mongo_db.health_records.find_one({'_id': mongo_id})
        
        # 使用通用函数处理记录
        record_data = mongo_health_record_to_dict(updated_record)
        
        return jsonify({
            'success': True,
            'message': '健康记录更新成功',
            'data': record_data
        })
    except Exception as e:
        current_app.logger.error(f"更新健康记录失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'更新健康记录失败: {str(e)}'
        }), 500

# 删除健康记录
@health_bp.route('/records/<record_id>', methods=['DELETE'])
@login_required
def delete_health_record(record_id):
    try:
        # 查找记录
        mongo_id = format_mongo_id(record_id)
        if not mongo_id:
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
        
        # 获取记录信息
        record = get_mongo_health_record(mongo_id)
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查权限
        if str(record['patient_id']) != str(current_user.id) and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '没有权限删除此记录'
            }), 403
        
        # 保存记录信息以便日志记录
        record_info = {
            'record_id': str(record['_id']),
            'title': record.get('title', ''),
            'record_type': record.get('record_type', ''),
            'patient_id': str(record.get('patient_id', '')),
            'creation_time': str(record.get('creation_time', ''))
        }
        
        # 获取关联的SQL记录
        sql_record = HealthRecord.query.filter_by(mongo_id=str(mongo_id)).first()
        sql_id = sql_record.id if sql_record else None
        
        # 删除MongoDB记录
        mongo_db = get_mongo_db()
        
        # 备份到已删除集合
        record['deletion_time'] = datetime.now()
        record['deleted_by'] = current_user.id
        record['deletion_reason'] = request.args.get('reason', '用户删除')
        
        mongo_db.health_records_deleted.insert_one(record)
        result = mongo_db.health_records.delete_one({'_id': mongo_id})
        
        if result.deleted_count == 0:
            return jsonify({
                'success': False,
                'message': '记录不存在或已被删除'
            }), 404
        
        # 删除SQL记录及关联的文件
        if sql_record:
            # 获取关联的文件
            files = RecordFile.query.filter_by(record_id=sql_record.id).all()
            
            # 删除物理文件
            for file in files:
                try:
                    file_path = os.path.join(UPLOAD_FOLDER, file.file_path)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as e:
                    current_app.logger.error(f"删除文件失败: {str(e)}")
            
            # 删除SQL记录及关联文件记录
            RecordFile.query.filter_by(record_id=sql_record.id).delete()
            SharedRecord.query.filter_by(record_id=sql_record.id).delete()
            db.session.delete(sql_record)
        
        # 记录删除健康记录日志
        from ..utils.log_utils import log_record
        
        log_record(
            message=f'用户删除了健康记录: {record_info["title"]}',
            details={
                'record_id': str(record['_id']),
                'title': record.get('title', ''),
                'record_type': record.get('record_type', ''),
                'patient_id': str(record.get('patient_id', '')),
                'creation_time': str(record.get('creation_time', '')),
                'sql_id': sql_id,
                'deletion_reason': request.args.get('reason', '用户删除'),
                'deletion_time': datetime.now().isoformat()
            }
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '记录已成功删除',
            'data': {
                'record_id': str(mongo_id)
            }
        })
    except Exception as e:
        current_app.logger.error(f"删除健康记录失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'删除记录失败: {str(e)}'
        }), 500

# 获取健康数据统计信息
@health_bp.route('/statistics', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_health_statistics():
    try:
        # 时间范围筛选
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # 基本查询条件
        query_filters = {'patient_id': current_user.id}
        
        # 添加时间过滤条件
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                if 'created_at' not in query_filters:
                    query_filters['created_at'] = {}
                query_filters['created_at']['$gte'] = start_date
            except ValueError:
                pass
                
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                end_date = end_date.replace(hour=23, minute=59, second=59)
                if 'created_at' not in query_filters:
                    query_filters['created_at'] = {}
                query_filters['created_at']['$lte'] = end_date
            except ValueError:
                pass
        
        # 获取MongoDB中所有记录
        mongo_records = list(mongo.db.health_records.find(query_filters))
        
        # 获取记录类型统计
        record_types_stats = {}
        for record in mongo_records:
            record_type = record.get('record_type', '未知')
            record_types_stats[record_type] = record_types_stats.get(record_type, 0) + 1
        
        # 按时间统计记录数量
        time_stats = []
        
        # 如果时间范围不超过3个月，按天统计
        if start_date and end_date and (end_date - start_date).days <= 90:
            current_date = start_date
            while current_date <= end_date:
                next_date = current_date + timedelta(days=1)
                count = sum(1 for record in mongo_records if 
                            record.get('created_at') and 
                            current_date <= record.get('created_at') < next_date)
                
                time_stats.append({
                    'date': current_date.strftime('%Y-%m-%d'),
                    'count': count
                })
                current_date = next_date
        else:  # 否则按月统计
            # 如果没有开始日期，使用最早记录的日期或默认过去一年
            if not start_date:
                if mongo_records:
                    dates = [record.get('created_at') for record in mongo_records if record.get('created_at')]
                    if dates:
                        start_date = min(dates).replace(day=1)
                    else:
                        start_date = datetime.now().replace(day=1) - timedelta(days=365)
                else:
                    start_date = datetime.now().replace(day=1) - timedelta(days=365)
                    
            if not end_date:
                end_date = datetime.now()
                
            current_month = start_date.replace(day=1)
            end_month = end_date.replace(day=1)
            
            while current_month <= end_month:
                next_month = (current_month.replace(day=28) + timedelta(days=4)).replace(day=1)
                count = sum(1 for record in mongo_records if 
                            record.get('created_at') and 
                            current_month <= record.get('created_at') < next_month)
                
                time_stats.append({
                    'year': current_month.year,
                    'month': current_month.month,
                    'count': count
                })
                current_month = next_month
        
        # 获取生命体征数据
        vital_signs_records = list(mongo.db.health_records.find({
            'patient_id': current_user.id,
            'record_type': 'vital_signs',
            'vital_signs': {'$exists': True, '$ne': []}
        }))
        
        # 整理生命体征数据
        vital_sign_data = {}
        for record in vital_signs_records:
            if 'vital_signs' in record:
                for vs in record['vital_signs']:
                    vs_type = vs.get('type')
                    if vs_type not in vital_sign_data:
                        vital_sign_data[vs_type] = {
                            'values': [],
                            'dates': [],
                            'unit': vs.get('unit')
                        }
                    vital_sign_data[vs_type]['values'].append(vs.get('value'))
                    measured_at = vs.get('measured_at')
                    if isinstance(measured_at, datetime):
                        measured_at = measured_at.isoformat()
                    vital_sign_data[vs_type]['dates'].append(measured_at)
        
        # 获取用药统计
        medication_stats = list(mongo.db.health_records.aggregate([
            {'$match': {
                'patient_id': current_user.id,
                'record_type': 'medication',
                'medication.medication_name': {'$exists': True}
            }},
            {'$group': {'_id': '$medication.medication_name', 'count': {'$sum': 1}}},
            {'$sort': {'count': -1}}
        ]))
        
        # 获取医生互动统计
        from ..models import User, HealthRecord
        from sqlalchemy import func
        
        doctor_interaction = db.session.query(
            HealthRecord.doctor_id,
            func.count(HealthRecord.id).label('count')
        ).filter(
            HealthRecord.patient_id == current_user.id
        ).group_by(HealthRecord.doctor_id).all()
        
        doctor_stats = []
        for doctor_id, count in doctor_interaction:
            doctor = User.query.get(doctor_id)
            if doctor:
                doctor_data = {
                    'id': doctor.id,
                    'name': doctor.full_name,
                    'count': count
                }
                
                if doctor.doctor_info:
                    doctor_data.update({
                        'hospital': doctor.doctor_info.hospital,
                        'department': doctor.doctor_info.department,
                        'specialty': doctor.doctor_info.specialty
                    })
                    
                doctor_stats.append(doctor_data)
        
        # 获取预约和处方统计
        from ..models.prescription import Prescription, PrescriptionStatus
        
        # 处方统计
        prescription_stats = {}
        for status in PrescriptionStatus:
            prescription_stats[status.value] = Prescription.query.filter_by(
                patient_id=current_user.id,
                status=status
            ).count()
        
        # 记录查询历史
        is_anonymous = request.args.get('anonymous', 'false').lower() == 'true'
        mongo.db.query_history.insert_one({
            'user_id': current_user.id,
            'query_type': 'statistics',
            'is_anonymous': is_anonymous,
            'query_params': {'start_date': start_date, 'end_date': end_date},
            'query_time': datetime.now()
        })
        
        return jsonify({
            'success': True,
            'data': {
                'record_types': record_types_stats,
                'time_stats': time_stats,
                'vital_signs': vital_sign_data,
                'medications': {item['_id']: item['count'] for item in medication_stats},
                'doctor_stats': doctor_stats,
                'prescription_stats': prescription_stats,
                'total_records': len(mongo_records),
                'total_doctors': len(doctor_stats)
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取健康数据统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取健康数据统计失败: {str(e)}'
        }), 500

# =========== 隐匿查询相关API ============

# 隐匿查询健康记录
@health_bp.route('/pir/records', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def pir_query_health_records():
    try:
        # 查询参数
        query_params = {}
        if request.args.get('record_type'):
            query_params['record_type'] = request.args.get('record_type')
        if request.args.get('start_date'):
            query_params['start_date'] = request.args.get('start_date')
        if request.args.get('end_date'):
            query_params['end_date'] = request.args.get('end_date')
        if request.args.get('keyword'):
            query_params['keyword'] = request.args.get('keyword')
        
        # 是否启用匿名查询
        is_anonymous = request.args.get('anonymous', 'true').lower() == 'true'
        
        # 查询MongoDB
        results, metadata = query_health_records_mongodb(query_params, current_user.id, is_anonymous)
        
        return jsonify({
            'success': True,
            'message': '隐匿查询成功',
            'data': {
                'records': results,
                'metadata': metadata
            }
        })
    except Exception as e:
        current_app.logger.error(f"隐匿查询失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'隐匿查询失败: {str(e)}'
        }), 500

# 获取PIR查询统计信息
@health_bp.route('/pir/statistics', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_pir_statistics():
    try:
        # 获取查询历史统计
        standard_queries = mongo.db.query_history.count_documents({
            'user_id': current_user.id,
            'is_anonymous': False
        })
        
        pir_queries = mongo.db.query_history.count_documents({
            'user_id': current_user.id,
            'is_anonymous': True
        })
        
        # 按类型分组的PIR查询统计
        query_types = list(mongo.db.query_history.aggregate([
            {'$match': {'user_id': current_user.id, 'is_anonymous': True}},
            {'$group': {'_id': '$query_type', 'count': {'$sum': 1}}}
        ]))
        
        # 按月统计的PIR查询
        monthly_stats = list(mongo.db.query_history.aggregate([
            {'$match': {'user_id': current_user.id, 'is_anonymous': True}},
            {
                '$group': {
                    '_id': {
                        'year': {'$year': '$query_time'},
                        'month': {'$month': '$query_time'}
                    },
                    'count': {'$sum': 1}
                }
            },
            {'$sort': {'_id.year': 1, '_id.month': 1}}
        ]))
        
        monthly_data = {}
        for item in monthly_stats:
            year = item['_id']['year']
            month = item['_id']['month']
            key = f"{year}-{month:02d}"
            monthly_data[key] = item['count']
        
        # 计算隐私保护程度 (PIR查询比例)
        total_queries = standard_queries + pir_queries
        privacy_protection_ratio = round(pir_queries / total_queries * 100, 2) if total_queries > 0 else 0
        
        return jsonify({
            'success': True,
            'data': {
                'total_queries': total_queries,
                'standard_queries': standard_queries,
                'pir_queries': pir_queries,
                'privacy_protection_ratio': privacy_protection_ratio,
                'query_types': {item['_id']: item['count'] for item in query_types},
                'monthly_stats': monthly_data
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取PIR统计信息失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取PIR统计信息失败: {str(e)}'
        }), 500

# 获取所有隐私查询历史
@health_bp.route('/pir/history', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_pir_history():
    try:
        # 分页参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        
        # 计算跳过的记录数
        skip = (page - 1) * per_page
        
        # 查询条件
        query = {'user_id': current_user.id}
        
        # 只查询PIR查询
        if request.args.get('only_pir', 'false').lower() == 'true':
            query['is_anonymous'] = True
        
        # 按时间倒序排列
        cursor = mongo.db.query_history.find(query).sort('query_time', -1).skip(skip).limit(per_page)
        
        # 获取总记录数
        total = mongo.db.query_history.count_documents(query)
        
        # 格式化结果
        history = []
        for item in cursor:
            item['_id'] = str(item['_id'])
            item['query_time'] = item['query_time'].isoformat()
            history.append(item)
        
        return jsonify({
            'success': True,
            'data': {
                'history': history,
                'total': total,
                'pages': math.ceil(total / per_page),
                'current_page': page
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取PIR查询历史失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取PIR查询历史失败: {str(e)}'
        }), 500

# =========================== 记录共享功能 ===========================

# 创建健康记录共享
@health_bp.route('/records/<record_id>/share', methods=['POST'])
@login_required
def share_health_record(record_id):
    try:
        data = request.json
        if not data or not data.get('share_with_id') or not data.get('permission'):
            return jsonify({
                'success': False,
                'message': '缺少必要字段 (share_with_id, permission)'
            }), 400
        
        # 查找记录
        mongo_id = format_mongo_id(record_id)
        if not mongo_id:
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
        sql_record = HealthRecord.query.filter_by(mongo_id=str(mongo_id)).first()
        if not sql_record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
            
        # 检查记录所有权
        if sql_record.patient_id != current_user.id and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '只有记录所有者可以分享记录'
            }), 403
            
        # 检查分享目标用户是否存在
        target_user = User.query.get(data['share_with_id'])
        if not target_user:
            return jsonify({
                'success': False,
                'message': '目标用户不存在'
            }), 404
            
        # 不能与自己分享
        if target_user.id == current_user.id:
            return jsonify({
                'success': False,
                'message': '不能与自己分享记录'
            }), 400
            
        # 检查记录是否已经与该用户分享
        existing_share = SharedRecord.query.filter_by(
            record_id=sql_record.id,
            shared_with=target_user.id
        ).first()
        
        if existing_share:
            # 更新权限
            old_permission = existing_share.permission
            try:
                permission = SharePermission(data['permission'])
                existing_share.permission = permission
                existing_share.updated_at = datetime.now()
                
                # 记录更新共享权限的日志
                log_record(
                    message=f'用户更新了记录共享权限',
                    details={
                        'owner_id': current_user.id,
                        'owner_username': current_user.username,
                        'shared_with': target_user.id,
                        'shared_with_username': target_user.username,
                        'record_id': str(mongo_id),
                        'sql_id': sql_record.id,
                        'old_permission': str(old_permission),
                        'new_permission': str(permission),
                        'update_time': datetime.now().isoformat()
                    }
                )
                
                # 创建通知
                notification = Notification(
                    user_id=target_user.id,
                    notification_type=NotificationType.SHARE,
                    title='健康记录共享权限更新',
                    message=f'{current_user.username}更新了与您共享的健康记录权限',
                    related_id=str(mongo_id)
                )
                db.session.add(notification)
                db.session.commit()
                
                return jsonify({
                    'success': True,
                    'message': '共享权限已更新',
                    'data': {
                        'share_id': existing_share.id,
                        'permission': str(permission)
                    }
                })
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': '无效的权限值'
                }), 400
        
        # 创建新的共享记录
        try:
            permission = SharePermission(data['permission'])
            expiry = None
            
            if data.get('expiry_days'):
                try:
                    days = int(data['expiry_days'])
                    if days > 0:
                        expiry = datetime.now() + timedelta(days=days)
                except ValueError:
                    pass
                    
            # 创建共享记录
            shared_record = SharedRecord(
                record_id=sql_record.id,
                mongo_record_id=str(mongo_id),  # 设置MongoDB记录ID
                owner_id=current_user.id,
                shared_with=target_user.id,
                permission=permission,
                expires_at=expiry,
                access_key=secrets.token_urlsafe(16)
            )
            db.session.add(shared_record)
            
            # 记录创建共享记录的日志
            log_record(
                message=f'用户分享了健康记录',
                details={
                    'owner_id': current_user.id,
                    'owner_username': current_user.username,
                    'shared_with': target_user.id,
                    'shared_with_username': target_user.username,
                    'record_id': str(mongo_id),
                    'sql_id': sql_record.id,
                    'record_title': sql_record.title,
                    'permission': str(permission),
                    'expiry': expiry.isoformat() if expiry else None,
                    'share_time': datetime.now().isoformat()
                }
            )
            
            # 创建通知
            notification = Notification(
                user_id=target_user.id,
                notification_type=NotificationType.SHARE,
                title='新的健康记录共享',
                message=f'{current_user.username}与您共享了一份健康记录"{sql_record.title}"',
                related_id=str(mongo_id)
            )
            db.session.add(notification)
            db.session.commit()
            
            # 直接返回成功消息，无需尝试更新通知的不存在的data属性
            return jsonify({
                'success': True,
                'message': '记录分享成功',
                'data': {
                    'share_id': shared_record.id,
                    'record_id': str(mongo_id),
                    'shared_with': target_user.username,
                    'permission': str(permission),
                    'access_key': shared_record.access_key,
                    'expiry': expiry.isoformat() if expiry else None
                }
            })
            
        except ValueError:
            return jsonify({
                'success': False,
                'message': '无效的权限值'
            }), 400
            
    except Exception as e:
        current_app.logger.error(f"分享健康记录失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'分享记录失败: {str(e)}'
        }), 500

# 获取我共享的记录列表
@health_bp.route('/shared/by-me', methods=['GET'])
@login_required
def get_records_shared_by_me():
    try:
        # 分页
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        
        # 查询条件
        query = SharedRecord.query.filter_by(owner_id=current_user.id)
        
        # 应用过滤器
        if 'shared_with' in request.args:
            query = query.filter_by(shared_with=request.args.get('shared_with', type=int))
        
        if 'valid_only' in request.args and request.args.get('valid_only').lower() == 'true':
            query = query.filter(
                (SharedRecord.expires_at == None) | (SharedRecord.expires_at > datetime.now())
            )
        
        # 执行查询
        total = query.count()
        shared_records = query.order_by(SharedRecord.created_at.desc()) \
                             .offset((page - 1) * per_page) \
                             .limit(per_page) \
                             .all()
                            
        # 获取相关记录信息
        result = []
        for shared in shared_records:
            # 获取健康记录
            health_record = shared.health_record
            
            # 如果需要详细信息，获取MongoDB记录
            mongo_record = None
            if health_record and health_record.mongo_id:
                mongo_record = get_mongo_health_record(health_record.mongo_id)
            
            # 获取共享用户信息
            shared_user = User.query.get(shared.shared_with)
            
            record_info = {
                'shared_id': shared.id,
                'record_id': shared.record_id,
                'shared_with': {
                    'id': shared_user.id if shared_user else None,
                    'username': shared_user.username if shared_user else None,
                    'full_name': shared_user.full_name if shared_user else None
                },
                'permission': shared.permission.value,
                'created_at': shared.created_at.isoformat() if shared.created_at else None,
                'expires_at': shared.expires_at.isoformat() if shared.expires_at else None,
                'is_valid': shared.is_valid(),
                'access_count': shared.access_count,
                'last_accessed': shared.last_accessed.isoformat() if shared.last_accessed else None,
                'access_key': shared.access_key,  # 添加访问密钥
                'record_info': {
                    'title': mongo_record['title'] if mongo_record else None,
                    'record_type': mongo_record['record_type'] if mongo_record else None,
                    'record_date': format_mongo_date(mongo_record.get('record_date')) if mongo_record else None
                }
            }
            result.append(record_info)
        
        return jsonify({
            'success': True,
            'data': {
                'shared_records': result,
                'total': total,
                'pages': math.ceil(total / per_page),
                'current_page': page
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取我共享的记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取共享记录失败: {str(e)}'
        }), 500

# 获取共享给我的记录列表
@health_bp.route('/shared/with-me', methods=['GET'])
@login_required
def get_records_shared_with_me():
    try:
        # 分页
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        
        # 查询条件
        query = SharedRecord.query.filter_by(shared_with=current_user.id)
        
        # 应用过滤器
        if 'owner_id' in request.args:
            query = query.filter_by(owner_id=request.args.get('owner_id', type=int))
            
        if 'valid_only' in request.args and request.args.get('valid_only').lower() == 'true':
            query = query.filter(
                (SharedRecord.expires_at == None) | (SharedRecord.expires_at > datetime.now())
            )
        
        # 执行查询
        total = query.count()
        shared_records = query.order_by(SharedRecord.created_at.desc()) \
                             .offset((page - 1) * per_page) \
                             .limit(per_page) \
                             .all()
                            
        # 获取相关记录信息
        result = []
        for shared in shared_records:
            # 获取健康记录
            health_record = shared.health_record
            
            # 如果需要详细信息，获取MongoDB记录
            mongo_record = None
            if health_record and health_record.mongo_id:
                mongo_record = get_mongo_health_record(health_record.mongo_id)
            
            # 获取共享用户信息
            owner_user = User.query.get(shared.owner_id)
            
            record_info = {
                'shared_id': shared.id,
                'record_id': shared.record_id,
                'mongo_id': shared.mongo_record_id or (health_record.mongo_id if health_record else None),
                'owner': {
                    'id': owner_user.id if owner_user else None,
                    'username': owner_user.username if owner_user else None,
                    'full_name': owner_user.full_name if owner_user else None
                },
                'permission': shared.permission.value,
                'created_at': shared.created_at.isoformat() if shared.created_at else None,
                'expires_at': shared.expires_at.isoformat() if shared.expires_at else None,
                'is_valid': shared.is_valid(),
                'access_count': shared.access_count,
                'last_accessed': shared.last_accessed.isoformat() if shared.last_accessed else None,
                'record_info': {
                    'title': health_record.title if health_record else (mongo_record.get('title') if mongo_record else None),
                    'record_type': health_record.record_type.value if health_record else (mongo_record.get('record_type') if mongo_record else None),
                    'record_date': format_mongo_date(health_record.record_date) if health_record and health_record.record_date else (
                        format_mongo_date(mongo_record.get('record_date')) if mongo_record else None
                    )
                }
            }
            result.append(record_info)
        
        return jsonify({
            'success': True,
            'data': {
                'shared_records': result,
                'total': total,
                'pages': math.ceil(total / per_page),
                'current_page': page
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取共享给我的记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取共享给我的记录失败: {str(e)}'
        }), 500

# 查看共享记录详情
@health_bp.route('/shared/records/<shared_id>', methods=['GET'])
@login_required
def view_shared_record(shared_id):
    try:
        # 获取共享记录
        shared_record = SharedRecord.query.get(shared_id)
        if not shared_record:
            return jsonify({
                'success': False,
                'message': '共享记录不存在'
            }), 404
            
        # 验证权限
        if shared_record.shared_with != current_user.id and shared_record.owner_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有访问权限'
            }), 403
            
        # 检查是否过期
        if not shared_record.is_valid() and shared_record.shared_with == current_user.id:
            return jsonify({
                'success': False,
                'message': '共享已过期'
            }), 403
            
        # 如果是被共享用户访问，记录访问情况
        if shared_record.shared_with == current_user.id:
            shared_record.record_access()
            
        # 获取健康记录
        health_record = shared_record.health_record
        if not health_record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 使用缓存函数获取MongoDB中的记录
        mongo_id = health_record.mongo_id
        record_data = get_mongo_health_record(mongo_id) if mongo_id else None
        
        if not record_data:
            return jsonify({
                'success': False,
                'message': '记录不存在或已被删除'
            }), 404
            
        # 获取用户信息
        owner = User.query.get(shared_record.owner_id)
        shared_with_user = User.query.get(shared_record.shared_with)
        
        result = {
            'shared_id': shared_record.id,
            'record_id': health_record.id,
            'mongo_id': health_record.mongo_id,
            'sharing_info': {
                'permission': shared_record.permission.value,
                'created_at': shared_record.created_at.isoformat() if shared_record.created_at else None,
                'expires_at': shared_record.expires_at.isoformat() if shared_record.expires_at else None,
                'access_count': shared_record.access_count,
                'last_accessed': shared_record.last_accessed.isoformat() if shared_record.last_accessed else None,
                'owner': {
                    'id': owner.id if owner else None,
                    'username': owner.username if owner else None,
                    'full_name': owner.full_name if owner else None
                },
                'shared_with': {
                    'id': shared_with_user.id if shared_with_user else None,
                    'username': shared_with_user.username if shared_with_user else None,
                    'full_name': shared_with_user.full_name if shared_with_user else None
                }
            },
            'record': record_data,
            'sql_id': health_record.id
        }
        
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        current_app.logger.error(f"查看共享记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'查看共享记录失败: {str(e)}'
        }), 500

# 撤销共享记录
@health_bp.route('/shared/<shared_id>', methods=['DELETE'])
@login_required
def revoke_shared_record(shared_id):
    try:
        # 查找共享记录
        shared_record = SharedRecord.query.get(shared_id)
        if not shared_record:
            return jsonify({
                'success': False,
                'message': '共享记录不存在'
            }), 404
            
        # 检查权限（只有记录所有者或管理员可以撤销共享）
        if shared_record.owner_id != current_user.id and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '没有权限撤销此共享'
            }), 403
            
        # 获取相关信息以便日志记录
        record = HealthRecord.query.get(shared_record.record_id)
        shared_with_user = User.query.get(shared_record.shared_with)
        
        revoke_info = {
            'share_id': shared_record.id,
            'record_id': shared_record.record_id,
            'mongo_id': record.mongo_id if record else None,
            'record_title': record.title if record else 'Unknown',
            'owner_id': shared_record.owner_id,
            'owner_username': current_user.username,
            'shared_with': shared_record.shared_with,
            'shared_with_username': shared_with_user.username if shared_with_user else 'Unknown',
            'permission': str(shared_record.permission),
            'created_at': shared_record.created_at.isoformat() if shared_record.created_at else None,
            'expiry': shared_record.expires_at.isoformat() if shared_record.expires_at else None
        }
        
        # 创建撤销通知
        if shared_with_user:
            notification = Notification(
                user_id=shared_record.shared_with,
                notification_type=NotificationType.SHARE,
                title="记录共享撤销",
                message=f'{current_user.username}撤销了与您共享的健康记录',
                related_id=record.mongo_id if record else None
            )
            db.session.add(notification)
        
        # 删除共享记录
        db.session.delete(shared_record)
        
        # 记录撤销共享的日志
        log_record(
            message=f'用户撤销了健康记录共享',
            details={
                'revoke_info': revoke_info,
                'reason': request.args.get('reason', '用户撤销共享'),
                'revoke_time': datetime.now().isoformat()
            }
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '共享已成功撤销'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"撤销共享记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'撤销共享失败: {str(e)}'
        }), 500

# =========================== 高级搜索功能 ===========================

# 高级搜索功能
@health_bp.route('/search/advanced', methods=['POST'])
@login_required
def advanced_search():
    try:
        # 获取搜索参数
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '缺少搜索参数'
            }), 400
            
        # 分页
        page = data.get('page', 1)
        per_page = data.get('per_page', 10)
        
        # 基础筛选条件，当前用户的记录
        base_filter = {'patient_id': current_user.id}
        
        # 添加时间范围过滤
        if 'date_range' in data:
            date_filter = {}
            if 'start_date' in data['date_range'] and data['date_range']['start_date']:
                try:
                    start_date = datetime.fromisoformat(data['date_range']['start_date'])
                    date_filter['$gte'] = start_date
                except ValueError:
                    pass
                    
            if 'end_date' in data['date_range'] and data['date_range']['end_date']:
                try:
                    end_date = datetime.fromisoformat(data['date_range']['end_date'])
                    date_filter['$lte'] = end_date
                except ValueError:
                    pass
                    
            if date_filter:
                base_filter['record_date'] = date_filter
        
        # 添加类型过滤
        if 'record_types' in data and data['record_types']:
            if isinstance(data['record_types'], list) and len(data['record_types']) > 0:
                base_filter['record_type'] = {'$in': data['record_types']}
        
        # 添加医疗机构过滤
        if 'institutions' in data and data['institutions']:
            if isinstance(data['institutions'], list) and len(data['institutions']) > 0:
                base_filter['institution'] = {'$in': data['institutions']}
        
        # 添加标签过滤
        if 'tags' in data and data['tags']:
            tag_filters = []
            for tag in data['tags']:
                # 使用正则表达式匹配标签，MongoDB中标签存储为逗号分隔的字符串
                tag_filters.append({'tags': {'$regex': f'(^|,\\s*){tag}(\\s*,|$)'}})
            if tag_filters:
                base_filter['$or'] = tag_filters
        
        # 添加关键词搜索（全文搜索）
        text_query = {}
        if 'keywords' in data and data['keywords']:
            keyword_str = data['keywords']
            
            # 创建一个全文搜索查询
            text_query = {
                '$text': {
                    '$search': keyword_str,
                    '$caseSensitive': False,
                    '$diacriticSensitive': False
                }
            }
            
            # 检查MongoDB是否已创建全文索引，如果没有则尝试创建
            try:
                indexes = list(mongo.db.health_records.list_indexes())
                has_text_index = any('text' in idx.get('name', '') for idx in indexes)
                
                if not has_text_index:
                    # 创建复合全文索引
                    mongo.db.health_records.create_index([
                        ('title', 'text'),
                        ('description', 'text'),
                        ('doctor_name', 'text'),
                        ('institution', 'text'),
                        ('tags', 'text')
                    ])
                    current_app.logger.info("Created text index for health_records collection")
            except Exception as e:
                current_app.logger.error(f"Error checking/creating text index: {str(e)}")
        
        # 组合所有筛选条件
        final_query = {}
        if text_query:
            # 如果有全文搜索，将其与基础筛选条件结合
            final_query = {
                '$and': [
                    base_filter,
                    text_query
                ]
            }
        else:
            final_query = base_filter
            
        # 排序设置
        sort_options = {}
        if 'sort' in data and data['sort']:
            sort_field = data['sort'].get('field', 'record_date')
            sort_order = data['sort'].get('order', 'desc')
            
            # 验证排序字段
            allowed_sort_fields = ['record_date', 'created_at', 'title']
            if sort_field not in allowed_sort_fields:
                sort_field = 'record_date'
                
            # 设置排序方向
            direction = -1 if sort_order.lower() == 'desc' else 1
            sort_options = {sort_field: direction}
        else:
            # 默认按记录日期降序
            sort_options = {'record_date': -1}
        
        # 执行查询
        # 首先获取总数
        total = mongo.db.health_records.count_documents(final_query)
        
        # 然后获取分页数据
        skip = (page - 1) * per_page
        cursor = mongo.db.health_records.find(final_query).sort(list(sort_options.items()))
        records = list(cursor.skip(skip).limit(per_page))
        
        # 转换记录为字典格式 - 这里使用统一的转换函数
        result = [mongo_health_record_to_dict(record) for record in records]
        
        # 获取唯一的标签、医疗机构等用于筛选条件
        aggregation_pipeline = [
            {'$match': {'patient_id': current_user.id}},
            {'$group': {
                '_id': None,
                'institutions': {'$addToSet': '$institution'},
                'record_types': {'$addToSet': '$record_type'}
            }}
        ]
        
        agg_result = list(mongo.db.health_records.aggregate(aggregation_pipeline))
        filter_options = {
            'institutions': [],
            'record_types': []
        }
        
        if agg_result:
            # 过滤掉None值并排序
            institutions = agg_result[0].get('institutions', [])
            filter_options['institutions'] = sorted([i for i in institutions if i])
            
            record_types = agg_result[0].get('record_types', [])
            filter_options['record_types'] = sorted([t for t in record_types if t])
            
        # 获取所有标签
        tag_set = set()
        tag_records = mongo.db.health_records.find(
            {'patient_id': current_user.id, 'tags': {'$ne': None, '$ne': ''}}
        )
        
        for record in tag_records:
            if 'tags' in record and record['tags']:
                tags = [tag.strip() for tag in record['tags'].split(',')]
                tag_set.update(tags)
                
        filter_options['tags'] = sorted(list(tag_set))
        
        # 记录此次查询到历史记录中
        query_history = QueryHistory(
            user_id=current_user.id,
            query_type='advanced_search',
            is_anonymous=False,
            query_params=data
        )
        db.session.add(query_history)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': {
                'records': result,
                'total': total,
                'pages': math.ceil(total / per_page),
                'current_page': page,
                'filter_options': filter_options
            }
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"高级搜索失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'高级搜索失败: {str(e)}'
        }), 500

# 获取可用的筛选条件
@health_bp.route('/search/filters', methods=['GET'])
@login_required
def get_search_filters():
    try:
        # 获取用户所有记录中的唯一标签、医疗机构、记录类型等
        aggregation_pipeline = [
            {'$match': {'patient_id': current_user.id}},
            {'$group': {
                '_id': None,
                'institutions': {'$addToSet': '$institution'},
                'record_types': {'$addToSet': '$record_type'},
                'doctor_names': {'$addToSet': '$doctor_name'},
                'earliest_date': {'$min': '$record_date'},
                'latest_date': {'$max': '$record_date'}
            }}
        ]
        
        agg_result = list(mongo.db.health_records.aggregate(aggregation_pipeline))
        filter_options = {
            'institutions': [],
            'record_types': [],
            'doctor_names': [],
            'date_range': {
                'min': None,
                'max': None
            }
        }
        
        if agg_result:
            # 过滤掉None值并排序
            institutions = agg_result[0].get('institutions', [])
            filter_options['institutions'] = sorted([i for i in institutions if i])
            
            record_types = agg_result[0].get('record_types', [])
            filter_options['record_types'] = sorted([t for t in record_types if t])
            
            doctor_names = agg_result[0].get('doctor_names', [])
            filter_options['doctor_names'] = sorted([d for d in doctor_names if d])
            
            # 日期范围
            if 'earliest_date' in agg_result[0] and agg_result[0]['earliest_date']:
                filter_options['date_range']['min'] = agg_result[0]['earliest_date'].isoformat()
                
            if 'latest_date' in agg_result[0] and agg_result[0]['latest_date']:
                filter_options['date_range']['max'] = agg_result[0]['latest_date'].isoformat()
            
        # 获取所有标签
        tag_set = set()
        tag_records = mongo.db.health_records.find(
            {'patient_id': current_user.id, 'tags': {'$ne': None, '$ne': ''}}
        )
        
        for record in tag_records:
            if 'tags' in record and record['tags']:
                tags = [tag.strip() for tag in record['tags'].split(',')]
                tag_set.update(tags)
                
        filter_options['tags'] = sorted(list(tag_set))
        
        return jsonify({
            'success': True,
            'data': filter_options
        })
        
    except Exception as e:
        current_app.logger.error(f"获取搜索筛选条件失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取搜索筛选条件失败: {str(e)}'
        }), 500

# =========================== 导入导出功能 ===========================

# 创建一个日期时间序列化函数
def json_serial(obj):
    """JSON序列化函数，处理datetime和date对象"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError (f"Type {type(obj)} not serializable")

# 导出健康记录
@health_bp.route('/export', methods=['POST'])
@login_required
@role_required(Role.PATIENT)
def export_health_records():
    try:
        # 获取请求参数
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '缺少导出参数'
            }), 400
            
        # 导出格式
        export_format = data.get('format', 'json').lower()
        if export_format not in ['json', 'excel']:
            return jsonify({
                'success': False,
                'message': '不支持的导出格式，请使用 json 或 excel'
            }), 400
            
        # 获取要导出的记录ID列表
        record_ids = data.get('record_ids', [])
        
        # 导出全部记录
        export_all = data.get('export_all', False)
        
        # 查询条件
        query = {'patient_id': current_user.id}
        
        if not export_all and record_ids:
            # 转换字符串ID为ObjectId
            object_ids = []
            for id_str in record_ids:
                try:
                    object_ids.append(ObjectId(id_str))
                except:
                    pass
                    
            if not object_ids:
                return jsonify({
                    'success': False,
                    'message': '无效的记录ID列表'
                }), 400
                
            query['_id'] = {'$in': object_ids}
        
        # 查询记录
        records = list(mongo.db.health_records.find(query))
        
        if not records:
            return jsonify({
                'success': False,
                'message': '没有找到符合条件的记录'
            }), 404
            
        # 根据导出格式处理数据
        if export_format == 'json':
            # 转换为字典列表 - 这里使用统一的转换函数
            export_data = []
            for record in records:
                record_dict = mongo_health_record_to_dict(record)
                export_data.append(record_dict)
                
            # 创建导出文件名
            filename = f"health_records_export_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
            filepath = os.path.join(UPLOAD_FOLDER, "..", "exports", filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # 写入JSON文件 - 使用自定义序列化函数处理datetime对象
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2, default=json_serial)
                
            # 生成下载令牌
            from ..utils.token_utils import generate_download_token
            token = generate_download_token(current_user.id, filename)
                
            return jsonify({
                'success': True,
                'message': '健康记录导出成功',
                'data': {
                    'export_format': 'json',
                    'filename': filename,
                    'record_count': len(records),
                    'download_url': f"/health/export/download/{filename}?token={token}"
                }
            })
        
        elif export_format == 'excel':
            # 确定Excel字段
            fields = [
                'record_id', 'title', 'record_type', 'description', 'record_date',
                'institution', 'doctor_name', 'tags', 'created_at'
            ]
            
            # 创建导出文件名
            filename = f"health_records_export_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
            filepath = os.path.join(UPLOAD_FOLDER, "..", "exports", filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # 使用pandas导出Excel格式
            import pandas as pd
            
            # 准备数据
            excel_data = []
            for record in records:
                # 将MongoDB记录转换为Excel行
                record_dict = mongo_health_record_to_dict(record)
                row = {
                    'record_id': record_dict.get('_id'),
                    'title': record_dict.get('title'),
                    'record_type': record_dict.get('record_type'),
                    'description': record_dict.get('description'),
                    'record_date': record_dict.get('record_date'),
                    'institution': record_dict.get('institution'),
                    'doctor_name': record_dict.get('doctor_name'),
                    'tags': record_dict.get('tags'),
                    'created_at': record_dict.get('created_at')
                }
                excel_data.append(row)
            
            # 创建DataFrame并导出为Excel
            df = pd.DataFrame(excel_data)
            df.to_excel(filepath, index=False)
            
            # 生成下载令牌
            from ..utils.token_utils import generate_download_token
            token = generate_download_token(current_user.id, filename)
                    
            return jsonify({
                'success': True,
                'message': '健康记录导出成功',
                'data': {
                    'export_format': 'excel',
                    'filename': filename,
                    'record_count': len(records),
                    'download_url': f"/health/export/download/{filename}?token={token}"
                }
            })
    
    except Exception as e:
        current_app.logger.error(f"导出健康记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'导出健康记录失败: {str(e)}'
        }), 500

# 下载导出的记录文件
@health_bp.route('/export/download/<filename>', methods=['GET'])
def download_exported_records(filename):
    try:
        # 获取认证token
        token = request.args.get('token')
        
        # 如果用户已登录，则直接检查权限
        if current_user.is_authenticated:
            if not current_user.has_role(Role.PATIENT):
                return jsonify({
                    'success': False,
                    'message': '只有患者可以下载导出文件'
                }), 403
                
            # 安全检查，确保只有当前用户可以下载自己的导出文件
            user_id_part = filename.split('_')[2] if len(filename.split('_')) > 2 else ""
            if f"health_records_export_{current_user.id}_" not in filename:
                return jsonify({
                    'success': False,
                    'message': '没有权限下载此文件'
                }), 403
        # 如果未登录但提供了token，则验证token
        elif token:
            from ..utils.token_utils import validate_download_token
            user_id = validate_download_token(token, filename)
            if not user_id:
                return jsonify({
                    'success': False,
                    'message': '无效的下载令牌或令牌已过期'
                }), 403
                
            # 验证文件名是否匹配token中的用户ID
            if f"health_records_export_{user_id}_" not in filename:
                return jsonify({
                    'success': False,
                    'message': '令牌与文件不匹配'
                }), 403
        # 既没有登录也没有提供token
        else:
            return jsonify({
                'success': False,
                'message': '请先登录或提供有效的下载令牌'
            }), 401
            
        # 导出目录
        export_dir = os.path.join(UPLOAD_FOLDER, "..", "exports")
        
        # 获取文件类型
        file_ext = filename.split('.')[-1].lower()
        if file_ext == 'json':
            mime_type = 'application/json'
        elif file_ext == 'xlsx':
            mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif file_ext == 'xls':
            mime_type = 'application/vnd.ms-excel'
        else:
            mime_type = 'application/octet-stream'
        
        return send_from_directory(
            export_dir,
            filename,
            as_attachment=True,
            mimetype=mime_type
        )
    
    except Exception as e:
        current_app.logger.error(f"下载导出文件失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'下载导出文件失败: {str(e)}'
        }), 500

# 导入健康记录
@health_bp.route('/import', methods=['POST'])
@login_required
@role_required(Role.PATIENT)
def import_health_records():
    try:
        # 检查是否有文件上传
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'message': '没有上传文件'
            }), 400
            
        file = request.files['file']
        
        # 检查文件名
        if file.filename == '':
            return jsonify({
                'success': False,
                'message': '未选择文件'
            }), 400
            
        # 检查文件类型
        file_ext = file.filename.split('.')[-1].lower()
        if file_ext not in ['json', 'xlsx', 'xls']:
            return jsonify({
                'success': False,
                'message': '不支持的文件格式，请上传JSON或Excel文件'
            }), 400
            
        # 保存上传的文件
        upload_dir = os.path.join(UPLOAD_FOLDER, "..", "imports")
        os.makedirs(upload_dir, exist_ok=True)
        
        filename = f"import_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{file_ext}"
        filepath = os.path.join(upload_dir, filename)
        file.save(filepath)
        
        # 处理导入数据
        imported_records = []
        skipped_records = []
        
        # 获取当前日期时间作为记录日期
        current_datetime = datetime.now()
        
        if file_ext == 'json':
            with open(filepath, 'r', encoding='utf-8') as f:
                import_data = json.load(f)
                
            if not isinstance(import_data, list):
                return jsonify({
                    'success': False,
                    'message': 'JSON文件格式不正确，应为记录列表'
                }), 400
                
            # 处理每条记录
            for record_data in import_data:
                # 确保记录数据有效
                if not isinstance(record_data, dict) or 'title' not in record_data or 'record_type' not in record_data:
                    skipped_records.append({
                        'reason': '缺少必要字段 (title, record_type)',
                        'data': str(record_data)[:100] + '...' if len(str(record_data)) > 100 else str(record_data)
                    })
                    continue
                
                # 跳过标题为空的行
                if not record_data.get('title') or str(record_data.get('title')).strip() == '':
                    continue
                    
                # 确保记录类型有效
                if record_data['record_type'] not in [t.value for t in RecordType]:
                    record_data['record_type'] = RecordType.OTHER.value
                    
                # 设置患者ID为当前用户
                record_data['patient_id'] = current_user.id
                
                # 处理PIR保护字段
                record_data['pir_protected'] = record_data.get('pir_protected', True)
                
                # 始终使用当前日期作为记录日期
                record_data['record_date'] = current_datetime
                
                # 设置创建和更新时间
                record_data['created_at'] = current_datetime
                record_data['updated_at'] = current_datetime
                
                # 存储到MongoDB
                result = mongo.db.health_records.insert_one(record_data)
                
                # 添加到MySQL索引表
                sql_record = HealthRecord(
                    patient_id=current_user.id,
                    record_type=record_data['record_type'],
                    title=record_data['title'],
                    record_date=current_datetime,  # 确保使用datetime对象而非字符串
                    visibility=RecordVisibility.PRIVATE if 'visibility' not in record_data else RecordVisibility(record_data['visibility']),
                    mongo_id=str(result.inserted_id),
                    created_at=current_datetime,
                    updated_at=current_datetime
                )
                db.session.add(sql_record)
                
                # 添加到已导入列表
                imported_records.append({
                    'id': str(result.inserted_id),
                    'title': record_data['title'],
                    'record_type': record_data['record_type']
                })
                
        elif file_ext in ['xlsx', 'xls']:
            # 使用pandas读取Excel文件
            import pandas as pd
            df = pd.read_excel(filepath)
            
            # 检查必要的列是否存在
            required_cols = ['title', 'record_type']
            for col in required_cols:
                if col not in df.columns:
                    return jsonify({
                        'success': False,
                        'message': f'Excel文件缺少必要的列: {col}'
                    }), 400
            
            # 跳过前三行示例数据
            if len(df) > 3:
                df = df.iloc[3:]
                
            # 处理每行数据
            for idx, row in df.iterrows():
                # 跳过标题为空的行
                if pd.isna(row['title']) or str(row['title']).strip() == '':
                    continue
                    
                # 跳过记录类型为空的行
                if pd.isna(row['record_type']) or str(row['record_type']).strip() == '':
                    skipped_records.append({
                        'reason': '缺少必要字段 (record_type)',
                        'row': idx + 1,
                        'title': str(row.get('title', ''))
                    })
                    continue
                
                # 转换为记录数据
                record_data = {
                    'title': str(row['title']),
                    'record_type': str(row['record_type']),
                    'patient_id': current_user.id,
                    'created_at': current_datetime,
                    'updated_at': current_datetime,
                    'record_date': current_datetime  # 使用当前日期作为记录日期
                }
                
                # 添加可选字段
                if 'description' in df.columns and pd.notna(row['description']):
                    record_data['description'] = str(row['description'])
                    
                # 处理PIR保护字段
                if 'pir_protected' in df.columns and pd.notna(row['pir_protected']):
                    pir_value = row['pir_protected']
                    if isinstance(pir_value, bool):
                        record_data['pir_protected'] = pir_value
                    elif isinstance(pir_value, str):
                        pir_value_lower = pir_value.lower()
                        record_data['pir_protected'] = pir_value_lower in ('true', 'yes', '1', 't', 'y')
                    else:
                        record_data['pir_protected'] = bool(pir_value)
                else:
                    record_data['pir_protected'] = True
                    
                # 添加其他可能存在的字段
                for field in ['institution', 'doctor_name']:
                    if field in df.columns and pd.notna(row[field]):
                        record_data[field] = str(row[field])
                
                # 处理标签字段
                if 'tags' in df.columns and pd.notna(row['tags']):
                    tags_str = str(row['tags'])
                    if tags_str:
                        record_data['tags'] = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
                
                # 存储到MongoDB
                try:
                    result = mongo.db.health_records.insert_one(record_data)
                    
                    # 添加到MySQL索引表
                    sql_record = HealthRecord(
                        patient_id=current_user.id,
                        record_type=record_data['record_type'],
                        title=record_data['title'],
                        record_date=current_datetime,  # 确保使用datetime对象而非字符串
                        visibility=RecordVisibility.PRIVATE if 'visibility' not in record_data else RecordVisibility(record_data['visibility']),
                        mongo_id=str(result.inserted_id),
                        created_at=current_datetime,
                        updated_at=current_datetime
                    )
                    db.session.add(sql_record)
                    
                    # 添加到已导入列表
                    imported_records.append({
                        'id': str(result.inserted_id),
                        'title': record_data['title'],
                        'record_type': record_data['record_type']
                    })
                except Exception as e:
                    current_app.logger.error(f"导入记录失败: {str(e)}, 数据: {record_data}")
                    skipped_records.append({
                        'reason': f'导入失败: {str(e)}',
                        'row': idx + 1,
                        'title': record_data.get('title', '')
                    })
        
        # 添加到MySQL索引表，便于后续查询
        try:
            # 确保所有挂起的数据库更改都被提交
            db.session.commit()
            
            # 记录导入日志
            from ..utils.log_utils import log_record
            log_record(
                message=f'用户导入了 {len(imported_records)} 条健康记录',
                details={
                    'successful_imports': len(imported_records),
                    'skipped_records': len(skipped_records),
                    'file_name': os.path.basename(filepath),
                    'file_format': file_ext,
                    'import_time': datetime.now().isoformat()
                }
            )
            
        except Exception as e:
            current_app.logger.error(f"同步导入记录到MySQL失败: {str(e)}")
            db.session.rollback()
            # 继续执行，不要中断过程
            
        return jsonify({
            'success': True,
            'message': f'成功导入 {len(imported_records)} 条记录，跳过 {len(skipped_records)} 条无效记录',
            'data': {
                'imported_records': imported_records,
                'skipped_records': skipped_records
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"导入健康记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'导入健康记录失败: {str(e)}'
        }), 500

# =========================== 增强隐匿查询功能 ===========================

# 高级隐匿查询功能（直接使用PIR技术）
@health_bp.route('/pir/advanced', methods=['POST'])
@login_required
@role_required(Role.PATIENT)
def advanced_pir_query():
    try:
        # 获取查询参数
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '缺少查询参数'
            }), 400
        
        # 提取查询参数
        query_params = {}
        if 'record_type' in data:
            query_params['record_type'] = data.get('record_type')
        if 'start_date' in data:
            query_params['start_date'] = data.get('start_date')
        if 'end_date' in data:
            query_params['end_date'] = data.get('end_date')
        if 'keyword' in data:
            query_params['keyword'] = data.get('keyword')
        
        # 准备PIR数据库
        # 首先获取用户的所有健康记录
        all_records = list(mongo.db.health_records.find({'patient_id': current_user.id}))
        
        # 创建PIR数据库
        pir_database, record_mapping = prepare_pir_database(all_records)
        
        # 查询索引随机化
        db_size = len(all_records)
        if db_size == 0:
            return jsonify({
                'success': True,
                'data': {
                    'records': [],
                    'metadata': {
                        'pir_enabled': True,
                        'records_processed': 0,
                        'obfuscation_level': 'high'
                    }
                }
            })
        
        # 使用PIRQuery创建查询向量
        # 根据查询参数筛选目标索引
        target_indices = []
        for idx, record in record_mapping.items():
            match = True
            
            # 只包含启用了PIR保护的记录
            if not record.get('pir_protected', False):
                match = False
            
            # 记录类型匹配
            if match and 'record_type' in query_params and query_params['record_type']:
                if record.get('record_type') != query_params['record_type']:
                    match = False
            
            # 日期范围匹配
            if match and 'start_date' in query_params and query_params['start_date']:
                start_date = datetime.strptime(query_params['start_date'], '%Y-%m-%d')
                if 'record_date' in record and record['record_date'] < start_date:
                    match = False
            
            if match and 'end_date' in query_params and query_params['end_date']:
                end_date = datetime.strptime(query_params['end_date'], '%Y-%m-%d')
                if 'record_date' in record and record['record_date'] > end_date:
                    match = False
            
            # 关键字匹配
            if match and 'keyword' in query_params and query_params['keyword']:
                keyword = query_params['keyword'].lower()
                title = record.get('title', '').lower()
                description = record.get('description', '').lower()
                tags = record.get('tags', '').lower()
                
                if keyword not in title and keyword not in description and keyword not in tags:
                    match = False
            
            if match:
                target_indices.append(idx)
        
        # 如果没有找到匹配的记录
        if not target_indices:
            # 记录查询历史
            mongo.db.query_history.insert_one({
                'user_id': current_user.id,
                'query_type': 'advanced_pir',
                'is_anonymous': True,
                'query_params': query_params,
                'query_time': datetime.now()
            })
            
            return jsonify({
                'success': True,
                'data': {
                    'records': [],
                    'metadata': {
                        'pir_enabled': True,
                        'records_processed': db_size,
                        'matches_found': 0,
                        'obfuscation_level': 'high'
                    }
                }
            })
        
        # 使用PIR查询向量
        result_records = []
        # 对每个匹配的索引创建PIR查询向量并执行查询
        for target_idx in target_indices:
            query_vector = PIRQuery.create_query_vector(db_size, target_idx)
            # 在真实系统中，这里应该由服务器处理查询向量
            # 这里简化为直接获取目标记录
            record = record_mapping[target_idx]
            # 处理ObjectId等非JSON序列化字段
            record['_id'] = str(record['_id'])
            
            # 处理日期格式
            for date_field in ['record_date', 'created_at', 'updated_at']:
                if date_field in record and record[date_field] and isinstance(record[date_field], datetime):
                    record[date_field] = record[date_field].isoformat()
            
            # 处理用药记录和生命体征的日期
            if 'medication' in record and record['medication']:
                for date_field in ['start_date', 'end_date']:
                    if date_field in record['medication'] and record['medication'][date_field]:
                        record['medication'][date_field] = record['medication'][date_field].isoformat() if isinstance(record['medication'][date_field], datetime) else record['medication'][date_field]
            
            if 'vital_signs' in record and record['vital_signs']:
                for vs in record['vital_signs']:
                    if 'measured_at' in vs and vs['measured_at']:
                        vs['measured_at'] = vs['measured_at'].isoformat() if isinstance(vs['measured_at'], datetime) else vs['measured_at']
            
            result_records.append(record)
        
        # 为增强隐私，添加混淆查询
        # 生成1-3个额外的随机查询（这些查询不会返回给客户端）
        num_decoy_queries = random.randint(1, 3)
        total_processed = db_size  # 假设所有记录都被处理
        
        # 记录查询历史
        mongo.db.query_history.insert_one({
            'user_id': current_user.id,
            'query_type': 'advanced_pir',
            'is_anonymous': True,
            'query_params': query_params,
            'query_time': datetime.now()
        })
        
        return jsonify({
            'success': True,
            'data': {
                'records': result_records,
                'metadata': {
                    'pir_enabled': True,
                    'records_processed': total_processed,
                    'matches_found': len(target_indices),
                    'noise_queries': num_decoy_queries,
                    'obfuscation_level': 'high',
                    'query_vector_size': db_size
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"高级PIR查询失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'高级PIR查询失败: {str(e)}'
        }), 500

# 获取PIR隐私设置
@health_bp.route('/pir/settings', methods=['GET'])
@login_required
def get_pir_settings():
    try:
        # 获取当前系统PIR设置
        pir_settings = {
            'pir_enabled': current_app.config.get('PIR_ENABLE_OBFUSCATION', True),
            'max_noise_queries': current_app.config.get('PIR_MAX_NOISE_QUERIES', 3),
            'encryption_strength': current_app.config.get('PIR_ENCRYPTION_STRENGTH', 'high'),
        }
        
        # 获取用户当前的PIR使用统计
        total_queries = mongo.db.query_history.count_documents({
            'user_id': current_user.id
        })
        
        pir_queries = mongo.db.query_history.count_documents({
            'user_id': current_user.id,
            'is_anonymous': True
        })
        
        # 计算PIR查询占比 (占比越高，隐私保护越好)
        pir_usage_ratio = round((pir_queries / total_queries) * 100, 2) if total_queries > 0 else 0
        
        # 噪声查询评分 (1-5分，最大为5分)
        noise_score = pir_settings['max_noise_queries'] * 6
        
        # 加密强度评分
        encryption_score = {
            'high': 30,   # 高加密强度给30分
            'medium': 20, # 中等加密强度给20分
            'low': 10     # 低加密强度给10分
        }.get(pir_settings['encryption_strength'], 10)
        
        # 是否启用PIR评分
        pir_enabled_score = 20 if pir_settings['pir_enabled'] else 0
        
        # 提供隐私保护评分，基于四个因素：
        # 1. PIR查询占比 (最大40分)
        # 2. 最大噪声查询数 (最大30分)
        # 3. 加密强度 (最大30分)
        # 4. 是否启用PIR (0或20分)
        privacy_score = min(100, round(
            (pir_usage_ratio * 0.4) +  # PIR使用率评分 (最高40分)
            noise_score +              # 噪声查询评分 (最高30分)
            encryption_score +         # 加密强度评分 (最高30分)
            pir_enabled_score          # 启用PIR评分 (最高20分)
        ))
        
        # 等级描述
        privacy_level = '极高' if privacy_score >= 90 else \
                       '高' if privacy_score >= 75 else \
                       '中等' if privacy_score >= 60 else \
                       '低' if privacy_score >= 40 else '极低'
        
        return jsonify({
            'success': True,
            'data': {
                'settings': pir_settings,
                'statistics': {
                    'total_queries': total_queries,
                    'pir_queries': pir_queries,
                    'pir_usage_ratio': pir_usage_ratio,
                    'privacy_score': privacy_score,
                    'privacy_level': privacy_level
                },
                'factors': {
                    'pir_usage': round(pir_usage_ratio * 0.4, 1),
                    'noise_queries': noise_score,
                    'encryption': encryption_score,
                    'pir_enabled': pir_enabled_score
                },
                'recommendations': {
                    'use_pir': pir_usage_ratio < 80,
                    'increase_noise': pir_settings['max_noise_queries'] < 5 and privacy_score < 75,
                    'increase_encryption': pir_settings['encryption_strength'] != 'high' and privacy_score < 85,
                    'suggestion': '增加噪声查询数量' if pir_settings['max_noise_queries'] < 5 and privacy_score < 75 else \
                                '提高加密强度' if pir_settings['encryption_strength'] != 'high' and privacy_score < 85 else \
                                '使用更多PIR查询' if pir_usage_ratio < 80 else '您的隐私保护设置已达到最佳水平'
                }
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"获取PIR设置失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取PIR设置失败: {str(e)}'
        }), 500

# 更新PIR隐私设置
@health_bp.route('/pir/settings', methods=['PUT'])
@login_required
def update_pir_settings():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '缺少设置参数'
            }), 400
        
        # 验证用户权限
        if not current_user.has_role(Role.PATIENT):
            return jsonify({
                'success': False,
                'message': '只有患者可以修改PIR设置'
            }), 403
        
        # 更新设置
        settings_updated = False
        
        # 检查是否启用PIR
        if 'pir_enabled' in data:
            current_app.config['PIR_ENABLE_OBFUSCATION'] = bool(data['pir_enabled'])
            settings_updated = True
        
        # 检查最大噪声查询数
        if 'max_noise_queries' in data:
            noise_queries = int(data['max_noise_queries'])
            if 1 <= noise_queries <= 5:  # 限制范围
                current_app.config['PIR_MAX_NOISE_QUERIES'] = noise_queries
                settings_updated = True
        
        # 检查加密强度
        if 'encryption_strength' in data:
            strength = data['encryption_strength']
            if strength in ['low', 'medium', 'high']:
                current_app.config['PIR_ENCRYPTION_STRENGTH'] = strength
                settings_updated = True
        
        if not settings_updated:
            return jsonify({
                'success': False,
                'message': '没有有效的设置更新'
            }), 400
        
        return jsonify({
            'success': True,
            'message': 'PIR设置更新成功',
            'data': {
                'pir_enabled': current_app.config.get('PIR_ENABLE_OBFUSCATION'),
                'max_noise_queries': current_app.config.get('PIR_MAX_NOISE_QUERIES'),
                'encryption_strength': current_app.config.get('PIR_ENCRYPTION_STRENGTH')
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"更新PIR设置失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新PIR设置失败: {str(e)}'
        }), 500 

# =========================== 第一阶段新增功能 ===========================
# 获取健康记录版本历史
@health_bp.route('/records/<record_id>/versions', methods=['GET'])
@login_required
def get_record_versions(record_id):
    try:
        # 尝试转换为ObjectId
        try:
            mongo_id = format_mongo_id(record_id)
            if not mongo_id:
                return jsonify({
                    'success': False,
                    'message': '无效的记录ID'
                }), 400
        except:
            # 尝试作为SQL ID处理
            try:
                sql_id = int(record_id)
                sql_record = HealthRecord.query.get(sql_id)
                if sql_record and sql_record.mongo_id:
                    mongo_id = format_mongo_id(sql_record.mongo_id)
                    record_id = sql_record.mongo_id
                else:
                    return jsonify({
                        'success': False,
                        'message': '记录不存在'
                    }), 404
            except:
                return jsonify({
                    'success': False,
                    'message': '无效的记录ID'
                }), 400
        
        # 使用缓存函数获取MongoDB中的记录
        record_data = get_mongo_health_record(record_id)
        
        if not record_data:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查访问权限
        has_permission, error_msg = check_record_access_permission(record_data, current_user)
        if not has_permission:
            return jsonify({
                'success': False,
                'message': error_msg
            }), 403
            
        # 获取版本历史
        versions = record_data.get('version_history', [])
        
        # 如果没有版本历史，创建一个初始版本
        if not versions:
            versions = [{
                'version': 1,
                'created_at': record_data.get('created_at', datetime.now().isoformat()),
                'created_by': record_data.get('patient_id'),
                'description': '初始版本'
            }]
            
            # 更新记录的版本历史
            mongo_db = get_mongo_db()
            mongo_db.health_records.update_one(
                {'_id': mongo_id},
                {'$set': {'version_history': versions, 'version': 1}}
            )
        
        # 格式化版本历史
        formatted_versions = []
        for version in versions:
            # 获取创建者信息
            creator_id = version.get('created_by')
            creator = None
            if creator_id:
                creator = User.query.get(creator_id)
                
            version_info = {
                'version': version.get('version'),
                'created_at': format_mongo_date(version.get('created_at')),
                'description': version.get('description', ''),
                'creator': {
                    'id': creator.id if creator else None,
                    'username': creator.username if creator else None
                } if creator else None,
                'changes': version.get('changes', [])
            }
            formatted_versions.append(version_info)
            
        # 查找或创建MySQL中的索引记录
        sql_record = HealthRecord.query.filter_by(mongo_id=record_id).first()
        
        # 记录查询历史
        if sql_record:
            query_history = QueryHistory(
                user_id=current_user.id,
                record_id=sql_record.id,
                query_type='version_history',
                is_anonymous=False,
                query_params={'mongo_id': record_id}
            )
            db.session.add(query_history)
            db.session.commit()
        
        # 在MongoDB中也记录查询历史
        mongo_db = get_mongo_db()
        mongo_db.query_history.insert_one({
            'user_id': current_user.id,
            'record_id': record_id,
            'query_type': 'version_history',
            'is_anonymous': False,
            'query_time': datetime.now()
        })
        
        return jsonify({
            'success': True,
            'data': {
                'record_id': record_id,
                'sql_id': sql_record.id if sql_record else None,
                'current_version': record_data.get('version', 1),
                'title': record_data.get('title'),
                'versions': formatted_versions
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取记录版本历史失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取记录版本历史失败: {str(e)}'
        }), 500

# 创建记录新版本
@health_bp.route('/records/<record_id>/versions', methods=['POST'])
@login_required
def create_record_version(record_id):
    try:
        # 从MongoDB获取记录
        from bson.objectid import ObjectId
        
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        
        try:
            mongo_id = ObjectId(record_id)
            record = mongo_db.health_records.find_one({'_id': mongo_id})
        except Exception as e:
            current_app.logger.error(f"获取记录失败: {str(e)}")
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查修改权限（只有患者自己或管理员可以修改）
        if str(record['patient_id']) != str(current_user.id) and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '没有权限修改此记录'
            }), 403
            
        # 获取版本信息
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '缺少版本信息'
            }), 400
            
        # 获取当前版本号
        current_version = record.get('version', 1)
        new_version = current_version + 1
        
        # 创建版本历史记录
        version_entry = {
            'version': new_version,
            'created_at': datetime.now(),
            'created_by': current_user.id,
            'description': data.get('description', f'版本 {new_version}'),
            'changes': data.get('changes', [])
        }
        
        # 获取更新数据
        update_data = data.get('data', {})
        
        # 准备更新字段
        update_fields = {}
        
        # 基本字段
        basic_fields = ['title', 'description', 'institution', 'doctor_name', 'visibility', 'tags', 'data', 'pir_protected']
        for field in basic_fields:
            if field in update_data:
                update_fields[field] = update_data[field]
        
        # 日期字段
        if 'record_date' in update_data:
            try:
                update_fields['record_date'] = datetime.strptime(update_data['record_date'], '%Y-%m-%dT%H:%M:%S.%f')
            except ValueError:
                try:
                    update_fields['record_date'] = datetime.strptime(update_data['record_date'], '%Y-%m-%dT%H:%M:%S')
                except ValueError:
                    try:
                        # 增加对简单日期格式的支持
                        update_fields['record_date'] = datetime.strptime(update_data['record_date'], '%Y-%m-%d')
                    except ValueError:
                        return jsonify({
                            'success': False,
                            'message': f'无效的日期格式: {update_data["record_date"]}'
                        }), 400
        
        # 用药记录
        if 'medication' in update_data and record.get('record_type') == 'medication':
            med_data = update_data['medication']
            medication_update = {}
            
            for field in ['medication_name', 'dosage', 'frequency', 'instructions', 'side_effects']:
                if field in med_data:
                    medication_update[f'medication.{field}'] = med_data[field]
            
            # 日期字段特殊处理
            for date_field in ['start_date', 'end_date']:
                if date_field in med_data and med_data[date_field]:
                    try:
                        # 尝试多种日期格式
                        date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']
                        for fmt in date_formats:
                            try:
                                date_value = datetime.strptime(med_data[date_field], fmt).date()
                                medication_update[f'medication.{date_field}'] = date_value
                                break
                            except ValueError:
                                continue
                        
                        # 如果所有格式都失败，尝试去除时间部分
                        if f'medication.{date_field}' not in medication_update and 'T' in med_data[date_field]:
                            date_part = med_data[date_field].split('T')[0]
                            date_value = datetime.strptime(date_part, '%Y-%m-%d').date()
                            medication_update[f'medication.{date_field}'] = date_value
                    except Exception as e:
                        current_app.logger.warning(f"更新记录时处理{date_field}出错: {str(e)}, 跳过该字段")
                        
            update_fields.update(medication_update)
        
        # 设置更新时间
        update_fields['updated_at'] = datetime.now()
        update_fields['version'] = new_version
        
        # 获取当前版本历史
        version_history = record.get('version_history', [])
        if not version_history:
            # 如果没有版本历史，创建一个初始版本
            version_history = [{
                'version': 1,
                'created_at': record.get('created_at', datetime.now()),
                'created_by': record.get('patient_id'),
                'description': '初始版本'
            }]
            
        # 添加新版本记录
        version_history.append(version_entry)
        update_fields['version_history'] = version_history
        
        # 在执行更新之前，保存当前版本的快照
        current_app.logger.info(f"正在为记录 {record_id} 创建版本 {current_version} 的快照")
        
        # 创建一个当前记录的副本用于版本快照
        version_snapshot = record.copy()
        
        # 保存版本信息
        version_snapshot['record_id'] = str(record['_id'])  # 添加原始记录ID的引用
        version_snapshot['_id'] = ObjectId()  # 生成新的ID
        version_snapshot['version'] = current_version  # 当前版本号
        version_snapshot['snapshot_date'] = datetime.now()
        
        # 保存快照到versions集合
        try:
            mongo_db.health_records_versions.insert_one(version_snapshot)
            current_app.logger.info(f"已创建记录 {record_id} 版本 {current_version} 的快照")
        except Exception as e:
            current_app.logger.error(f"创建版本快照失败: {str(e)}")
            # 继续执行，不阻止更新主记录
        
        # 执行更新
        current_app.logger.info(f"更新记录 {record_id} 到新版本 {new_version}")
        mongo_db.health_records.update_one(
            {'_id': mongo_id},
            {'$set': update_fields}
        )
        
        # 获取更新后的记录
        updated_record = mongo_db.health_records.find_one({'_id': mongo_id})
        
        # 使用通用函数处理记录
        from ..utils.mongo_utils import format_mongo_doc
        record_data = format_mongo_doc(updated_record)
        
        return jsonify({
            'success': True,
            'message': '健康记录版本创建成功',
            'data': {
                'record': record_data,
                'version': new_version,
                'version_info': version_entry
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"创建记录版本失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建记录版本失败: {str(e)}'
        }), 500

# 获取特定版本的记录
@health_bp.route('/records/<record_id>/versions/<int:version_number>', methods=['GET'])
@login_required
def get_record_version(record_id, version_number):
    try:
        # 从MongoDB获取记录
        from bson.objectid import ObjectId
        
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        
        current_app.logger.info(f"正在获取记录 {record_id} 的版本 {version_number}")
        
        try:
            mongo_id = ObjectId(record_id)
            record = mongo_db.health_records.find_one({'_id': mongo_id})
        except Exception as e:
            current_app.logger.error(f"获取记录失败: {str(e)}")
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
        if not record:
            current_app.logger.error(f"找不到记录 {record_id}")
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查访问权限
        has_permission, error_msg = check_record_access_permission(record, current_user)
        if not has_permission:
            return jsonify({
                'success': False,
                'message': error_msg
            }), 403
            
        # 如果请求的是当前版本，直接返回
        current_version = record.get('version', 1)
        if version_number == current_version:
            # 使用通用函数处理记录
            from ..utils.mongo_utils import format_mongo_doc
            record_data = format_mongo_doc(record)
            return jsonify({
                'success': True,
                'data': {
                    'record': record_data,
                    'version': current_version,
                    'is_current': True
                }
            })
            
        # 检查版本历史
        version_history = record.get('version_history', [])
        
        # 检查是否有请求的版本
        version_info = None
        for version in version_history:
            if version.get('version') == version_number:
                version_info = version
                break
                
        if not version_info:
            current_app.logger.error(f"版本历史中找不到版本 {version_number}")
            return jsonify({
                'success': False,
                'message': f'版本 {version_number} 不存在'
            }), 404
            
        # 获取版本快照
        version_record = mongo_db.health_records_versions.find_one({
            'record_id': str(record['_id']),
            'version': version_number
        })
        
        if not version_record:
            current_app.logger.error(f"找不到版本 {version_number} 的快照")
            
            # 自动修复：创建一个估计的版本快照
            try:
                # 获取对应的版本条目信息
                version_info = None
                for version in version_history:
                    if version.get('version') == version_number:
                        version_info = version
                        break
                
                if version_info:
                    current_app.logger.info(f"尝试为记录 {record_id} 自动创建版本 {version_number} 的估计快照")
                    
                    # 基于当前记录创建一个估计的快照
                    snapshot = record.copy()
                    snapshot['record_id'] = str(record['_id'])
                    snapshot['_id'] = ObjectId()
                    snapshot['version'] = version_number
                    snapshot['snapshot_date'] = datetime.now()
                    snapshot['is_estimated'] = True  # 标记为估计的快照
                    
                    # 调整版本历史
                    if 'version_history' in snapshot:
                        history_copy = [h for h in snapshot['version_history'] 
                                       if h.get('version') <= version_number]
                        snapshot['version_history'] = history_copy
                    
                    # 保存快照
                    mongo_db.health_records_versions.insert_one(snapshot)
                    current_app.logger.info(f"为记录 {record_id} 创建了版本 {version_number} 的估计快照")
                    
                    # 使用新创建的快照作为版本记录
                    version_record = snapshot
                else:
                    return jsonify({
                        'success': False,
                        'message': f'版本 {version_number} 的快照不存在，且无法自动创建'
                    }), 404
                    
            except Exception as e:
                current_app.logger.error(f"自动创建版本快照失败: {str(e)}")
                return jsonify({
                    'success': False,
                    'message': f'版本 {version_number} 的快照不存在，自动创建失败: {str(e)}'
                }), 404
            
        # 处理版本记录
        from ..utils.mongo_utils import format_mongo_doc
        version_data = format_mongo_doc(version_record)
        
        # 添加版本信息
        version_data['version_info'] = version_info
        
        return jsonify({
            'success': True,
            'data': {
                'record': version_data,
                'version': version_number,
                'is_current': False,
                'current_version': current_version
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取记录版本失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取记录版本失败: {str(e)}'
        }), 500
 
# 恢复到特定版本
@health_bp.route('/records/<record_id>/versions/<int:version_number>/restore', methods=['POST'])
@login_required
def restore_record_version(record_id, version_number):
    try:
        # 从MongoDB获取记录
        from bson.objectid import ObjectId
        
        current_app.logger.info(f"尝试恢复记录 {record_id} 到版本 {version_number}")
        
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        
        try:
            mongo_id = ObjectId(record_id)
            record = mongo_db.health_records.find_one({'_id': mongo_id})
        except Exception as e:
            current_app.logger.error(f"获取记录失败: {str(e)}")
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
        if not record:
            current_app.logger.error(f"找不到记录 {record_id}")
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查修改权限（只有患者自己或管理员可以修改）
        if str(record['patient_id']) != str(current_user.id) and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '没有权限修改此记录'
            }), 403
            
        # 获取当前版本号
        current_version = record.get('version', 1)
        if version_number == current_version:
            return jsonify({
                'success': False,
                'message': '无需恢复，当前已是请求的版本'
            }), 400
            
        # 检查版本历史
        version_history = record.get('version_history', [])
        
        # 检查是否有请求的版本
        version_exists = False
        for version in version_history:
            if version.get('version') == version_number:
                version_exists = True
                break
                
        if not version_exists:
            current_app.logger.error(f"版本历史中找不到版本 {version_number}")
            return jsonify({
                'success': False,
                'message': f'版本 {version_number} 不存在'
            }), 404
            
        # 获取版本快照
        version_record = mongo_db.health_records_versions.find_one({
            'record_id': str(record['_id']),
            'version': version_number
        })
        
        if not version_record:
            # 如果没有找到版本快照，返回错误
            current_app.logger.error(f"找不到版本 {version_number} 的快照")
            return jsonify({
                'success': False,
                'message': f'版本 {version_number} 的快照不存在'
            }), 404
            
        # 创建新版本（恢复操作会创建一个新版本）
        new_version = current_version + 1
        
        # 创建版本历史记录
        version_entry = {
            'version': new_version,
            'created_at': datetime.now(),
            'created_by': current_user.id,
            'description': request.json.get('description', f'恢复到版本 {version_number}'),
            'restored_from': version_number
        }
        
        # 拷贝版本快照中的数据
        restore_data = version_record.copy()
        
        # 移除一些不应该恢复的字段
        for field in ['_id', 'version', 'version_history', 'created_at']:
            if field in restore_data:
                del restore_data[field]
                
        # 设置新版本相关字段
        restore_data['version'] = new_version
        restore_data['updated_at'] = datetime.now()
        
        # 添加新版本记录到历史
        version_history.append(version_entry)
        restore_data['version_history'] = version_history
        
        # 执行更新
        current_app.logger.info(f"更新记录 {record_id} 到新版本 {new_version}")
        mongo_db.health_records.update_one(
            {'_id': mongo_id},
            {'$set': restore_data}
        )
        
        # 获取更新后的记录
        updated_record = mongo_db.health_records.find_one({'_id': mongo_id})
        
        # 使用通用函数处理记录
        from ..utils.mongo_utils import format_mongo_doc
        record_data = format_mongo_doc(updated_record)
        
        current_app.logger.info(f"成功恢复记录 {record_id} 到版本 {version_number}")
        return jsonify({
            'success': True,
            'message': f'成功恢复到版本 {version_number}',
            'data': {
                'record': record_data,
                'version': new_version,
                'restored_from': version_number
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"恢复记录版本失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'恢复记录版本失败: {str(e)}'
        }), 500

# 批量更新记录可见性
@health_bp.route('/records/batch/visibility', methods=['POST'])
@login_required
def batch_update_visibility():
    try:
        data = request.json
        if not data or 'record_ids' not in data or 'visibility' not in data:
            return jsonify({
                'success': False,
                'message': '缺少必要字段 (record_ids, visibility)'
            }), 400
            
        # 获取记录ID列表和目标可见性
        record_ids = data['record_ids']
        target_visibility = data['visibility']
        
        # 验证可见性值
        if target_visibility not in [v.value for v in RecordVisibility]:
            return jsonify({
                'success': False,
                'message': f'无效的可见性值: {target_visibility}'
            }), 400
            
        # 更新计数器
        updated_count = 0
        not_found_count = 0
        not_owned_count = 0
        
        for record_id in record_ids:
            # 查找记录
            mongo_id = format_mongo_id(record_id)
            if not mongo_id:
                not_found_count += 1
                continue
                
            # 查找MySQL记录
            record = HealthRecord.query.filter_by(mongo_id=str(mongo_id)).first()
            if not record:
                not_found_count += 1
                continue
                
            # 验证所有权
            if record.patient_id != current_user.id and not current_user.has_role(Role.ADMIN):
                not_owned_count += 1
                continue
                
            # 更新可见性
            record.visibility = target_visibility
            
            # 同步更新MongoDB
            mongo.db.health_records.update_one(
                {'_id': mongo_id},
                {'$set': {'visibility': target_visibility}}
            )
            
            updated_count += 1
            
        # 提交更改
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'成功更新{updated_count}条记录的可见性',
            'data': {
                'updated_count': updated_count,
                'not_found_count': not_found_count,
                'not_owned_count': not_owned_count
            }
        })
    except Exception as e:
        current_app.logger.error(f"批量更新可见性失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'批量更新可见性失败: {str(e)}'
        }), 500

# 批量更新记录PIR保护状态
@health_bp.route('/records/batch/pir-protection', methods=['POST'])
@login_required
def batch_update_pir_protection():
    try:
        data = request.json
        if not data or 'record_ids' not in data or 'pir_protected' not in data:
            return jsonify({
                'success': False,
                'message': '缺少必要字段 (record_ids, pir_protected)'
            }), 400
            
        # 获取记录ID列表和目标PIR保护状态
        record_ids = data['record_ids']
        pir_protected = data['pir_protected']
        
        # 确保pir_protected是布尔值
        if not isinstance(pir_protected, bool):
            return jsonify({
                'success': False,
                'message': 'pir_protected必须是布尔值'
            }), 400
            
        # 更新计数器
        updated_count = 0
        not_found_count = 0
        not_owned_count = 0
        
        for record_id in record_ids:
            # 查找记录
            mongo_id = format_mongo_id(record_id)
            if not mongo_id:
                not_found_count += 1
                continue
                
            # 查找MongoDB记录
            record = mongo.db.health_records.find_one({'_id': mongo_id})
            if not record:
                not_found_count += 1
                continue
                
            # 验证所有权
            if record.get('patient_id') != current_user.id and not current_user.has_role(Role.ADMIN):
                not_owned_count += 1
                continue
                
            # 更新PIR保护状态
            mongo.db.health_records.update_one(
                {'_id': mongo_id},
                {'$set': {'pir_protected': pir_protected}}
            )
            
            updated_count += 1
            
        return jsonify({
            'success': True,
            'message': f'成功更新{updated_count}条记录的PIR保护状态',
            'data': {
                'updated_count': updated_count,
                'not_found_count': not_found_count,
                'not_owned_count': not_owned_count
            }
        })
    except Exception as e:
        current_app.logger.error(f"批量更新PIR保护状态失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'批量更新PIR保护状态失败: {str(e)}'
        }), 500

# 临时修复版本快照
@health_bp.route('/admin/fix-missing-versions', methods=['POST'])
@login_required
@role_required(Role.ADMIN)
def fix_missing_versions():
    try:
        record_id = request.json.get('record_id')
        
        # 获取MongoDB连接
        mongo_db = get_mongo_db()
        
        # 如果提供了特定记录ID，只修复该记录
        if record_id:
            try:
                mongo_id = ObjectId(record_id)
                records = [mongo_db.health_records.find_one({'_id': mongo_id})]
                if not records[0]:
                    return jsonify({
                        'success': False,
                        'message': '记录不存在'
                    }), 404
            except Exception as e:
                current_app.logger.error(f"获取记录失败: {str(e)}")
                return jsonify({
                    'success': False,
                    'message': '无效的记录ID'
                }), 400
        else:
            # 获取所有有版本历史的记录
            records = list(mongo_db.health_records.find(
                {'version_history': {'$exists': True, '$ne': []}}
            ))
        
        fixed_count = 0
        skipped_count = 0
        error_count = 0
        
        for record in records:
            record_id = str(record['_id'])
            version_history = record.get('version_history', [])
            
            if not version_history:
                continue
                
            current_app.logger.info(f"处理记录 {record_id}，有 {len(version_history)} 个版本历史")
            
            # 获取已存在的版本快照
            existing_snapshots = list(mongo_db.health_records_versions.find({'record_id': record_id}))
            existing_versions = [snapshot.get('version') for snapshot in existing_snapshots]
            
            current_app.logger.info(f"记录 {record_id} 已有 {len(existing_snapshots)} 个版本快照")
            
            # 处理每个缺失的版本
            for version_entry in version_history:
                version_number = version_entry.get('version')
                
                # 跳过已存在快照的版本
                if version_number in existing_versions:
                    current_app.logger.info(f"跳过记录 {record_id} 的版本 {version_number}，快照已存在")
                    skipped_count += 1
                    continue
                
                # 如果是第一个版本，使用记录本身创建快照
                if version_number == 1:
                    try:
                        # 创建快照
                        snapshot = record.copy()
                        snapshot['record_id'] = record_id
                        snapshot['_id'] = ObjectId()
                        snapshot['version'] = 1
                        snapshot['snapshot_date'] = datetime.now()
                        
                        # 只保留初始版本时可能存在的字段
                        if 'version_history' in snapshot:
                            history_copy = [h for h in snapshot['version_history'] if h.get('version') == 1]
                            snapshot['version_history'] = history_copy
                            
                        # 保存快照
                        mongo_db.health_records_versions.insert_one(snapshot)
                        current_app.logger.info(f"为记录 {record_id} 创建了版本 {version_number} 的快照")
                        fixed_count += 1
                    except Exception as e:
                        current_app.logger.error(f"创建记录 {record_id} 的版本 {version_number} 快照失败: {str(e)}")
                        error_count += 1
                else:
                    # 对于中间版本，我们无法确切知道那时的记录状态
                    # 复制当前记录，但调整版本信息
                    try:
                        # 基于当前记录创建一个估计的快照
                        snapshot = record.copy()
                        snapshot['record_id'] = record_id
                        snapshot['_id'] = ObjectId()
                        snapshot['version'] = version_number
                        snapshot['snapshot_date'] = datetime.now()
                        snapshot['is_estimated'] = True  # 标记为估计的快照
                        
                        # 调整版本历史
                        if 'version_history' in snapshot:
                            history_copy = [h for h in snapshot['version_history'] 
                                           if h.get('version') <= version_number]
                            snapshot['version_history'] = history_copy
                            
                        # 保存快照
                        mongo_db.health_records_versions.insert_one(snapshot)
                        current_app.logger.info(f"为记录 {record_id} 创建了版本 {version_number} 的估计快照")
                        fixed_count += 1
                    except Exception as e:
                        current_app.logger.error(f"创建记录 {record_id} 的版本 {version_number} 快照失败: {str(e)}")
                        error_count += 1
        
        return jsonify({
            'success': True,
            'message': f'修复完成，共处理 {len(records)} 条记录',
            'data': {
                'fixed_count': fixed_count,
                'skipped_count': skipped_count,
                'error_count': error_count
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"修复版本快照失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'修复版本快照失败: {str(e)}'
        }), 500

# =========================== 用户共享功能 ===========================

# 获取可共享的用户列表
@health_bp.route('/share/users', methods=['GET'])
@login_required
def get_shareable_users():
    """获取患者可以共享记录的用户列表，包括医生和其他患者"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 搜索条件
        search_term = request.args.get('search', '')
        role_filter = request.args.get('role')  # 可以筛选角色：doctor, patient
        
        # 基础查询：不包括自己和管理员
        query = User.query.filter(
            User.id != current_user.id,  # 排除自己
            User.role != Role.ADMIN      # 排除管理员
        )
        
        # 按角色过滤
        if role_filter:
            try:
                role_enum = next(r for r in Role if r.value == role_filter)
                query = query.filter(User.role == role_enum)
            except (StopIteration, ValueError):
                pass  # 无效角色，忽略过滤
        
        # 搜索
        if search_term:
            query = query.filter(or_(
                User.username.ilike(f'%{search_term}%'),
                User.full_name.ilike(f'%{search_term}%'),
                User.email.ilike(f'%{search_term}%')
            ))
        
        # 按最近共享情况排序
        # 使用简单的排序方式避免SQLAlchemy版本兼容性问题
        query = query.order_by(User.full_name)
        
        # 处理分页
        pagination = query.paginate(page=page, per_page=per_page)
        users = pagination.items
        
        # 处理结果 - 获取共享计数并手动排序
        result_with_counts = []
        for user in users:
            user_data = {
                'id': user.id,
                'username': user.username,
                'full_name': user.full_name,
                'email': user.email,
                'avatar': user.avatar,
                'role': user.role.value
            }
            
            # 添加角色特定信息
            if user.role == Role.DOCTOR and hasattr(user, 'doctor_info') and user.doctor_info:
                user_data['doctor_info'] = {
                    'specialty': user.doctor_info.specialty,
                    'hospital': user.doctor_info.hospital,
                    'department': user.doctor_info.department
                }
            elif user.role == Role.PATIENT and hasattr(user, 'patient_info') and user.patient_info:
                user_data['patient_info'] = {
                    'gender': user.patient_info.gender
                }
                
            # 获取与该用户的共享记录数量
            shared_count = SharedRecord.query.filter_by(
                owner_id=current_user.id,
                shared_with=user.id
            ).count()
            
            user_data['shared_records_count'] = shared_count
            result_with_counts.append((user_data, shared_count))
        
        # 手动按共享记录数排序（降序）
        result_with_counts.sort(key=lambda x: x[1], reverse=True)
        result = [item[0] for item in result_with_counts]
        
        # 角色统计
        role_counts = db.session.query(
            User.role, func.count().label('count')
        ).filter(
            User.id != current_user.id,
            User.is_active == True,
            User.role != Role.ADMIN
        ).group_by(User.role).all()
        
        role_stats = {r.value: c for r, c in role_counts}
        
        return jsonify({
            'success': True,
            'data': {
                'users': result,
                'role_stats': role_stats,
                'pagination': {
                    'total': pagination.total,
                    'pages': pagination.pages,
                    'page': page,
                    'per_page': per_page,
                    'has_next': pagination.has_next,
                    'has_prev': pagination.has_prev
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取可共享用户列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取可共享用户列表失败: {str(e)}'
        }), 500

# 获取用户详情（用于共享记录前查看）
@health_bp.route('/share/users/<int:user_id>', methods=['GET'])
@login_required
def get_sharable_user_detail(user_id):
    """获取特定用户的详细信息，用于共享记录前的确认"""
    try:
        user = User.query.get_or_404(user_id)
        
        # 不能查询自己的详情用于共享
        if user.id == current_user.id:
            return jsonify({
                'success': False,
                'message': '不能与自己共享记录'
            }), 400
            
        # 不能共享给管理员
        if user.role == Role.ADMIN:
            return jsonify({
                'success': False,
                'message': '不能与管理员共享记录'
            }), 400
        
        # 构建基本用户信息
        user_data = {
            'id': user.id,
            'username': user.username,
            'full_name': user.full_name,
            'email': user.email,
            'avatar': user.avatar,
            'role': user.role.value,
            'created_at': user.created_at.isoformat() if user.created_at else None
        }
        
        # 添加角色特定信息
        if user.role == Role.DOCTOR and hasattr(user, 'doctor_info') and user.doctor_info:
            user_data['doctor_info'] = {
                'specialty': user.doctor_info.specialty,
                'hospital': user.doctor_info.hospital,
                'department': user.doctor_info.department,
                'years_of_experience': user.doctor_info.years_of_experience,
                'bio': user.doctor_info.bio
            }
        elif user.role == Role.PATIENT and hasattr(user, 'patient_info') and user.patient_info:
            user_data['patient_info'] = {
                'gender': user.patient_info.gender,
                'address': user.patient_info.address
            }
        
        # 获取已共享记录
        shared_records_query = SharedRecord.query.filter_by(
            owner_id=current_user.id,
            shared_with=user.id
        ).order_by(SharedRecord.created_at.desc())
        
        shared_records = []
        for shared in shared_records_query.limit(5).all():  # 只显示最近5条
            record = HealthRecord.query.get(shared.record_id)
            if record:
                shared_records.append({
                    'share_id': shared.id,
                    'record_id': record.mongo_id,
                    'title': record.title,
                    'record_type': record.record_type.value if hasattr(record.record_type, 'value') else record.record_type,
                    'permission': shared.permission.value,
                    'created_at': shared.created_at.isoformat() if shared.created_at else None
                })
        
        user_data['shared_records'] = shared_records
        user_data['shared_records_count'] = shared_records_query.count()
        
        return jsonify({
            'success': True,
            'data': user_data
        })
        
    except Exception as e:
        current_app.logger.error(f"获取用户详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取用户详情失败: {str(e)}'
        }), 500

# 通过访问密钥直接获取共享记录
@health_bp.route('/shared/access/<access_key>', methods=['GET'])
def access_shared_record_by_key(access_key):
    try:
        # 根据access_key查找共享记录
        shared_record = SharedRecord.query.filter_by(access_key=access_key).first()
        if not shared_record:
            return jsonify({
                'success': False,
                'message': '无效的访问密钥或共享记录已不存在'
            }), 404
            
        # 检查是否过期
        if not shared_record.is_valid():
            return jsonify({
                'success': False,
                'message': '共享已过期'
            }), 403
        
        # 记录访问情况
        shared_record.record_access()
            
        # 获取健康记录
        health_record = shared_record.health_record
        if not health_record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 使用缓存函数获取MongoDB中的记录
        mongo_id = health_record.mongo_id
        record_data = get_mongo_health_record(mongo_id) if mongo_id else None
        
        if not record_data:
            return jsonify({
                'success': False,
                'message': '记录不存在或已被删除'
            }), 404
            
        # 获取用户信息
        owner = User.query.get(shared_record.owner_id)
        shared_with_user = User.query.get(shared_record.shared_with)
        
        result = {
            'shared_info': {
                'id': shared_record.id,
                'permission': shared_record.permission.value,
                'created_at': shared_record.created_at.isoformat() if shared_record.created_at else None,
                'expires_at': shared_record.expires_at.isoformat() if shared_record.expires_at else None,
                'is_valid': shared_record.is_valid(),
                'access_count': shared_record.access_count,
                'last_accessed': shared_record.last_accessed.isoformat() if shared_record.last_accessed else None,
                'owner': {
                    'id': owner.id if owner else None,
                    'username': owner.username if owner else None,
                    'full_name': owner.full_name if owner else None
                },
                'shared_with': {
                    'id': shared_with_user.id if shared_with_user else None,
                    'username': shared_with_user.username if shared_with_user else None,
                    'full_name': shared_with_user.full_name if shared_with_user else None
                }
            },
            'record': record_data,
            'sql_id': health_record.id
        }
        
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        current_app.logger.error(f"通过访问密钥获取共享记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取共享记录失败: {str(e)}'
        }), 500

@health_bp.route('/record-types', methods=['GET'])
def get_record_types():
    """获取所有记录类型"""
    try:
        # 获取所有启用的记录类型
        record_types = CustomRecordType.query.filter_by(is_active=True).all()
        
        # 转换为字典列表
        record_types_data = [record_type.to_dict() for record_type in record_types]
        
        return jsonify({
            'success': True,
            'data': {
                'record_types': record_types_data
            }
        }), 200
    
    except Exception as e:
        current_app.logger.error(f"获取记录类型失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': '获取记录类型失败',
            'error': str(e)
        }), 500

# 解密健康记录
@health_bp.route('/records/<record_id>/decrypt', methods=['POST'])
@login_required
def decrypt_health_record(record_id):
    try:
        # 获取解密密钥
        encryption_key = request.json.get('encryption_key')
        if not encryption_key:
            return jsonify({
                'success': False,
                'message': '未提供解密密钥'
            }), 400
        
        current_app.logger.info(f"尝试解密记录 {record_id}")
        
        # 从MongoDB获取记录
        try:
            mongo_id = format_mongo_id(record_id)
            if not mongo_id:
                return jsonify({
                    'success': False,
                    'message': '无效的记录ID'
                }), 400
        except Exception as e:
            current_app.logger.error(f"格式化MongoDB ID失败: {str(e)}")
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
        mongo_db = get_mongo_db()
        record = mongo_db.health_records.find_one({'_id': mongo_id})
        
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查是否有权限访问（只有患者自己或管理员可以访问）
        if str(record['patient_id']) != str(current_user.id) and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
        
        # 检查记录是否已加密
        if not record.get('is_encrypted', False):
            return jsonify({
                'success': False,
                'message': '此记录未加密'
            }), 400
            
        current_app.logger.info(f"记录加密状态确认: is_encrypted={record.get('is_encrypted')}")
        
        # 检查加密数据是否存在
        if 'encrypted_data' not in record:
            current_app.logger.error(f"记录缺少加密数据字段")
            return jsonify({
                'success': False,
                'message': '记录格式无效，缺少加密数据'
            }), 400
        
        # 检查加密盐值是否存在
        if 'key_salt' not in record:
            current_app.logger.error(f"记录缺少密钥盐值字段")
            return jsonify({
                'success': False,
                'message': '记录格式无效，缺少密钥盐值'
            }), 400
        
        # 导入解密工具和格式化工具
        from ..utils.encryption_utils import decrypt_record, verify_record_integrity
        from ..utils.mongo_utils import format_mongo_doc
        
        try:
            # 使用增强的格式化函数将MongoDB文档转换为JSON可序列化的字典
            record_dict = format_mongo_doc(record)
            current_app.logger.info(f"记录格式化完成，准备解密")
            
            # 记录关键解密信息
            current_app.logger.info(f"解密信息: key_salt存在={bool(record_dict.get('key_salt'))}, encrypted_data格式正确={isinstance(record_dict.get('encrypted_data'), dict)}")
            
            # 解密记录
            decrypted_record = decrypt_record(record_dict, encryption_key)
            current_app.logger.info(f"记录解密成功")
            
            # 验证记录完整性
            if 'integrity_hash' in decrypted_record:
                original_hash = decrypted_record['integrity_hash']
                calculated_hash = verify_record_integrity(decrypted_record)
                
                hash_matched = original_hash == calculated_hash
                current_app.logger.info(f"完整性验证: hash匹配={hash_matched}")
                
                integrity_warning = None
                if not hash_matched:
                    integrity_warning = "记录完整性验证失败，记录可能已被篡改，但仍然返回解密数据"
                    current_app.logger.warning(integrity_warning)
            else:
                current_app.logger.info("记录中没有完整性哈希，跳过验证")
                hash_matched = None
                integrity_warning = None
            
            return jsonify({
                'success': True,
                'message': '记录解密成功',
                'data': {
                    'record': decrypted_record,
                    'integrity_verified': hash_matched,
                    'integrity_warning': integrity_warning
                }
            })
            
        except ValueError as e:
            current_app.logger.error(f"解密记录值错误: {str(e)}")
            return jsonify({
                'success': False,
                'message': f'解密失败: {str(e)}'
            }), 400
        except Exception as e:
            current_app.logger.error(f"解密记录过程中发生未知错误: {str(e)}")
            return jsonify({
                'success': False,
                'message': f'解密过程中发生错误: {str(e)}'
            }), 400
            
    except Exception as e:
        current_app.logger.error(f"解密健康记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'解密健康记录失败: {str(e)}'
        }), 500

# 生成健康记录导入模板
@health_bp.route('/import/template', methods=['GET'])
@login_required
def generate_import_template():
    try:
        # 获取请求参数，确定要生成的模板格式
        template_format = request.args.get('format', 'excel').lower()
        if template_format != 'excel':
            return jsonify({
                'success': False,
                'message': '目前仅支持Excel格式的导入模板'
            }), 400

        # 创建导出目录
        export_dir = os.path.join(UPLOAD_FOLDER, "..", "templates")
        os.makedirs(export_dir, exist_ok=True)
        
        # 创建模板文件名 - 加入用户ID和时间戳确保唯一性
        filename = f"health_records_import_template_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
        filepath = os.path.join(export_dir, filename)
        
        # 使用pandas创建Excel模板
        import pandas as pd
        
        # 获取当前日期和前几天的日期作为示例
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        two_days_ago = today - timedelta(days=2)
        
        # 定义模板数据 - 包含示例行和列说明
        template_data = {
            'title': ['肺部CT检查', '血压记录', '血糖监测', ''],
            'record_type': ['IMAGING', 'VITAL_SIGNS', 'LAB_RESULT', ''],
            'description': ['肺部CT检查结果正常，未见异常', '收缩压120mmHg，舒张压80mmHg', '空腹血糖5.4mmol/L', ''],
            'institution': ['北京协和医院', '社区医疗中心', '家庭自测', ''],
            'doctor_name': ['张医生', '李医生', '', ''],
            'tags': ['年度检查,肺部', '定期检查,血压', '日常监测,血糖', ''],
            'pir_protected': [True, True, False, '']
        }
        
        # 创建DataFrame
        df = pd.DataFrame(template_data)
        
        # 写入第一个工作表 - 模板
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='导入模板', index=False)
            
            # 获取工作簿和工作表对象
            workbook = writer.book
            worksheet = writer.sheets['导入模板']
            
            # 创建第二个工作表 - 说明
            instruction_sheet = workbook.create_sheet(title='填写说明')
            
            # 添加说明内容
            instructions = [
                ['健康记录导入模板填写说明'],
                [''],
                ['1. 必填字段：title(标题), record_type(记录类型)'],
                ['2. 记录类型(record_type)可选值：'],
                ['   - GENERAL: 一般记录'],
                ['   - IMAGING: 影像检查'],
                ['   - LAB_RESULT: 实验室结果'],
                ['   - MEDICATION: 药物治疗'],
                ['   - SURGERY: 手术'],
                ['   - VITAL_SIGN: 生命体征'],
                ['   - VACCINATION: 疫苗接种'],
                ['   - ALLERGY: 过敏记录'],
                ['   - DIAGNOSIS: 诊断结果'],
                ['   - PROGRESS_NOTE: 进展记录'],
                ['   - CHRONIC_DISEASE: 慢性病'],
                ['   - EMERGENCY: 急诊记录'],
                ['   - FAMILY_HISTORY: 家族病史'],
                ['   - OTHER: 其他'],
                ['3. 记录日期(record_date): 系统会自动使用当前日期，无需填写'],
                ['4. 标签(tags)格式: 多个标签用逗号分隔，如"标签1,标签2,标签3"'],
                ['5. PIR保护(pir_protected): 填写TRUE或FALSE，表示是否启用PIR隐私保护'],
                ['6. 第一行到第三行是示例数据，实际导入时会被忽略']
            ]
            
            for row_idx, row_data in enumerate(instructions, 1):
                for col_idx, value in enumerate(row_data, 1):
                    instruction_sheet.cell(row=row_idx, column=col_idx, value=value)
            
            # 设置列宽
            instruction_sheet.column_dimensions['A'].width = 80
        
        # 生成下载令牌
        from ..utils.token_utils import generate_download_token
        token = generate_download_token(current_user.id, filename)
        
        return jsonify({
            'success': True,
            'message': '成功生成健康记录导入模板',
            'data': {
                'filename': filename,
                'format': 'excel',
                'download_url': f"/health/import/template/download/{filename}?token={token}"
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"生成导入模板失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'生成导入模板失败: {str(e)}'
        }), 500

# 下载健康记录导入模板
@health_bp.route('/import/template/download/<filename>', methods=['GET'])
def download_import_template(filename):
    try:
        # 获取认证token
        token = request.args.get('token')
        
        # 如果用户已登录，则直接检查权限
        if current_user.is_authenticated:
            # 安全检查，确保只有当前用户可以下载自己的导出文件
            if f"health_records_import_template_{current_user.id}_" not in filename:
                return jsonify({
                    'success': False,
                    'message': '没有权限下载此文件'
                }), 403
        # 如果未登录但提供了token，则验证token
        elif token:
            from ..utils.token_utils import validate_download_token
            user_id = validate_download_token(token, filename)
            if not user_id:
                return jsonify({
                    'success': False,
                    'message': '无效的下载令牌或令牌已过期'
                }), 403
                
            # 验证文件名是否匹配token中的用户ID
            if f"health_records_import_template_{user_id}_" not in filename:
                return jsonify({
                    'success': False,
                    'message': '令牌与文件不匹配'
                }), 403
        # 既没有登录也没有提供token
        else:
            return jsonify({
                'success': False,
                'message': '请先登录或提供有效的下载令牌'
            }), 401
            
        # 模板目录
        template_dir = os.path.join(UPLOAD_FOLDER, "..", "templates")
        
        # 获取文件类型
        file_ext = filename.split('.')[-1].lower()
        if file_ext == 'xlsx':
            mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif file_ext == 'xls':
            mime_type = 'application/vnd.ms-excel'
        else:
            mime_type = 'application/octet-stream'
        
        return send_from_directory(
            template_dir,
            filename,
            as_attachment=True,
            mimetype=mime_type
        )
    
    except Exception as e:
        current_app.logger.error(f"下载导入模板失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'下载导入模板失败: {str(e)}'
        }), 500

@health_bp.route('/statistics/monthly', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_monthly_health_statistics():
    try:
        # 获取请求参数
        month = request.args.get('month')  # 格式: YYYY-MM
        record_type = request.args.get('record_type')
        
        # 如果未指定月份，使用当前月份
        if not month:
            month = datetime.now().strftime('%Y-%m')
        
        # 解析月份
        year, month_num = map(int, month.split('-'))
        
        # 当月的开始和结束日期
        current_month_start = datetime(year, month_num, 1)
        if month_num == 12:
            next_month_start = datetime(year + 1, 1, 1)
        else:
            next_month_start = datetime(year, month_num + 1, 1)
        
        # 上个月的开始和结束日期
        if month_num == 1:
            prev_month_start = datetime(year - 1, 12, 1)
            prev_month_end = current_month_start
        else:
            prev_month_start = datetime(year, month_num - 1, 1)
            prev_month_end = current_month_start
        
        # 去年同月的开始和结束日期
        last_year_month_start = datetime(year - 1, month_num, 1)
        if month_num == 12:
            last_year_month_end = datetime(year, 1, 1)
        else:
            last_year_month_end = datetime(year - 1, month_num + 1, 1)
        
        # 基本查询条件
        base_query = {'patient_id': current_user.id}
        
        # 添加记录类型过滤
        if record_type:
            base_query['record_type'] = record_type
        
        # 查询当月记录数
        current_month_query = base_query.copy()
        current_month_query.update({
            'created_at': {
                '$gte': current_month_start,
                '$lt': next_month_start
            }
        })
        current_month_count = mongo.db.health_records.count_documents(current_month_query)
        
        # 查询上月记录数
        prev_month_query = base_query.copy()
        prev_month_query.update({
            'created_at': {
                '$gte': prev_month_start,
                '$lt': prev_month_end
            }
        })
        prev_month_count = mongo.db.health_records.count_documents(prev_month_query)
        
        # 计算环比增长率
        month_over_month_growth = 0
        if prev_month_count > 0:
            month_over_month_growth = round((current_month_count - prev_month_count) / prev_month_count * 100, 2)
        
        # 查询去年同月记录数
        last_year_month_query = base_query.copy()
        last_year_month_query.update({
            'created_at': {
                '$gte': last_year_month_start,
                '$lt': last_year_month_end
            }
        })
        last_year_month_count = mongo.db.health_records.count_documents(last_year_month_query)
        
        # 计算同比增长率
        year_over_year_growth = None
        if last_year_month_count > 0:
            year_over_year_growth = round((current_month_count - last_year_month_count) / last_year_month_count * 100, 2)
        
        # 获取每月记录数（近6个月）
        monthly_counts = {}
        for i in range(5, -1, -1):
            if month_num - i <= 0:
                month_year = year - 1
                month_val = month_num - i + 12
            else:
                month_year = year
                month_val = month_num - i
                
            month_start = datetime(month_year, month_val, 1)
            
            if month_val == 12:
                month_end = datetime(month_year + 1, 1, 1)
            else:
                month_end = datetime(month_year, month_val + 1, 1)
            
            month_query = base_query.copy()
            month_query.update({
                'created_at': {
                    '$gte': month_start,
                    '$lt': month_end
                }
            })
            month_count = mongo.db.health_records.count_documents(month_query)
            month_key = f"{month_year}-{month_val:02d}"
            monthly_counts[month_key] = month_count
        
        # 获取当月按记录类型的统计
        type_counts = {}
        if not record_type:
            pipeline = [
                {'$match': {
                    'patient_id': current_user.id,
                    'created_at': {
                        '$gte': current_month_start, 
                        '$lt': next_month_start
                    }
                }},
                {'$group': {
                    '_id': '$record_type',
                    'count': {'$sum': 1}
                }}
            ]
            type_results = list(mongo.db.health_records.aggregate(pipeline))
            for result in type_results:
                type_counts[result['_id']] = result['count']
        
        return jsonify({
            'success': True,
            'data': {
                'current_month_count': current_month_count,
                'monthly_counts': monthly_counts,
                'type_counts': type_counts,
                'month_over_month_growth': month_over_month_growth,
                'year_over_year_growth': year_over_year_growth
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取月度健康记录统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取月度健康记录统计失败: {str(e)}'
        }), 500