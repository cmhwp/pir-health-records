from flask import Blueprint, request, jsonify, current_app, g
from flask_login import login_required, current_user
from ..models import db, User, Role, HealthRecord, RecordFile, RecordType, RecordVisibility
from ..models.health_records import (
    format_mongo_id, mongo_health_record_to_dict, get_mongo_health_record,
    batch_get_mongo_records
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
import os
import uuid
from datetime import datetime
import json
from werkzeug.utils import secure_filename

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
        record_data['doctor_name'] = current_user.name
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
            message=f'医生{current_user.name}为患者{patient.name}创建了健康记录: {record_data.get("title")}',
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
                record_dict['patient_name'] = patient.name
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
            message=f'医生{current_user.name}更新了健康记录: {record.title}',
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
            message=f'医生{current_user.name}使用PIR查询了患者{patient.name}的健康记录',
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
                message=f'医生{current_user.name}解密了健康记录: {record.title}',
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
                    message=f'医生{current_user.name}验证了健康记录合规性: {record.title}',
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
                message=f'医生{current_user.name}验证了健康记录合规性: {record.title}',
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