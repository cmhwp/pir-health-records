from flask import Blueprint, request, jsonify, current_app, g
from flask_login import login_required, current_user
from ..models import db, User, Role, HealthRecord, RecordFile, RecordType, RecordVisibility, QueryHistory
from ..models.health_records import (
    format_mongo_id, mongo_health_record_to_dict, get_mongo_health_record,
    batch_get_mongo_records
)
from ..models.appointment import Appointment, AppointmentStatus
from ..models.notification import Notification, NotificationType
# 导入处方模型
from ..models.prescription import Prescription, PrescriptionStatus
from ..routers.auth import role_required
from ..utils.pir_utils import (
    PIRQuery, prepare_pir_database, 
    store_health_record_mongodb, query_health_records_mongodb
)
from ..utils.mongo_utils import mongo, get_mongo_db
from ..utils.encryption_utils import encrypt_record, decrypt_record, verify_record_integrity
from ..utils.log_utils import log_record
from bson.objectid import ObjectId
import os
import uuid
from datetime import datetime, timedelta
import json
from werkzeug.utils import secure_filename
from sqlalchemy import func, distinct

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
@role_required(Role.DOCTOR)
def verify_record_compliance(record_id):
    try:
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
        
        # 获取医生今日患者数量
        today_patients_count = db.session.query(func.count(distinct(HealthRecord.patient_id))).filter(
            HealthRecord.doctor_id == current_user.id,
            func.date(HealthRecord.created_at) == today
        ).scalar() or 0
        
        # 获取医生总患者数量
        total_patients_count = db.session.query(func.count(distinct(HealthRecord.patient_id))).filter(
            HealthRecord.doctor_id == current_user.id
        ).scalar() or 0
        
        # 获取医生处理的记录数量
        total_records_count = HealthRecord.query.filter_by(doctor_id=current_user.id).count()
        
        # 获取最近的5条健康记录
        recent_records = HealthRecord.query.filter_by(
            doctor_id=current_user.id
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
                'record_type': record.record_type.value,
                'record_date': record.record_date.isoformat() if record.record_date else None,
                'created_at': record.created_at.isoformat() if record.created_at else None
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
                    'total_records': total_records_count
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
        
        # 获取医生处理过的患者列表（从健康记录中获取患者ID）
        patient_ids = db.session.query(distinct(HealthRecord.patient_id)).filter(
            HealthRecord.doctor_id == current_user.id
        ).all()
        
        patient_ids = [pid[0] for pid in patient_ids]
        
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
            # 获取患者记录数量
            record_count = HealthRecord.query.filter_by(
                patient_id=patient.id,
                doctor_id=current_user.id
            ).count()
            
            # 获取最近一次记录时间
            latest_record = HealthRecord.query.filter_by(
                patient_id=patient.id,
                doctor_id=current_user.id
            ).order_by(HealthRecord.created_at.desc()).first()
            
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
                'record_count': record_count,
                'latest_visit': latest_record.created_at.isoformat() if latest_record else None
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
        
        # 验证医生是否处理过该患者
        has_record = HealthRecord.query.filter_by(
            patient_id=patient_id,
            doctor_id=current_user.id
        ).first() is not None
        
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
        
        # 获取患者记录统计
        record_stats = db.session.query(
            HealthRecord.record_type,
            func.count(HealthRecord.id).label('count')
        ).filter(
            HealthRecord.patient_id == patient_id,
            HealthRecord.doctor_id == current_user.id
        ).group_by(HealthRecord.record_type).all()
        
        record_stats_dict = {record_type.value: count for record_type, count in record_stats}
        
        # 获取最近的健康记录
        recent_records = HealthRecord.query.filter_by(
            patient_id=patient_id,
            doctor_id=current_user.id
        ).order_by(HealthRecord.created_at.desc()).limit(5).all()
        
        recent_records_data = []
        for record in recent_records:
            record_dict = {
                'id': record.id,
                'mongo_id': record.mongo_id,
                'title': record.title,
                'record_type': record.record_type.value,
                'record_date': record.record_date.isoformat() if record.record_date else None,
                'created_at': record.created_at.isoformat() if record.created_at else None
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
                'record_statistics': record_stats_dict,
                'recent_records': recent_records_data,
                'total_records': HealthRecord.query.filter_by(
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

# 获取医生的预约列表
@doctor_bp.route('/appointments', methods=['GET'])
@login_required
@role_required(Role.DOCTOR)
def get_doctor_appointments():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 日期筛选
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        date_filter = request.args.get('date_filter', 'all')  # all, today, upcoming, past
        
        # 状态筛选
        status = request.args.get('status')  # pending, confirmed, cancelled, completed
        
        # 将Appointment导入避免出错
        
        query = Appointment.query.filter_by(doctor_id=current_user.id)
        
        # 日期过滤
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        if date_filter == 'today':
            query = query.filter(
                func.date(Appointment.appointment_time) == today.date()
            )
        elif date_filter == 'upcoming':
            query = query.filter(
                Appointment.appointment_time >= today
            )
        elif date_filter == 'past':
            query = query.filter(
                Appointment.appointment_time < today
            )
            
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                query = query.filter(Appointment.appointment_time >= start_date)
            except ValueError:
                pass
                
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                end_date = end_date.replace(hour=23, minute=59, second=59)
                query = query.filter(Appointment.appointment_time <= end_date)
            except ValueError:
                pass
        
        # 状态过滤
        if status:
            try:
                status_enum = AppointmentStatus(status)
                query = query.filter_by(status=status_enum)
            except ValueError:
                pass
        
        # 排序
        sort_field = request.args.get('sort_by', 'appointment_time')
        sort_order = request.args.get('sort_order', 'asc')
        
        if sort_order == 'desc':
            query = query.order_by(getattr(Appointment, sort_field).desc())
        else:
            query = query.order_by(getattr(Appointment, sort_field).asc())
        
        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        appointments = pagination.items
        
        # 处理结果
        result = []
        for appointment in appointments:
            patient = User.query.get(appointment.patient_id)
            
            result.append({
                'id': appointment.id,
                'patient_id': appointment.patient_id,
                'patient_name': patient.full_name if patient else "未知患者",
                'appointment_time': appointment.appointment_time.isoformat() if appointment.appointment_time else None,
                'duration': appointment.duration,
                'purpose': appointment.purpose,
                'status': appointment.status.value,
                'notes': appointment.notes,
                'created_at': appointment.created_at.isoformat() if appointment.created_at else None
            })
        
        return jsonify({
            'success': True,
            'data': {
                'appointments': result,
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
        current_app.logger.error(f"获取预约列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取预约列表失败: {str(e)}'
        }), 500

# 创建预约
@doctor_bp.route('/appointments', methods=['POST'])
@login_required
@role_required(Role.DOCTOR)
def create_appointment():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供预约数据'
            }), 400
        
        required_fields = ['patient_id', 'appointment_time', 'duration', 'purpose']
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
        
        # 解析预约时间
        try:
            appointment_time = datetime.fromisoformat(data['appointment_time'])
        except ValueError:
            return jsonify({
                'success': False,
                'message': '预约时间格式无效'
            }), 400
        
        # 检查医生的时间冲突
        doctor_conflict = Appointment.query.filter(
            Appointment.doctor_id == current_user.id,
            Appointment.status != AppointmentStatus.CANCELLED,
            Appointment.appointment_time <= appointment_time + timedelta(minutes=data['duration']),
            Appointment.appointment_time + timedelta(minutes=Appointment.duration) >= appointment_time
        ).first()
        
        if doctor_conflict:
            return jsonify({
                'success': False,
                'message': '您在该时间段已有其他预约'
            }), 409
        
        # 检查患者的时间冲突
        patient_conflict = Appointment.query.filter(
            Appointment.patient_id == data['patient_id'],
            Appointment.status != AppointmentStatus.CANCELLED,
            Appointment.appointment_time <= appointment_time + timedelta(minutes=data['duration']),
            Appointment.appointment_time + timedelta(minutes=Appointment.duration) >= appointment_time
        ).first()
        
        if patient_conflict:
            return jsonify({
                'success': False,
                'message': '患者在该时间段已有其他预约'
            }), 409
        
        # 创建预约
        appointment = Appointment(
            doctor_id=current_user.id,
            patient_id=data['patient_id'],
            appointment_time=appointment_time,
            duration=data['duration'],
            purpose=data['purpose'],
            notes=data.get('notes', ''),
            status=AppointmentStatus.CONFIRMED
        )
        
        db.session.add(appointment)
        
        # 记录操作
        log_record(
            message=f'医生{current_user.full_name}为患者{patient.full_name}创建了预约',
            details={
                'doctor_id': current_user.id,
                'patient_id': data['patient_id'],
                'appointment_id': appointment.id,
                'appointment_time': appointment_time.isoformat(),
                'creation_time': datetime.now().isoformat()
            }
        )
        
        # 添加通知
        
        notification = Notification(
            user_id=data['patient_id'],
            type=NotificationType.APPOINTMENT,
            title=f"医生{current_user.full_name}为您创建了预约",
            content=f"预约时间: {appointment_time.strftime('%Y-%m-%d %H:%M')}, 目的: {data['purpose']}",
            data={
                'appointment_id': appointment.id,
                'doctor_id': current_user.id,
                'doctor_name': current_user.full_name,
                'appointment_time': appointment_time.isoformat()
            },
            is_read=False
        )
        
        db.session.add(notification)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '预约创建成功',
            'data': {
                'appointment_id': appointment.id
            }
        }), 201
        
    except Exception as e:
        current_app.logger.error(f"创建预约失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'创建预约失败: {str(e)}'
        }), 500

# 更新预约状态
@doctor_bp.route('/appointments/<int:appointment_id>', methods=['PUT'])
@login_required
@role_required(Role.DOCTOR)
def update_appointment(appointment_id):
    try:
        from ..models.appointment import Appointment, AppointmentStatus
        
        # 获取预约
        appointment = Appointment.query.get(appointment_id)
        if not appointment:
            return jsonify({
                'success': False,
                'message': '预约不存在'
            }), 404
        
        # 验证医生是否有权限操作
        if appointment.doctor_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有权限操作此预约'
            }), 403
        
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供更新数据'
            }), 400
        
        # 更新预约状态
        if 'status' in data:
            try:
                new_status = AppointmentStatus(data['status'])
                appointment.status = new_status
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': '无效的预约状态'
                }), 400
        
        # 更新预约时间
        if 'appointment_time' in data:
            try:
                appointment_time = datetime.fromisoformat(data['appointment_time'])
                appointment.appointment_time = appointment_time
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': '预约时间格式无效'
                }), 400
        
        # 更新其他字段
        for field in ['duration', 'purpose', 'notes']:
            if field in data:
                setattr(appointment, field, data[field])
        
        appointment.updated_at = datetime.now()
        
        # 获取患者信息
        patient = User.query.get(appointment.patient_id)
        
        # 记录操作
        log_record(
            message=f'医生{current_user.full_name}更新了患者{patient.full_name if patient else "未知"}的预约',
            details={
                'doctor_id': current_user.id,
                'patient_id': appointment.patient_id,
                'appointment_id': appointment.id,
                'update_fields': list(data.keys()),
                'update_time': datetime.now().isoformat()
            }
        )
        
        # 添加通知
        if 'status' in data or 'appointment_time' in data:
            
            content = "您的预约已更新"
            if 'status' in data:
                content = f"您的预约状态已更新为: {data['status']}"
            elif 'appointment_time' in data:
                content = f"您的预约时间已更改为: {appointment_time.strftime('%Y-%m-%d %H:%M')}"
            
            notification = Notification(
                user_id=appointment.patient_id,
                type=NotificationType.APPOINTMENT,
                title=f"医生{current_user.full_name}更新了您的预约",
                content=content,
                data={
                    'appointment_id': appointment.id,
                    'doctor_id': current_user.id,
                    'doctor_name': current_user.full_name,
                    'update_fields': list(data.keys())
                },
                is_read=False
            )
            
            db.session.add(notification)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '预约更新成功',
            'data': {
                'appointment_id': appointment.id
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"更新预约失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'更新预约失败: {str(e)}'
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
            from ..models.prescription import PrescriptionItem
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
            type=NotificationType.PRESCRIPTION,
            title=f"医生{current_user.full_name}为您开具了处方",
            content=f"诊断: {data['diagnosis']}, 包含{len(items)}种药品",
            data={
                'prescription_id': prescription.id,
                'doctor_id': current_user.id,
                'doctor_name': current_user.full_name
            },
            is_read=False
        )
        
        db.session.add(notification)
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
                type=NotificationType.PRESCRIPTION,
                title=f"医生{current_user.full_name}已撤销您的处方",
                content=f"处方ID: {prescription.id}, 诊断: {prescription.diagnosis}",
                data={
                    'prescription_id': prescription.id,
                    'doctor_id': current_user.id,
                    'doctor_name': current_user.full_name,
                    'status': 'REVOKED'
                },
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
        
        record_type_data = {record_type.value: count for record_type, count in record_type_stats}
        
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
                'record_type': record.record_type.value if record.record_type else None,
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