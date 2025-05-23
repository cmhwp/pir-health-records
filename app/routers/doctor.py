from flask import Blueprint, request, jsonify, current_app, g, flash, redirect, url_for
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from ..models import db, User, Role, HealthRecord, RecordFile, RecordType, RecordVisibility
from ..models.notification import Notification, NotificationType
from ..models.prescription import Prescription, PrescriptionStatus, PrescriptionItem
from ..models.health_records import (
    format_mongo_id, mongo_health_record_to_dict, get_mongo_health_record,
    batch_get_mongo_records,QueryHistory,
)
from ..routers.auth import role_required
from ..utils.pir_utils import (
    PIRQuery, prepare_pir_database, 
    store_health_record_mongodb, query_health_records_mongodb
)
from ..utils.mongo_utils import mongo, get_mongo_db
from ..utils.encryption_utils import encrypt_record, decrypt_record, verify_record_integrity
from ..utils.log_utils import log_record
from bson.objectid import ObjectId
import hashlib
import os
import base64
import json
import io
import csv
import re
from datetime import datetime, timedelta
import uuid
from sqlalchemy import func, desc, distinct

# 创建蓝图
doctor_bp = Blueprint('doctor', __name__, url_prefix='/api/doctor')


# 确保上传目录存在
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads', 'encrypted_records')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'json', 'enc'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_encrypted_file(file):
    """保存加密上传的文件，返回文件路径"""
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

# 存储加密的健康记录
@doctor_bp.route('/records', methods=['POST'])
@login_required
@role_required(Role.DOCTOR)
def store_encrypted_health_record():
    try:
        # 获取基本记录信息
        record_data = json.loads(request.form.get('record_data', '{}'))
        
        if not record_data.get('title') or not record_data.get('record_type'):
            return jsonify({
                'success': False,
                'message': '缺少必要字段 (title, record_type)'
            }), 400
        
        # 获取患者ID（必须指定）
        patient_id = record_data.get('patient_id')
        if not patient_id:
            return jsonify({
                'success': False,
                'message': '必须指定患者ID'
            }), 400
            
        # 验证患者存在
        patient = User.query.get(patient_id)
        if not patient or not patient.has_role(Role.PATIENT):
            return jsonify({
                'success': False,
                'message': '指定的患者不存在或无效'
            }), 404
        
        # 处理加密文件上传
        file_info = []
        files = request.files.getlist('files')
        for file in files:
            file_data = save_encrypted_file(file)
            if file_data:
                file_info.append({
                    'file_name': file_data['original_name'],
                    'file_path': file_data['saved_name'],
                    'file_type': file_data['type'],
                    'file_size': file_data['size'],
                    'description': request.form.get('file_description', ''),
                    'encrypted': True,
                    'uploaded_at': datetime.now().isoformat()
                })
        
        # 加密记录数据
        encryption_key = request.form.get('encryption_key')
        if encryption_key:
            # 如果提供了加密密钥，则加密记录
            record_data = encrypt_record(record_data, encryption_key)
        
        # 添加医生信息到记录
        record_data['doctor_id'] = current_user.id
        record_data['doctor_name'] = current_user.full_name
        record_data['hospital'] = current_user.doctor_info.hospital if hasattr(current_user, 'doctor_info') else None
        record_data['department'] = current_user.doctor_info.department if hasattr(current_user, 'doctor_info') else None
        record_data['is_encrypted'] = bool(encryption_key)
        
        # 设置合规性和数据完整性验证
        record_data['compliance_verified'] = True
        record_data['integrity_hash'] = verify_record_integrity(record_data)
        
        # 存储到MongoDB和MySQL
        record, mongo_id = HealthRecord.create_with_mongo(record_data, patient_id, file_info)
        
        # 为每个文件创建RecordFile记录
        for file_data in file_info:
            record_file = RecordFile(
                record_id=record.id,
                file_name=file_data['file_name'],
                file_path=file_data['file_path'],
                file_type=file_data['file_type'],
                file_size=file_data['file_size'],
                description=file_data.get('description', ''),
                is_encrypted=True
            )
            db.session.add(record_file)
        
        # 记录创建健康记录日志
        log_record(
            message=f'医生{current_user.full_name}为患者{patient.full_name}创建了健康记录: {record_data.get("title")}',
            details={
                'record_id': str(mongo_id),
                'sql_id': record.id,
                'record_type': record_data.get('record_type'),
                'title': record_data.get('title'),
                'visibility': record_data.get('visibility', 'private'),
                'file_count': len(file_info),
                'doctor_id': current_user.id,
                'patient_id': patient_id,
                'is_encrypted': bool(encryption_key),
                'creation_time': datetime.now().isoformat()
            }
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '健康记录创建成功',
            'data': {
                'record_id': str(mongo_id),
                'sql_id': record.id
            }
        }), 201
        
    except Exception as e:
        current_app.logger.error(f"创建加密健康记录失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'创建加密健康记录失败: {str(e)}'
        }), 500

# 获取医生创建的记录列表
@doctor_bp.route('/records', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_doctor_records():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)  # 限制最大每页数量
        
        # 获取医生创建的记录列表（从MySQL）
        query = HealthRecord.query.filter_by(doctor_id=current_user.id)
        
        # 按记录类型筛选
        record_type = request.args.get('record_type')
        if record_type:
            try:
                record_type_enum = RecordType(record_type)
                query = query.filter_by(record_type=record_type_enum)
            except ValueError:
                pass
        
        # 按患者筛选
        patient_id = request.args.get('patient_id')
        if patient_id:
            query = query.filter_by(patient_id=patient_id)
        
        # 按时间范围筛选
        start_date = request.args.get('start_date')
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                query = query.filter(HealthRecord.record_date >= start_date)
            except ValueError:
                pass
                
        end_date = request.args.get('end_date')
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                query = query.filter(HealthRecord.record_date <= end_date)
            except ValueError:
                pass
        
        # 排序
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc')
        
        if sort_order == 'desc':
            query = query.order_by(getattr(HealthRecord, sort_by).desc())
        else:
            query = query.order_by(getattr(HealthRecord, sort_by).asc())
        
        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        records = pagination.items
        
        # 转换为字典列表
        result = []
        for record in records:
            record_dict = record.to_dict(include_mongo_data=False)  # 不包含MongoDB详细数据
            # 获取患者名称
            patient = User.query.get(record.patient_id)
            if patient:
                record_dict['patient_name'] = patient.full_name
            result.append(record_dict)
        
        return jsonify({
            'success': True,
            'data': {
                'records': result,
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
        current_app.logger.error(f"获取医生记录列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取医生记录列表失败: {str(e)}'
        }), 500

# 更新医生创建的记录
@doctor_bp.route('/records/<record_id>', methods=['PUT'])
@login_required
@role_required(Role.DOCTOR)
def update_doctor_record(record_id):
    try:
        # 获取记录
        record = HealthRecord.query.get(record_id)
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 验证医生是否有权限修改此记录
        if record.doctor_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有权限修改此记录'
            }), 403
        
        # 获取更新数据
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供更新数据'
            }), 400
        
        # 获取MongoDB记录并更新
        mongo_db = get_mongo_db()
        mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(record.mongo_id)})
        
        if not mongo_record:
            return jsonify({
                'success': False,
                'message': 'MongoDB记录不存在'
            }), 404
        
        # 如果记录已加密，需要提供加密密钥
        if mongo_record.get('is_encrypted', False):
            encryption_key = data.get('encryption_key')
            if not encryption_key:
                return jsonify({
                    'success': False,
                    'message': '记录已加密，请提供加密密钥'
                }), 400
                
            # 解密记录，更新，然后重新加密
            try:
                update_data = data.get('record_data', {})
                if update_data:
                    # 更新MongoDB记录
                    for key, value in update_data.items():
                        if key not in ['_id', 'patient_id', 'doctor_id', 'doctor_name', 'is_encrypted']:
                            mongo_record[key] = value
                    
                    # 重新加密并更新完整性哈希
                    mongo_record = encrypt_record(mongo_record, encryption_key)
                    mongo_record['integrity_hash'] = verify_record_integrity(mongo_record)
                    mongo_record['updated_at'] = datetime.now()
                    
                    # 更新MongoDB
                    mongo_db.health_records.update_one(
                        {'_id': ObjectId(record.mongo_id)},
                        {'$set': mongo_record}
                    )
            except Exception as e:
                return jsonify({
                    'success': False,
                    'message': f'解密或更新记录失败: {str(e)}'
                }), 400
        else:
            # 未加密记录直接更新
            update_data = data.get('record_data', {})
            if update_data:
                # 更新MongoDB记录
                mongo_db.health_records.update_one(
                    {'_id': ObjectId(record.mongo_id)},
                    {'$set': {
                        **update_data,
                        'updated_at': datetime.now(),
                        'integrity_hash': verify_record_integrity({**mongo_record, **update_data})
                    }}
                )
        
        # 更新MySQL索引记录
        if data.get('title'):
            record.title = data['title']
        if data.get('record_type'):
            try:
                record.record_type = RecordType(data['record_type'])
            except ValueError:
                pass
        if data.get('record_date'):
            try:
                record.record_date = datetime.fromisoformat(data['record_date'])
            except ValueError:
                pass
        if data.get('visibility'):
            try:
                record.visibility = RecordVisibility(data['visibility'])
            except ValueError:
                pass
                
        record.updated_at = datetime.now()
        db.session.commit()
        
        # 记录更新操作
        log_record(
            message=f'医生{current_user.full_name}更新了健康记录: {record.title}',
            details={
                'record_id': record.mongo_id,
                'sql_id': record.id,
                'update_fields': list(update_data.keys()) if update_data else []
            }
        )
        
        return jsonify({
            'success': True,
            'message': '记录更新成功',
            'data': {
                'record_id': record.mongo_id,
                'sql_id': record.id
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"更新医生记录失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'更新医生记录失败: {str(e)}'
        }), 500

# PIR查询接口 - 安全查询患者记录，不泄露具体查询内容
@doctor_bp.route('/pir/query', methods=['POST'])
@login_required
@role_required(Role.DOCTOR)
def pir_query_patient_records():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供查询参数'
            }), 400
        
        # 获取患者ID
        patient_id = data.get('patient_id')
        if not patient_id:
            return jsonify({
                'success': False,
                'message': '必须指定患者ID'
            }), 400
            
        # 验证患者存在
        patient = User.query.get(patient_id)
        if not patient or not patient.has_role(Role.PATIENT):
            return jsonify({
                'success': False,
                'message': '指定的患者不存在或无效'
            }), 404
        
        # 构建PIR查询
        query_params = data.get('query_params', {})
        
        # 添加可见性条件，医生只能查询对医生可见的记录
        query_params['visibility'] = ['doctor', 'public']
        
        # 使用PIR进行隐私查询
        results = query_health_records_mongodb(
            query_params,
            patient_id,
            is_anonymous=True,  # 匿名查询，不记录具体查询内容
            requester_id=current_user.id,
            requester_role='doctor'
        )
        
        # 处理记录查询结果
        processed_results = []
        for record in results:
            # 检查记录是否加密
            if record.get('is_encrypted', False):
                # 如果记录已加密，只返回元数据
                processed_record = {
                    'id': str(record.get('_id')),
                    'title': record.get('title'),
                    'record_type': record.get('record_type'),
                    'record_date': record.get('record_date'),
                    'doctor_name': record.get('doctor_name'),
                    'is_encrypted': True,
                    'created_at': record.get('created_at'),
                    'updated_at': record.get('updated_at')
                }
            else:
                # 如果未加密，返回完整记录
                processed_record = mongo_health_record_to_dict(record)
                
            processed_results.append(processed_record)
        
        # 记录查询操作（不记录具体查询参数，只记录查询行为）
        log_record(
            message=f'医生{current_user.full_name}使用PIR查询了患者{patient.full_name}的健康记录',
            details={
                'doctor_id': current_user.id,
                'patient_id': patient_id,
                'query_time': datetime.now().isoformat(),
                'result_count': len(processed_results),
                'is_anonymous': True
            }
        )
        
        return jsonify({
            'success': True,
            'data': {
                'records': processed_results,
                'total': len(processed_results)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"PIR查询失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'PIR查询失败: {str(e)}'
        }), 500

# 解密单个记录（医生需要解密密钥）
@doctor_bp.route('/records/<record_id>/decrypt', methods=['POST'])
@login_required
@role_required(Role.DOCTOR)
def decrypt_record_api(record_id):
    try:
        data = request.json
        if not data or 'encryption_key' not in data:
            return jsonify({
                'success': False,
                'message': '未提供加密密钥'
            }), 400
        
        # 获取记录
        record = HealthRecord.query.get(record_id)
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 获取MongoDB记录
        mongo_db = get_mongo_db()
        mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(record.mongo_id)})
        
        if not mongo_record:
            return jsonify({
                'success': False,
                'message': 'MongoDB记录不存在'
            }), 404
        
        # 检查记录是否加密
        if not mongo_record.get('is_encrypted', False):
            return jsonify({
                'success': False,
                'message': '记录未加密'
            }), 400
        
        # 检查医生是否有权限访问此记录
        if mongo_record.get('doctor_id') != current_user.id and record.visibility != RecordVisibility.PUBLIC:
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
        
        # 尝试解密记录
        try:
            decrypted_record = decrypt_record(mongo_record, data['encryption_key'])
            
            # 记录解密操作
            log_record(
                message=f'医生{current_user.full_name}解密了健康记录: {record.title}',
                details={
                    'record_id': record.mongo_id,
                    'sql_id': record.id,
                    'patient_id': record.patient_id,
                    'doctor_id': current_user.id
                }
            )
            
            return jsonify({
                'success': True,
                'data': mongo_health_record_to_dict(decrypted_record)
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'解密记录失败: {str(e)}'
            }), 400
            
    except Exception as e:
        current_app.logger.error(f"解密记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'解密记录失败: {str(e)}'
        }), 500

# 合规性验证接口 - 验证记录是否符合合规要求
@doctor_bp.route('/records/<record_id>/verify-compliance', methods=['POST'])
@login_required
def verify_record_compliance(record_id):
    try:
        # 获取记录
        record = HealthRecord.query.get(record_id)
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 验证用户是否有权限访问记录
        if record.patient_id != current_user.id and record.doctor_id != current_user.id:
            # 不是创建记录的医生也不是记录所属的患者
            return jsonify({
                'success': False,
                'message': '没有权限验证此记录'
            }), 403
        
        # 获取MongoDB记录
        mongo_db = get_mongo_db()
        mongo_record = mongo_db.health_records.find_one({'_id': ObjectId(record.mongo_id)})
        
        if not mongo_record:
            return jsonify({
                'success': False,
                'message': 'MongoDB记录不存在'
            }), 404
        
        # 如果记录已加密，需要提供加密密钥进行验证
        if mongo_record.get('is_encrypted', False):
            data = request.json
            if not data or 'encryption_key' not in data:
                return jsonify({
                    'success': False,
                    'message': '记录已加密，请提供加密密钥'
                }), 400
                
            try:
                # 解密记录
                decrypted_record = decrypt_record(mongo_record, data['encryption_key'])
                
                # 验证记录完整性
                stored_hash = decrypted_record.get('integrity_hash')
                calculated_hash = verify_record_integrity(decrypted_record)
                
                if not stored_hash or stored_hash != calculated_hash:
                    return jsonify({
                        'success': False,
                        'message': '记录完整性验证失败，数据可能已被篡改',
                        'verification': {
                            'integrity': False,
                            'compliance': False
                        }
                    }), 400
                    
                # 执行合规性检查
                compliance_result = {
                    'integrity': True,
                    'has_required_fields': all(field in decrypted_record for field in [
                        'patient_id', 'record_type', 'title', 'record_date'
                    ]),
                    'has_doctor_info': all(field in decrypted_record for field in [
                        'doctor_id', 'doctor_name'
                    ]),
                    'privacy_compliance': decrypted_record.get('visibility') in ['private', 'doctor']
                }
                
                compliance_result['compliance'] = all([
                    compliance_result['integrity'],
                    compliance_result['has_required_fields'],
                    compliance_result['has_doctor_info']
                ])
                
                # 更新记录合规性状态
                mongo_db.health_records.update_one(
                    {'_id': ObjectId(record.mongo_id)},
                    {'$set': {
                        'compliance_verified': compliance_result['compliance'],
                        'compliance_verification_date': datetime.now(),
                        'compliance_verified_by': current_user.id
                    }}
                )
                
                # 记录验证操作
                log_record(
                    message=f'医生{current_user.full_name}验证了健康记录合规性: {record.title}',
                    details={
                        'record_id': record.mongo_id,
                        'sql_id': record.id,
                        'verification_result': compliance_result,
                        'doctor_id': current_user.id
                    }
                )
                
                return jsonify({
                    'success': True,
                    'message': '记录合规性验证完成',
                    'data': {
                        'verification': compliance_result
                    }
                })
                
            except Exception as e:
                return jsonify({
                    'success': False,
                    'message': f'解密或验证记录失败: {str(e)}'
                }), 400
        else:
            # 未加密记录直接验证
            # 验证记录完整性
            stored_hash = mongo_record.get('integrity_hash')
            calculated_hash = verify_record_integrity(mongo_record)
            
            if not stored_hash or stored_hash != calculated_hash:
                return jsonify({
                    'success': False,
                    'message': '记录完整性验证失败，数据可能已被篡改',
                    'verification': {
                        'integrity': False,
                        'compliance': False
                    }
                }), 400
                
            # 执行合规性检查
            compliance_result = {
                'integrity': True,
                'has_required_fields': all(field in mongo_record for field in [
                    'patient_id', 'record_type', 'title', 'record_date'
                ]),
                'has_doctor_info': all(field in mongo_record for field in [
                    'doctor_id', 'doctor_name'
                ]),
                'privacy_compliance': mongo_record.get('visibility') in ['private', 'doctor']
            }
            
            compliance_result['compliance'] = all([
                compliance_result['integrity'],
                compliance_result['has_required_fields'],
                compliance_result['has_doctor_info']
            ])
            
            # 更新记录合规性状态
            mongo_db.health_records.update_one(
                {'_id': ObjectId(record.mongo_id)},
                {'$set': {
                    'compliance_verified': compliance_result['compliance'],
                    'compliance_verification_date': datetime.now(),
                    'compliance_verified_by': current_user.id
                }}
            )
            
            # 记录验证操作
            log_record(
                message=f'医生{current_user.full_name}验证了健康记录合规性: {record.title}',
                details={
                    'record_id': record.mongo_id,
                    'sql_id': record.id,
                    'verification_result': compliance_result,
                    'doctor_id': current_user.id
                }
            )
            
            return jsonify({
                'success': True,
                'message': '记录合规性验证完成',
                'data': {
                    'verification': compliance_result
                }
            })
            
    except Exception as e:
        current_app.logger.error(f"验证记录合规性失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'验证记录合规性失败: {str(e)}'
        }), 500

# 健康记录审计日志接口 - 查看记录访问和修改历史
@doctor_bp.route('/records/<record_id>/audit-logs', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_record_audit_logs(record_id):
    try:
        # 获取记录
        record = HealthRecord.query.get(record_id)
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 验证医生是否有权限查看此记录
        if record.doctor_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有权限查看此记录的审计日志'
            }), 403
        
        # 获取查询历史
        query_history = QueryHistory.query.filter_by(record_id=record.id).order_by(QueryHistory.query_time.desc()).all()
        
        # 获取系统日志
        from ..models.log import SystemLog
        
        system_logs = SystemLog.query.filter(
            SystemLog.details.contains(f'"record_id": "{record.mongo_id}"')
        ).order_by(SystemLog.created_at.desc()).all()
        
        # 合并并处理日志
        audit_logs = []
        
        for history in query_history:
            user = User.query.get(history.user_id)
            log_entry = {
                'id': history.id,
                'type': 'query',
                'action': history.query_type,
                'user_id': history.user_id,
                'user_name': user.full_name if user else None,
                'user_role': user.role.value if user else None,
                'timestamp': history.query_time.isoformat(),
                'is_anonymous': history.is_anonymous
            }
            audit_logs.append(log_entry)
        
        for log in system_logs:
            log_entry = {
                'id': log.id,
                'type': 'system_log',
                'action': log.message,
                'user_id': log.user_id,
                'user_name': log.user.full_name if log.user else None,
                'user_role': log.user.role.value if log.user else None,
                'timestamp': log.created_at.isoformat(),
                'details': log.details
            }
            audit_logs.append(log_entry)
        
        # 按时间排序
        audit_logs.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({
            'success': True,
            'data': {
                'record': {
                    'id': record.id,
                    'mongo_id': record.mongo_id,
                    'title': record.title
                },
                'audit_logs': audit_logs
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取审计日志失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取审计日志失败: {str(e)}'
        }), 500

# 工作台 - 获取医生工作台统计和信息
@doctor_bp.route('/dashboard', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_doctor_dashboard():
    try:
        today = datetime.now().date()
        
        # 获取医生的处方患者数量（而非健康记录）
        from ..models.prescription import Prescription
        
        # 今日处方患者数量
        today_patients_count = db.session.query(func.count(distinct(Prescription.patient_id))).filter(
            Prescription.doctor_id == current_user.id,
            func.date(Prescription.created_at) == today
        ).scalar() or 0
        
        # 获取医生总处方患者数量
        total_patients_count = db.session.query(func.count(distinct(Prescription.patient_id))).filter(
            Prescription.doctor_id == current_user.id
        ).scalar() or 0
        
        # 获取医生可见的健康记录数量（仅包括DOCTOR或PUBLIC可见性）
        total_records_count = HealthRecord.query.filter(
            HealthRecord.doctor_id == current_user.id,
            HealthRecord.visibility.in_([RecordVisibility.DOCTOR, RecordVisibility.PUBLIC])
        ).count()
        
        # 获取最近的5条健康记录（仅包括医生可见的）
        recent_records = HealthRecord.query.filter(
            HealthRecord.visibility.in_([RecordVisibility.DOCTOR, RecordVisibility.PUBLIC])
        ).order_by(HealthRecord.created_at.desc()).limit(5).all()
        
        recent_records_data = []
        for record in recent_records:
            patient = User.query.get(record.patient_id)
            patient_name = patient.full_name if patient else "未知患者"
            
            record_dict = {
                'id': record.id,
                'mongo_id': record.mongo_id,
                'title': record.title,
                'patient_id': record.patient_id,
                'patient_name': patient_name,
                'record_type': record.record_type,
                'record_date': record.record_date.isoformat() if record.record_date else None,
                'created_at': record.created_at.isoformat() if record.created_at else None,
                'visibility': record.visibility.value
            }
            recent_records_data.append(record_dict)
        
        # 获取医生信息
        doctor_info = None
        if hasattr(current_user, 'doctor_info') and current_user.doctor_info:
            doctor_info = {
                'specialty': current_user.doctor_info.specialty,
                'hospital': current_user.doctor_info.hospital,
                'department': current_user.doctor_info.department,
                'years_of_experience': current_user.doctor_info.years_of_experience
            }
        
        # 获取待处理处方数量
        pending_prescriptions_count = Prescription.query.filter_by(
            doctor_id=current_user.id,
            status=PrescriptionStatus.PENDING
        ).count()
        
        return jsonify({
            'success': True,
            'data': {
                'doctor': {
                    'id': current_user.id,
                    'name': current_user.full_name,
                    'info': doctor_info
                },
                'statistics': {
                    'today_patients': today_patients_count,
                    'total_patients': total_patients_count,
                    'total_visible_records': total_records_count,
                    'pending_prescriptions': pending_prescriptions_count
                },
                'recent_records': recent_records_data
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取医生工作台信息失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取医生工作台信息失败: {str(e)}'
        }), 500

# 获取医生的患者列表
@doctor_bp.route('/patients', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_doctor_patients():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 从健康记录中获取患者ID
        patient_ids_from_records = db.session.query(distinct(HealthRecord.patient_id)).filter(
            HealthRecord.doctor_id == current_user.id
        ).all()
        
        patient_ids_from_records = [pid[0] for pid in patient_ids_from_records]
        
        # 从处方中获取患者ID
        from ..models.prescription import Prescription
        patient_ids_from_prescriptions = db.session.query(distinct(Prescription.patient_id)).filter(
            Prescription.doctor_id == current_user.id
        ).all()
        
        patient_ids_from_prescriptions = [pid[0] for pid in patient_ids_from_prescriptions]
        
        # 合并两个患者ID列表（去重）
        patient_ids = list(set(patient_ids_from_records + patient_ids_from_prescriptions))
        
        # 按姓名搜索
        search_term = request.args.get('search', '')
        
        query = User.query.filter(
            User.id.in_(patient_ids),
            User.role == Role.PATIENT
        )
        
        if search_term:
            query = query.filter(User.full_name.like(f'%{search_term}%'))
        
        # 排序
        sort_by = request.args.get('sort_by', 'full_name')
        sort_order = request.args.get('sort_order', 'asc')
        
        if sort_by == 'full_name':
            if sort_order == 'desc':
                query = query.order_by(User.full_name.desc())
            else:
                query = query.order_by(User.full_name.asc())
        else:
            if sort_order == 'desc':
                query = query.order_by(User.created_at.desc())
            else:
                query = query.order_by(User.created_at.asc())
        
        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        patients = pagination.items
        
        # 处理结果
        result = []
        for patient in patients:
            # 获取患者对医生可见的健康记录数量（只包括医生可见和公开可见的记录）
            record_count = HealthRecord.query.filter(
                HealthRecord.patient_id == patient.id,
                HealthRecord.visibility.in_([RecordVisibility.DOCTOR, RecordVisibility.PUBLIC])
            ).count()
            
            # 获取患者由医生创建的健康记录数量
            doctor_created_records = HealthRecord.query.filter_by(
                patient_id=patient.id,
                doctor_id=current_user.id
            ).count()
            
            # 获取处方记录数量
            prescription_count = Prescription.query.filter_by(
                patient_id=patient.id,
                doctor_id=current_user.id
            ).count()
            
            # 获取最近一次记录时间（包括健康记录和处方记录）
            latest_record = HealthRecord.query.filter(
                HealthRecord.patient_id == patient.id,
                HealthRecord.visibility.in_([RecordVisibility.DOCTOR, RecordVisibility.PUBLIC])
            ).order_by(HealthRecord.created_at.desc()).first()
            
            latest_prescription = Prescription.query.filter_by(
                patient_id=patient.id,
                doctor_id=current_user.id
            ).order_by(Prescription.created_at.desc()).first()
            
            # 确定最近的接触时间
            latest_time = None
            if latest_record and latest_prescription:
                latest_time = max(latest_record.created_at, latest_prescription.created_at)
            elif latest_record:
                latest_time = latest_record.created_at
            elif latest_prescription:
                latest_time = latest_prescription.created_at
            
            patient_info = None
            if hasattr(patient, 'patient_info') and patient.patient_info:
                patient_info = {
                    'gender': patient.patient_info.gender,
                    'date_of_birth': patient.patient_info.date_of_birth.isoformat() if patient.patient_info.date_of_birth else None,
                    'allergies': patient.patient_info.allergies,
                    'medical_history': patient.patient_info.medical_history
                }
            
            result.append({
                'id': patient.id,
                'name': patient.full_name,
                'email': patient.email,
                'info': patient_info,
                'visible_record_count': record_count,
                'doctor_created_record_count': doctor_created_records,
                'prescription_count': prescription_count,
                'total_interactions': record_count + prescription_count,
                'latest_visit': latest_time.isoformat() if latest_time else None,
                'has_prescriptions': prescription_count > 0
            })
        
        return jsonify({
            'success': True,
            'data': {
                'patients': result,
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
        current_app.logger.error(f"获取患者列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取患者列表失败: {str(e)}'
        }), 500

# 获取患者详情
@doctor_bp.route('/patients/<int:patient_id>', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_patient_details(patient_id):
    try:
        # 验证患者存在
        patient = User.query.get(patient_id)
        if not patient or not patient.has_role(Role.PATIENT):
            return jsonify({
                'success': False,
                'message': '患者不存在或无效'
            }), 404
        
        # 验证医生是否处理过该患者（通过处方或已创建健康记录）
        has_record = (HealthRecord.query.filter_by(patient_id=patient_id, doctor_id=current_user.id).first() is not None) or \
                     (Prescription.query.filter_by(patient_id=patient_id, doctor_id=current_user.id).first() is not None)
        
        if not has_record:
            return jsonify({
                'success': False,
                'message': '没有权限访问该患者信息'
            }), 403
        
        # 获取患者基本信息
        patient_info = None
        if hasattr(patient, 'patient_info') and patient.patient_info:
            patient_info = {
                'gender': patient.patient_info.gender,
                'date_of_birth': patient.patient_info.date_of_birth.isoformat() if patient.patient_info.date_of_birth else None,
                'allergies': patient.patient_info.allergies,
                'medical_history': patient.patient_info.medical_history,
                'emergency_contact': patient.patient_info.emergency_contact,
                'emergency_phone': patient.patient_info.emergency_phone,
                'address': patient.patient_info.address
            }
        
        # 获取患者可见健康记录统计（包括医生可见和公开可见的记录）
        record_stats = db.session.query(
            HealthRecord.record_type,
            func.count(HealthRecord.id).label('count')
        ).filter(
            HealthRecord.patient_id == patient_id,
            HealthRecord.visibility.in_([RecordVisibility.DOCTOR, RecordVisibility.PUBLIC])
        ).group_by(HealthRecord.record_type).all()
        
        record_stats_dict = {record_type: count for record_type, count in record_stats}
        
        # 获取医生创建的该患者记录统计
        doctor_created_stats = db.session.query(
            HealthRecord.record_type,
            func.count(HealthRecord.id).label('count')
        ).filter(
            HealthRecord.patient_id == patient_id,
            HealthRecord.doctor_id == current_user.id
        ).group_by(HealthRecord.record_type).all()
        
        doctor_created_stats_dict = {record_type: count for record_type, count in doctor_created_stats}
        
        # 获取最近的健康记录（包括医生可见和公开可见的记录）
        recent_records = HealthRecord.query.filter(
            HealthRecord.patient_id == patient_id,
            HealthRecord.visibility.in_([RecordVisibility.DOCTOR, RecordVisibility.PUBLIC])
        ).order_by(HealthRecord.created_at.desc()).limit(5).all()
        
        recent_records_data = []
        for record in recent_records:
            record_dict = {
                'id': record.id,
                'mongo_id': record.mongo_id,
                'title': record.title,
                'record_type': record.record_type,
                'record_date': record.record_date.isoformat() if record.record_date else None,
                'created_at': record.created_at.isoformat() if record.created_at else None,
                'visibility': record.visibility.value,
                'is_doctor_created': record.doctor_id == current_user.id
            }
            recent_records_data.append(record_dict)
        
        # 记录访问操作
        log_record(
            message=f'医生{current_user.full_name}查看了患者{patient.full_name}的详细信息',
            details={
                'doctor_id': current_user.id,
                'patient_id': patient_id,
                'access_time': datetime.now().isoformat()
            }
        )
        
        # 获取患者处方统计信息
        prescription_stats = Prescription.query.filter_by(
            patient_id=patient_id,
            doctor_id=current_user.id
        ).with_entities(
            Prescription.status,
            func.count(Prescription.id).label('count')
        ).group_by(Prescription.status).all()
        
        prescription_stats_dict = {status.value: count for status, count in prescription_stats}
        
        return jsonify({
            'success': True,
            'data': {
                'patient': {
                    'id': patient.id,
                    'name': patient.full_name,
                    'email': patient.email,
                    'phone': patient.phone,
                    'created_at': patient.created_at.isoformat() if patient.created_at else None,
                    'info': patient_info
                },
                'record_statistics': {
                    'visible_records': record_stats_dict,
                    'doctor_created': doctor_created_stats_dict
                },
                'prescription_statistics': prescription_stats_dict,
                'recent_records': recent_records_data,
                'total_visible_records': HealthRecord.query.filter(
                    HealthRecord.patient_id == patient_id,
                    HealthRecord.visibility.in_([RecordVisibility.DOCTOR, RecordVisibility.PUBLIC])
                ).count(),
                'total_doctor_created_records': HealthRecord.query.filter_by(
                    patient_id=patient_id,
                    doctor_id=current_user.id
                ).count(),
                'total_prescriptions': Prescription.query.filter_by(
                    patient_id=patient_id,
                    doctor_id=current_user.id
                ).count()
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取患者详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取患者详情失败: {str(e)}'
        }), 500

# 获取处方列表
@doctor_bp.route('/prescriptions', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_prescriptions():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        
        
        # 构建查询
        query = Prescription.query.filter_by(doctor_id=current_user.id)
        
        # 按患者筛选
        patient_id = request.args.get('patient_id')
        if patient_id:
            query = query.filter_by(patient_id=patient_id)
        
        # 按状态筛选
        status = request.args.get('status')
        if status:
            try:
                status_enum = PrescriptionStatus(status)
                query = query.filter_by(status=status_enum)
            except ValueError:
                pass
        
        # 按日期范围筛选
        start_date = request.args.get('start_date')
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                query = query.filter(Prescription.created_at >= start_date)
            except ValueError:
                pass
                
        end_date = request.args.get('end_date')
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                end_date = end_date.replace(hour=23, minute=59, second=59)
                query = query.filter(Prescription.created_at <= end_date)
            except ValueError:
                pass
        
        # 排序
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc')
        
        if sort_order == 'desc':
            query = query.order_by(getattr(Prescription, sort_by).desc())
        else:
            query = query.order_by(getattr(Prescription, sort_by).asc())
        
        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        prescriptions = pagination.items
        
        # 处理结果
        result = []
        for prescription in prescriptions:
            patient = User.query.get(prescription.patient_id)
            
            # 获取处方药品
            items = PrescriptionItem.query.filter_by(prescription_id=prescription.id).all()
            
            prescription_items = []
            for item in items:
                prescription_items.append({
                    'id': item.id,
                    'medicine_name': item.medicine_name,
                    'dosage': item.dosage,
                    'frequency': item.frequency,
                    'duration': item.duration,
                    'notes': item.notes
                })
            
            result.append({
                'id': prescription.id,
                'patient_id': prescription.patient_id,
                'patient_name': patient.full_name if patient else "未知患者",
                'diagnosis': prescription.diagnosis,
                'instructions': prescription.instructions,
                'status': prescription.status.value,
                'items': prescription_items,
                'created_at': prescription.created_at.isoformat() if prescription.created_at else None,
                'valid_until': prescription.valid_until.isoformat() if prescription.valid_until else None
            })
        
        return jsonify({
            'success': True,
            'data': {
                'prescriptions': result,
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
        current_app.logger.error(f"获取处方列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取处方列表失败: {str(e)}'
        }), 500

# 创建处方
@doctor_bp.route('/prescriptions', methods=['POST'])
@login_required
@role_required(Role.DOCTOR)
def create_prescription():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供处方数据'
            }), 400
        
        required_fields = ['patient_id', 'diagnosis', 'items']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'message': f'缺少必要字段: {field}'
                }), 400
        
        # 验证患者存在
        patient = User.query.get(data['patient_id'])
        if not patient or not patient.has_role(Role.PATIENT):
            return jsonify({
                'success': False,
                'message': '指定的患者不存在或无效'
            }), 404
        
        # 验证药品项目
        items = data.get('items', [])
        if not items or not isinstance(items, list):
            return jsonify({
                'success': False,
                'message': '处方药品不能为空'
            }), 400
        
        # 导入处方模型
        from ..models.prescription import Prescription, PrescriptionItem, PrescriptionStatus
        
        # 设置有效期（默认为30天）
        valid_days = data.get('valid_days', 30)
        valid_until = datetime.now() + timedelta(days=valid_days)
        
        # 创建处方
        prescription = Prescription(
            patient_id=data['patient_id'],
            doctor_id=current_user.id,
            diagnosis=data['diagnosis'],
            instructions=data.get('instructions', ''),
            status=PrescriptionStatus.ACTIVE,
            valid_until=valid_until
        )
        
        db.session.add(prescription)
        db.session.flush()  # 获取处方ID
        
        # 添加处方药品
        for item_data in items:
            if 'medicine_name' not in item_data or 'dosage' not in item_data:
                continue
                
            item = PrescriptionItem(
                prescription_id=prescription.id,
                medicine_name=item_data['medicine_name'],
                dosage=item_data['dosage'],
                frequency=item_data.get('frequency', ''),
                duration=item_data.get('duration', ''),
                notes=item_data.get('notes', '')
            )
            db.session.add(item)
        
        # 记录操作
        log_record(
            message=f'医生{current_user.full_name}为患者{patient.full_name}创建了处方',
            details={
                'doctor_id': current_user.id,
                'patient_id': data['patient_id'],
                'prescription_id': prescription.id,
                'diagnosis': data['diagnosis'],
                'medicine_count': len(items),
                'creation_time': datetime.now().isoformat()
            }
        )
        
        # 添加通知
        notification = Notification(
            user_id=data['patient_id'],
            notification_type=NotificationType.PRESCRIPTION,
            title=f"医生{current_user.full_name}为您开具了处方",
            message=f"诊断: {data['diagnosis']}, 包含{len(items)}种药品",
            related_id=str(prescription.id),
            is_read=False
        )
        
        db.session.add(notification)
        
        # 创建健康记录和用药记录
        try:
            # 导入必要的类
            from ..models.health_records import HealthRecord, RecordType, RecordVisibility, MedicationRecord
            
            # 创建健康记录
            health_record = HealthRecord(
                patient_id=data['patient_id'],
                doctor_id=current_user.id,
                record_type=RecordType.MEDICATION,
                title=f"处方: {data['diagnosis']}",
                record_date=datetime.now(),
                visibility=RecordVisibility.DOCTOR
            )
            
            db.session.add(health_record)
            db.session.flush()  # 获取记录ID
            
            # 为每种药品创建用药记录
            for item in items:
                medication_record = MedicationRecord.from_prescription_item(
                    record_id=health_record.id,
                    prescription_id=prescription.id,
                    item=item
                )
                db.session.add(medication_record)
            
            # 保存到MongoDB
            mongo_data = {
                'patient_id': data['patient_id'],
                'doctor_id': current_user.id,
                'record_type': RecordType.MEDICATION.value,
                'title': f"处方: {data['diagnosis']}",
                'diagnosis': data['diagnosis'],
                'instructions': data.get('instructions', ''),
                'prescription_id': prescription.id,
                'medications': [
                    {
                        'medication_name': item.medicine_name,
                        'dosage': item.dosage,
                        'frequency': item.frequency,
                        'duration': item.duration,
                        'instructions': item.notes
                    } for item in items
                ],
                'record_date': datetime.now(),
                'visibility': RecordVisibility.DOCTOR.value
            }
            
            # 保存到MongoDB
            mongo_id = store_health_record_mongodb(mongo_data, data['patient_id'])
            
            # 更新MySQL记录的mongo_id
            health_record.mongo_id = mongo_id
            
        except Exception as e:
            # 记录错误但不阻止处方创建
            current_app.logger.error(f"创建用药记录失败: {str(e)}")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '处方创建成功',
            'data': {
                'prescription_id': prescription.id
            }
        }), 201
        
    except Exception as e:
        current_app.logger.error(f"创建处方失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'创建处方失败: {str(e)}'
        }), 500

# 更新处方状态
@doctor_bp.route('/prescriptions/<int:prescription_id>', methods=['PUT'])
@login_required
@role_required(Role.DOCTOR)
def update_prescription(prescription_id):
    try:
        
        # 获取处方
        prescription = Prescription.query.get(prescription_id)
        if not prescription:
            return jsonify({
                'success': False,
                'message': '处方不存在'
            }), 404
        
        # 验证医生是否有权限操作
        if prescription.doctor_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有权限操作此处方'
            }), 403
        
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供更新数据'
            }), 400
        
        # 更新处方状态
        if 'status' in data:
            try:
                new_status = PrescriptionStatus(data['status'])
                prescription.status = new_status
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': '无效的处方状态'
                }), 400
        
        # 更新其他字段
        for field in ['diagnosis', 'instructions', 'valid_until']:
            if field in data:
                if field == 'valid_until':
                    try:
                        valid_until = datetime.fromisoformat(data['valid_until'])
                        setattr(prescription, field, valid_until)
                    except ValueError:
                        return jsonify({
                            'success': False,
                            'message': '有效期格式无效'
                        }), 400
                else:
                    setattr(prescription, field, data[field])
        
        prescription.updated_at = datetime.now()
        
        # 获取患者信息
        patient = User.query.get(prescription.patient_id)
        
        # 记录操作
        log_record(
            message=f'医生{current_user.full_name}更新了患者{patient.full_name if patient else "未知"}的处方',
            details={
                'doctor_id': current_user.id,
                'patient_id': prescription.patient_id,
                'prescription_id': prescription.id,
                'update_fields': list(data.keys()),
                'update_time': datetime.now().isoformat()
            }
        )
        
        # 如果状态变更为已撤销，添加通知
        if 'status' in data and data['status'] == 'REVOKED':
            
            notification = Notification(
                user_id=prescription.patient_id,
                notification_type=NotificationType.PRESCRIPTION,
                title=f"医生{current_user.full_name}已撤销您的处方",
                message=f"处方ID: {prescription.id}, 诊断: {prescription.diagnosis}",
                related_id=str(prescription.id),
                is_read=False
            )
            
            db.session.add(notification)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '处方更新成功',
            'data': {
                'prescription_id': prescription.id
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"更新处方失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'更新处方失败: {str(e)}'
        }), 500

# 获取患者统计数据
@doctor_bp.route('/statistics/patients', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_patient_statistics():
    try:
        # 时间范围筛选
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        query_filters = [HealthRecord.doctor_id == current_user.id]
        
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                query_filters.append(HealthRecord.created_at >= start_date)
            except ValueError:
                pass
                
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                end_date = end_date.replace(hour=23, minute=59, second=59)
                query_filters.append(HealthRecord.created_at <= end_date)
            except ValueError:
                pass
        
        # 总患者数量
        total_patients = db.session.query(func.count(distinct(HealthRecord.patient_id))).filter(
            *query_filters
        ).scalar() or 0
        
        # 各类型记录数量统计
        record_type_stats = db.session.query(
            HealthRecord.record_type,
            func.count(HealthRecord.id).label('count')
        ).filter(
            *query_filters
        ).group_by(HealthRecord.record_type).all()
        
        record_type_data = {record_type: count for record_type, count in record_type_stats}
        
        # 每月新增患者数量统计
        monthly_patients = []
        if start_date and end_date:
            # 获取月份范围
            current_date = start_date.replace(day=1)
            end_month = end_date.replace(day=1)
            
            while current_date <= end_month:
                next_month = (current_date.replace(day=28) + timedelta(days=4)).replace(day=1)
                
                # 当月新增患者数量
                month_patients = db.session.query(func.count(distinct(HealthRecord.patient_id))).filter(
                    HealthRecord.doctor_id == current_user.id,
                    HealthRecord.created_at >= current_date,
                    HealthRecord.created_at < next_month,
                    ~HealthRecord.patient_id.in_(
                        db.session.query(distinct(HealthRecord.patient_id)).filter(
                            HealthRecord.doctor_id == current_user.id,
                            HealthRecord.created_at < current_date
                        )
                    )
                ).scalar() or 0
                
                monthly_patients.append({
                    'year': current_date.year,
                    'month': current_date.month,
                    'count': month_patients
                })
                
                current_date = next_month
        
        # 导入PatientInfo模型
        from ..models.role_models import PatientInfo
        
        # 按性别统计患者
        gender_stats = db.session.query(
            PatientInfo.gender,
            func.count(distinct(HealthRecord.patient_id)).label('count')
        ).join(
            PatientInfo, PatientInfo.user_id == HealthRecord.patient_id
        ).filter(
            *query_filters
        ).group_by(PatientInfo.gender).all()
        
        gender_data = {gender or '未知': count for gender, count in gender_stats}
        
        # 按年龄段统计患者
        current_year = datetime.now().year
        age_ranges = [
            ('0-18', 0, 18),
            ('19-30', 19, 30),
            ('31-45', 31, 45),
            ('46-60', 46, 60),
            ('61+', 61, 200)
        ]
        
        age_stats = {}
        for range_name, min_age, max_age in age_ranges:
            min_birth_year = current_year - max_age
            max_birth_year = current_year - min_age
            
            count = db.session.query(func.count(distinct(HealthRecord.patient_id))).join(
                PatientInfo, PatientInfo.user_id == HealthRecord.patient_id
            ).filter(
                *query_filters,
                PatientInfo.date_of_birth.isnot(None),
                func.extract('year', PatientInfo.date_of_birth) >= min_birth_year,
                func.extract('year', PatientInfo.date_of_birth) <= max_birth_year
            ).scalar() or 0
            
            age_stats[range_name] = count
        
        # 未知年龄的患者数量
        unknown_age_count = db.session.query(func.count(distinct(HealthRecord.patient_id))).filter(
            *query_filters,
            ~HealthRecord.patient_id.in_(
                db.session.query(distinct(PatientInfo.user_id)).filter(
                    PatientInfo.date_of_birth.isnot(None)
                )
            )
        ).scalar() or 0
        
        age_stats['未知'] = unknown_age_count
        
        return jsonify({
            'success': True,
            'data': {
                'total_patients': total_patients,
                'record_types': record_type_data,
                'monthly_patients': monthly_patients,
                'gender_distribution': gender_data,
                'age_distribution': age_stats
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取患者统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取患者统计失败: {str(e)}'
        }), 500

# 获取疾病统计数据
@doctor_bp.route('/statistics/diseases', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_disease_statistics():
    try:
        # 时间范围筛选
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        query_filters = [HealthRecord.doctor_id == current_user.id]
        
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                query_filters.append(HealthRecord.created_at >= start_date)
            except ValueError:
                pass
                
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                end_date = end_date.replace(hour=23, minute=59, second=59)
                query_filters.append(HealthRecord.created_at <= end_date)
            except ValueError:
                pass
        
        # 使用MongoDB获取更多的数据分析
        mongo_db = get_mongo_db()
        
        # 获取所有医生创建的记录ID
        record_ids = [record.mongo_id for record in HealthRecord.query.filter(*query_filters).all()]
        record_object_ids = [ObjectId(mongo_id) for mongo_id in record_ids if mongo_id]
        
        if not record_object_ids:
            return jsonify({
                'success': True,
                'data': {
                    'common_diagnoses': {},
                    'disease_trends': [],
                    'medication_stats': {},
                    'treatment_stats': {}
                }
            })
        
        # 获取MongoDB中的记录
        mongo_records = list(mongo_db.health_records.find({
            '_id': {'$in': record_object_ids}
        }))
        
        # 提取诊断结果
        diagnoses = {}
        for record in mongo_records:
            diagnosis = record.get('diagnosis', '')
            if diagnosis:
                diagnoses[diagnosis] = diagnoses.get(diagnosis, 0) + 1
        
        # 获取最常见的10种诊断
        common_diagnoses = dict(sorted(diagnoses.items(), key=lambda x: x[1], reverse=True)[:10])
        
        # 按月统计疾病趋势
        disease_trends = []
        if start_date and end_date:
            # 获取前三常见诊断
            top_diagnoses = list(common_diagnoses.keys())[:3]
            
            # 获取月份范围
            current_date = start_date.replace(day=1)
            end_month = end_date.replace(day=1)
            
            while current_date <= end_month:
                next_month = (current_date.replace(day=28) + timedelta(days=4)).replace(day=1)
                
                month_trends = {
                    'year': current_date.year,
                    'month': current_date.month,
                    'diagnoses': {}
                }
                
                # 统计每种诊断在当月的数量
                for diagnosis in top_diagnoses:
                    count = 0
                    for record in mongo_records:
                        record_date = record.get('created_at')
                        if isinstance(record_date, str):
                            try:
                                record_date = datetime.fromisoformat(record_date)
                            except ValueError:
                                continue
                        
                        if (record_date and current_date <= record_date < next_month and 
                            record.get('diagnosis') == diagnosis):
                            count += 1
                    
                    month_trends['diagnoses'][diagnosis] = count
                
                disease_trends.append(month_trends)
                current_date = next_month
        
        # 统计药物使用情况
        medication_stats = {}
        for record in mongo_records:
            medications = record.get('medications', [])
            for medication in medications:
                med_name = medication.get('name', '')
                if med_name:
                    medication_stats[med_name] = medication_stats.get(med_name, 0) + 1
        
        # 获取最常用的10种药物
        common_medications = dict(sorted(medication_stats.items(), key=lambda x: x[1], reverse=True)[:10])
        
        # 统计治疗方式
        treatment_stats = {}
        for record in mongo_records:
            treatment = record.get('treatment', '')
            if treatment:
                treatment_stats[treatment] = treatment_stats.get(treatment, 0) + 1
        
        # 获取最常用的10种治疗方式
        common_treatments = dict(sorted(treatment_stats.items(), key=lambda x: x[1], reverse=True)[:10])
        
        return jsonify({
            'success': True,
            'data': {
                'common_diagnoses': common_diagnoses,
                'disease_trends': disease_trends,
                'medication_stats': common_medications,
                'treatment_stats': common_treatments
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取疾病统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取疾病统计失败: {str(e)}'
        }), 500

# 医生处理处方申请
@doctor_bp.route('/prescriptions/pending', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_pending_prescriptions():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 获取待处理的处方申请
        query = Prescription.query.filter_by(
            doctor_id=current_user.id, 
            status=PrescriptionStatus.PENDING
        ).order_by(Prescription.created_at.desc())
        
        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        prescriptions = pagination.items
        
        # 处理结果
        result = []
        for prescription in prescriptions:
            patient = User.query.get(prescription.patient_id)
            
            # 获取处方药品
            items = PrescriptionItem.query.filter_by(prescription_id=prescription.id).all()
            
            prescription_items = []
            for item in items:
                prescription_items.append({
                    'id': item.id,
                    'medicine_name': item.medicine_name,
                    'dosage': item.dosage,
                    'frequency': item.frequency,
                    'duration': item.duration,
                    'notes': item.notes
                })
            
            prescription_data = {
                'id': prescription.id,
                'patient_id': prescription.patient_id,
                'patient_name': patient.full_name if patient else "未知患者",
                'symptoms': prescription.symptoms,  # 添加患者症状字段
                'diagnosis': prescription.diagnosis,
                'instructions': prescription.instructions,
                'status': prescription.status.value,
                'items': prescription_items,
                'created_at': prescription.created_at.isoformat() if prescription.created_at else None,
            }
            
            # 添加患者信息
            if patient and patient.patient_info:
                prescription_data['patient_info'] = {
                    'gender': patient.patient_info.gender,
                    'date_of_birth': patient.patient_info.date_of_birth.isoformat() if patient.patient_info.date_of_birth else None,
                    'allergies': patient.patient_info.allergies
                }
                
            result.append(prescription_data)
        
        return jsonify({
            'success': True,
            'data': {
                'prescriptions': result,
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
        current_app.logger.error(f"获取待处理处方失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取待处理处方失败: {str(e)}'
        }), 500

# 处理处方申请
@doctor_bp.route('/prescriptions/<int:prescription_id>/process', methods=['PUT'])
@login_required
@role_required(Role.DOCTOR)
def process_prescription_request(prescription_id):
    try:
        # 获取处方
        prescription = Prescription.query.get(prescription_id)
        if not prescription:
            return jsonify({
                'success': False,
                'message': '处方不存在'
            }), 404
        
        # 验证医生是否有权限操作
        if prescription.doctor_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有权限操作此处方'
            }), 403
        
        # 验证处方状态
        if prescription.status != PrescriptionStatus.PENDING:
            return jsonify({
                'success': False,
                'message': '只能处理待确认状态的处方申请'
            }), 400
        
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供处理数据'
            }), 400
        
        # 获取处理决定
        action = data.get('action')
        if not action or action not in ['approve', 'reject']:
            return jsonify({
                'success': False,
                'message': '无效的处理操作，必须是 approve 或 reject'
            }), 400
        
        # 获取患者信息
        patient = User.query.get(prescription.patient_id)
        
        if action == 'approve':
            # 医生必须提供诊断结果
            if 'diagnosis' not in data or not data['diagnosis'].strip():
                return jsonify({
                    'success': False,
                    'message': '批准处方时必须提供诊断结果'
                }), 400
            
            # 更新处方状态为激活
            prescription.status = PrescriptionStatus.ACTIVE
            
            # 更新处方信息
            prescription.diagnosis = data['diagnosis']
                
            if 'instructions' in data:
                prescription.instructions = data['instructions']
                
            # 设置有效期（默认为30天）
            valid_days = data.get('valid_days', 30)
            prescription.valid_until = datetime.now() + timedelta(days=valid_days)
            
            # 处理药品项目 - 必须指定至少一种药品
            items_data = data.get('items', [])
            if not items_data or not isinstance(items_data, list) or len(items_data) == 0:
                return jsonify({
                    'success': False,
                    'message': '批准处方时必须指定至少一种药品'
                }), 400
                
            # 清除现有药品并添加医生开具的药品（总是替换全部）
            PrescriptionItem.query.filter_by(prescription_id=prescription.id).delete()
            
            # 添加新药品
            for item_data in items_data:
                if 'medicine_name' not in item_data or not item_data['medicine_name'].strip():
                    continue
                    
                if 'dosage' not in item_data or not item_data['dosage'].strip():
                    continue
                    
                item = PrescriptionItem(
                    prescription_id=prescription.id,
                    medicine_name=item_data['medicine_name'],
                    dosage=item_data['dosage'],
                    frequency=item_data.get('frequency', ''),
                    duration=item_data.get('duration', ''),
                    notes=item_data.get('notes', '')
                )
                db.session.add(item)
            
            # 获取更新后的药品数量
            items_count = PrescriptionItem.query.filter_by(prescription_id=prescription.id).count()
            if items_count == 0:
                return jsonify({
                    'success': False,
                    'message': '处方中必须包含至少一种有效药品'
                }), 400
            
            # 发送通知给患者
            notification = Notification(
                user_id=prescription.patient_id,
                notification_type=NotificationType.PRESCRIPTION,
                title=f"医生{current_user.full_name}已批准您的处方申请",
                message=f"您报告的症状: {prescription.symptoms}\n医生诊断: {prescription.diagnosis}\n已为您开具包含{items_count}种药品的处方，请查看详情",
                related_id=str(prescription.id),
                is_read=False
            )
            
            db.session.add(notification)
            
            message = '处方申请已批准并激活'
            
        else:  # 拒绝处方
            # 更新处方状态为已撤销/拒绝
            prescription.status = PrescriptionStatus.REVOKED
            
            # 记录拒绝原因
            rejection_reason = data.get('reason', '不满足开具处方的条件')
            prescription.instructions = f"申请被拒绝。原因: {rejection_reason}"
            
            # 发送通知给患者
            notification = Notification(
                user_id=prescription.patient_id,
                notification_type=NotificationType.PRESCRIPTION,
                title=f"医生{current_user.full_name}未批准您的处方申请",
                message=f"原因: {rejection_reason}",
                related_id=str(prescription.id),
                is_read=False
            )
            
            db.session.add(notification)
            
            message = '处方申请已拒绝'
        
        prescription.updated_at = datetime.now()
        
        # 记录操作
        log_record(
            message=f'医生{current_user.full_name}{message}，患者: {patient.full_name if patient else "未知"}',
            details={
                'doctor_id': current_user.id,
                'patient_id': prescription.patient_id,
                'prescription_id': prescription.id,
                'action': action,
                'update_time': datetime.now().isoformat()
            }
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': message,
            'data': {
                'prescription_id': prescription.id,
                'status': prescription.status.value
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"处理处方申请失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'处理处方申请失败: {str(e)}'
        }), 500

# 删除健康记录
@doctor_bp.route('/records/<record_id>', methods=['DELETE'])
@login_required
@role_required(Role.DOCTOR)
def delete_health_record(record_id):
    try:
        # 获取记录
        record = HealthRecord.query.get(record_id)
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 验证医生是否有权限删除此记录
        if record.doctor_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有权限删除此记录'
            }), 403
        
        # 获取患者信息用于日志
        patient = User.query.get(record.patient_id)
        patient_name = patient.full_name if patient else "未知患者"
        
        # 获取MongoDB记录ID
        mongo_id = record.mongo_id
        
        # 删除关联的文件记录
        RecordFile.query.filter_by(record_id=record.id).delete()
        
        # 删除MySQL记录
        db.session.delete(record)
        
        # 删除MongoDB中的详细记录
        if mongo_id:
            mongo_db = get_mongo_db()
            mongo_db.health_records.delete_one({'_id': ObjectId(mongo_id)})
        
        # 记录删除操作
        log_record(
            message=f'医生{current_user.full_name}删除了患者{patient_name}的健康记录: {record.title}',
            details={
                'record_id': mongo_id,
                'sql_id': record.id,
                'doctor_id': current_user.id,
                'patient_id': record.patient_id,
                'record_type': record.record_type,
                'deletion_time': datetime.now().isoformat()
            }
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '记录删除成功'
        })
        
    except Exception as e:
        current_app.logger.error(f"删除健康记录失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'删除健康记录失败: {str(e)}'
        }), 500

# 获取患者创建的健康记录列表（医生可见或公开可见）
@doctor_bp.route('/patient-records', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_patient_visible_records():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)  # 限制最大每页数量
        
        # 获取患者创建的记录，医生可见或公开可见
        query = HealthRecord.query.filter(
            HealthRecord.visibility.in_([RecordVisibility.DOCTOR, RecordVisibility.PUBLIC])
        )
        
        # 按患者筛选
        patient_id = request.args.get('patient_id')
        if patient_id:
            query = query.filter_by(patient_id=patient_id)
        
        # 按记录类型筛选
        record_type = request.args.get('record_type')
        if record_type:
            query = query.filter_by(record_type=record_type)
        
        # 按时间范围筛选
        start_date = request.args.get('start_date')
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                query = query.filter(HealthRecord.record_date >= start_date)
            except ValueError:
                pass
                
        end_date = request.args.get('end_date')
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                query = query.filter(HealthRecord.record_date <= end_date)
            except ValueError:
                pass
        
        # 排序
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc')
        
        if sort_order == 'desc':
            query = query.order_by(getattr(HealthRecord, sort_by).desc())
        else:
            query = query.order_by(getattr(HealthRecord, sort_by).asc())
        
        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        records = pagination.items
        
        # 转换为字典列表
        result = []
        for record in records:
            record_dict = record.to_dict(include_mongo_data=False)  # 不包含MongoDB详细数据
            # 获取患者名称
            patient = User.query.get(record.patient_id)
            if patient:
                record_dict['patient_name'] = patient.full_name
            result.append(record_dict)
        
        return jsonify({
            'success': True,
            'data': {
                'records': result,
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
        current_app.logger.error(f"获取患者健康记录列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取患者健康记录列表失败: {str(e)}'
        }), 500

# 获取单个患者健康记录详情（医生可见或公开可见）
@doctor_bp.route('/patient-records/<record_id>', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_patient_record_detail(record_id):
    try:
        # 获取记录
        record = HealthRecord.query.get(record_id)
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 验证记录可见性
        if record.visibility not in [RecordVisibility.DOCTOR, RecordVisibility.PUBLIC]:
            return jsonify({
                'success': False,
                'message': '没有权限查看此记录'
            }), 403
        
        # 获取完整记录数据（包括MongoDB数据）
        record_data = record.to_dict(include_mongo_data=True)
        
        # 获取患者信息
        patient = User.query.get(record.patient_id)
        if patient:
            record_data['patient_name'] = patient.full_name
            
            # 添加基本患者信息
            if hasattr(patient, 'patient_info') and patient.patient_info:
                record_data['patient_info'] = {
                    'gender': patient.patient_info.gender,
                    'date_of_birth': patient.patient_info.date_of_birth.isoformat() if patient.patient_info.date_of_birth else None,
                    'allergies': patient.patient_info.allergies,
                    'medical_history': patient.patient_info.medical_history
                }
        
        # 记录查询操作
        query_history = QueryHistory(
            user_id=current_user.id,
            record_id=record.id,
            query_type='view_detail',
            is_anonymous=False
        )
        db.session.add(query_history)
        
        log_record(
            message=f'医生{current_user.full_name}查看了健康记录: {record.title}',
            details={
                'record_id': record.mongo_id,
                'sql_id': record.id,
                'doctor_id': current_user.id,
                'patient_id': record.patient_id,
                'query_time': datetime.now().isoformat()
            }
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': record_data
        })
        
    except Exception as e:
        current_app.logger.error(f"获取健康记录详情失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'获取健康记录详情失败: {str(e)}'
        }), 500