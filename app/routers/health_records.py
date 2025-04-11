from flask import Blueprint, request, jsonify, current_app, g, send_from_directory
from flask_login import login_required, current_user
from ..models import db, User, Role, HealthRecord, RecordFile, MedicationRecord, VitalSign, QueryHistory
from ..models import RecordType, RecordVisibility, SharePermission, SharedRecord
from ..models import Notification, NotificationType
from ..models.health_records import format_mongo_id
from ..routers.auth import role_required
from ..utils.pir_utils import (
    PIRQuery, prepare_pir_database, 
    store_health_record_mongodb, query_health_records_mongodb
)
from ..utils.mongo_utils import mongo
from bson.objectid import ObjectId
import os
import uuid
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import json
from sqlalchemy import desc, func, distinct, or_
import math
import secrets

# Add the missing function
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
        
        # 存储到MongoDB (最佳选择，优化PIR性能)
        mongo_id = store_health_record_mongodb(record_data, current_user.id, file_info)
        
        return jsonify({
            'success': True,
            'message': '健康记录创建成功',
            'data': {
                'record_id': mongo_id,
                'storage_type': 'mongodb'
            }
        }), 201
        
    except Exception as e:
        current_app.logger.error(f"创建健康记录失败: {str(e)}")
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
        # 查询MongoDB
        from bson.objectid import ObjectId
        
        try:
            record = mongo.db.health_records.find_one({'_id': ObjectId(record_id)})
        except:
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
        
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查访问权限
        if str(record['patient_id']) != str(current_user.id) and record['visibility'] == 'private':
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
            
        if record['visibility'] == 'doctor' and not current_user.has_role(Role.DOCTOR):
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
            
        if record['visibility'] == 'researcher' and not current_user.has_role(Role.RESEARCHER):
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
        
        # 将ObjectId转为字符串
        record['_id'] = str(record['_id'])
        
        # 处理日期格式
        for date_field in ['record_date', 'created_at', 'updated_at']:
            if date_field in record and record[date_field]:
                record[date_field] = record[date_field].isoformat() if isinstance(record[date_field], datetime) else record[date_field]
                
        # 处理用药记录和生命体征的日期
        if 'medication' in record and record['medication']:
            for date_field in ['start_date', 'end_date']:
                if date_field in record['medication'] and record['medication'][date_field]:
                    record['medication'][date_field] = record['medication'][date_field].isoformat() if isinstance(record['medication'][date_field], datetime) else record['medication'][date_field]
        
        if 'vital_signs' in record and record['vital_signs']:
            for vs in record['vital_signs']:
                if 'measured_at' in vs and vs['measured_at']:
                    vs['measured_at'] = vs['measured_at'].isoformat() if isinstance(vs['measured_at'], datetime) else vs['measured_at']
        
        # 记录查询历史
        is_anonymous = request.args.get('anonymous', 'false').lower() == 'true'
        query_type = 'pir_record_detail' if is_anonymous else 'standard_record_detail'
        
        mongo.db.query_history.insert_one({
            'user_id': current_user.id,
            'record_id': record_id,
            'query_type': query_type,
            'is_anonymous': is_anonymous,
            'query_time': datetime.now()
        })
        
        return jsonify({
            'success': True,
            'data': {
                'record': record
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
        from bson.objectid import ObjectId
        
        try:
            record = mongo.db.health_records.find_one({'_id': ObjectId(record_id)})
        except:
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
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
        basic_fields = ['title', 'description', 'institution', 'doctor_name', 'visibility', 'tags', 'data']
        for field in basic_fields:
            if field in update_data:
                update_fields[field] = update_data[field]
        
        # 日期字段
        if 'record_date' in update_data:
            try:
                update_fields['record_date'] = datetime.strptime(update_data['record_date'], '%Y-%m-%dT%H:%M:%S.%f')
            except ValueError:
                update_fields['record_date'] = datetime.strptime(update_data['record_date'], '%Y-%m-%dT%H:%M:%S')
        
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
                        medication_update[f'medication.{date_field}'] = datetime.strptime(med_data[date_field], '%Y-%m-%d')
                    except:
                        pass
            
            update_fields.update(medication_update)
        
        # 设置更新时间
        update_fields['updated_at'] = datetime.now()
        
        # 执行更新
        mongo.db.health_records.update_one(
            {'_id': ObjectId(record_id)},
            {'$set': update_fields}
        )
        
        # 获取更新后的记录
        updated_record = mongo.db.health_records.find_one({'_id': ObjectId(record_id)})
        updated_record['_id'] = str(updated_record['_id'])
        
        # 处理日期格式
        for date_field in ['record_date', 'created_at', 'updated_at']:
            if date_field in updated_record and updated_record[date_field]:
                updated_record[date_field] = updated_record[date_field].isoformat()
        
        return jsonify({
            'success': True,
            'message': '健康记录更新成功',
            'data': updated_record
        })
    except Exception as e:
        current_app.logger.error(f"更新健康记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新健康记录失败: {str(e)}'
        }), 500

# 删除健康记录
@health_bp.route('/records/<record_id>', methods=['DELETE'])
@login_required
def delete_health_record(record_id):
    try:
        # 从MongoDB获取记录
        from bson.objectid import ObjectId
        
        try:
            record = mongo.db.health_records.find_one({'_id': ObjectId(record_id)})
        except:
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查是否有权限删除（只有患者自己或管理员可以删除）
        if str(record['patient_id']) != str(current_user.id) and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '没有权限删除此记录'
            }), 403
        
        # 删除相关物理文件
        if 'files' in record and record['files']:
            for file in record['files']:
                file_path = os.path.join(UPLOAD_FOLDER, file.get('file_path'))
                if os.path.exists(file_path):
                    os.remove(file_path)
        
        # 删除MongoDB记录
        mongo.db.health_records.delete_one({'_id': ObjectId(record_id)})
        
        # 删除相关查询历史
        mongo.db.query_history.delete_many({'record_id': record_id})
        
        return jsonify({
            'success': True,
            'message': '健康记录删除成功'
        })
    except Exception as e:
        current_app.logger.error(f"删除健康记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除健康记录失败: {str(e)}'
        }), 500

# 获取健康数据统计信息
@health_bp.route('/statistics', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_health_statistics():
    try:
        # 使用MongoDB聚合查询获取统计数据
        
        # 获取记录类型统计
        record_types = list(mongo.db.health_records.aggregate([
            {'$match': {'patient_id': current_user.id}},
            {'$group': {'_id': '$record_type', 'count': {'$sum': 1}}},
            {'$sort': {'count': -1}}
        ]))
        
        # 获取月度记录统计
        current_year = datetime.now().year
        monthly_stats = list(mongo.db.health_records.aggregate([
            {'$match': {'patient_id': current_user.id}},
            {'$project': {
                'month': {'$month': '$record_date'},
                'year': {'$year': '$record_date'}
            }},
            {'$match': {'year': current_year}},
            {'$group': {'_id': '$month', 'count': {'$sum': 1}}},
            {'$sort': {'_id': 1}}
        ]))
        
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
        
        # 记录查询历史
        is_anonymous = request.args.get('anonymous', 'false').lower() == 'true'
        mongo.db.query_history.insert_one({
            'user_id': current_user.id,
            'query_type': 'statistics',
            'is_anonymous': is_anonymous,
            'query_params': {'year': current_year},
            'query_time': datetime.now()
        })
        
        return jsonify({
            'success': True,
            'data': {
                'record_types': {item['_id']: item['count'] for item in record_types},
                'monthly_records': {item['_id']: item['count'] for item in monthly_stats},
                'vital_signs': vital_sign_data,
                'medications': {item['_id']: item['count'] for item in medication_stats}
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取健康数据统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取健康数据统计失败: {str(e)}'
        }), 500

# =========== 隐匿查询相关API ============

# MongoDB健康记录创建
@health_bp.route('/mongo/records', methods=['POST'])
@login_required
@role_required(Role.PATIENT)
def create_mongo_health_record():
    try:
        # 获取基本记录信息
        record_data = json.loads(request.form.get('record_data', '{}'))
        
        if not record_data.get('title') or not record_data.get('record_type'):
            return jsonify({
                'success': False,
                'message': '缺少必要字段 (title, record_type)'
            }), 400
        
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
        
        # 存储到MongoDB
        mongo_id = store_health_record_mongodb(record_data, current_user.id, file_info)
        
        return jsonify({
            'success': True,
            'message': '健康记录创建成功(MongoDB)',
            'data': {
                'mongo_id': mongo_id
            }
        }), 201
        
    except Exception as e:
        current_app.logger.error(f"创建MongoDB健康记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建MongoDB健康记录失败: {str(e)}'
        }), 500

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

# 获取MongoDB中的健康记录详情
@health_bp.route('/mongo/records/<record_id>', methods=['GET'])
@login_required
def get_mongo_health_record(record_id):
    try:
        # 查询MongoDB
        from bson.objectid import ObjectId
        
        record = mongo.db.health_records.find_one({'_id': ObjectId(record_id)})
        
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
        
        # 检查访问权限
        if str(record['patient_id']) != str(current_user.id) and record['visibility'] == 'private':
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
        
        if record['visibility'] == 'doctor' and not current_user.has_role(Role.DOCTOR):
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
        
        if record['visibility'] == 'researcher' and not current_user.has_role(Role.RESEARCHER):
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
        
        # 将ObjectId转为字符串
        record['_id'] = str(record['_id'])
        
        # 处理日期格式
        for date_field in ['record_date', 'created_at', 'updated_at']:
            if date_field in record and record[date_field]:
                record[date_field] = record[date_field].isoformat() 
                
        # 处理用药记录和生命体征的日期
        if 'medication' in record and record['medication']:
            for date_field in ['start_date', 'end_date']:
                if date_field in record['medication'] and record['medication'][date_field]:
                    record['medication'][date_field] = record['medication'][date_field].isoformat()
        
        if 'vital_signs' in record and record['vital_signs']:
            for vs in record['vital_signs']:
                if 'measured_at' in vs and vs['measured_at']:
                    vs['measured_at'] = vs['measured_at'].isoformat()
        
        # 记录查询历史
        is_anonymous = request.args.get('anonymous', 'false').lower() == 'true'
        query_type = 'pir_record_detail' if is_anonymous else 'standard_record_detail'
        
        mongo.db.query_history.insert_one({
            'user_id': current_user.id,
            'record_id': record_id,
            'query_type': query_type,
            'is_anonymous': is_anonymous,
            'query_time': datetime.now()
        })
        
        return jsonify({
            'success': True,
            'data': record
        })
    except Exception as e:
        current_app.logger.error(f"获取MongoDB健康记录详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取MongoDB健康记录详情失败: {str(e)}'
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
        # 获取共享参数
        data = request.json
        if not data or not data.get('shared_with'):
            return jsonify({
                'success': False,
                'message': '缺少必要参数'
            }), 400
            
        # 验证记录存在并且属于当前用户
        mongo_id = format_mongo_id(record_id)
        if not mongo_id:
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
        record = mongo.db.health_records.find_one({'_id': mongo_id})
        if not record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
            
        if str(record.get('patient_id')) != str(current_user.id):
            return jsonify({
                'success': False,
                'message': '您没有权限共享此记录'
            }), 403
            
        # 验证共享对象用户ID
        shared_with_id = data.get('shared_with')
        shared_with_user = User.query.get(shared_with_id)
        if not shared_with_user:
            return jsonify({
                'success': False,
                'message': '共享对象用户不存在'
            }), 404
            
        # 验证是否已经共享给该用户
        existing_share = SharedRecord.query.filter_by(
            record_id=str(record['_id']),
            owner_id=current_user.id,
            shared_with=shared_with_id
        ).first()
        
        if existing_share:
            return jsonify({
                'success': False,
                'message': '已经与该用户共享此记录'
            }), 400
            
        # 解析权限
        try:
            permission_str = data.get('permission', 'view').lower()
            permission = SharePermission(permission_str)
        except ValueError:
            permission = SharePermission.VIEW
            
        # 解析过期时间
        expires_at = None
        if 'expires_days' in data and data['expires_days'] > 0:
            expires_at = datetime.now() + timedelta(days=data['expires_days'])
            
        # 生成访问密钥
        access_key = secrets.token_urlsafe(32)
            
        # 创建共享记录
        shared_record = SharedRecord(
            record_id=str(record['_id']),
            owner_id=current_user.id,
            shared_with=shared_with_id,
            permission=permission,
            expires_at=expires_at,
            access_key=access_key
        )
        
        # 创建通知
        notification = Notification(
            user_id=shared_with_id,
            sender_id=current_user.id,
            notification_type=NotificationType.RECORD_SHARED,
            title="有新的健康记录与您共享",
            message=f"{current_user.username} 共享了一条 {record['record_type']} 类型的健康记录: {record['title']}",
            related_id=str(record['_id']),
            expires_at=expires_at
        )
        
        db.session.add(shared_record)
        db.session.add(notification)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '记录共享成功',
            'data': shared_record.to_dict()
        }), 201
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"共享健康记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'共享健康记录失败: {str(e)}'
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
            # 获取MongoDB中的记录信息
            mongo_id = format_mongo_id(shared.record_id)
            mongo_record = mongo.db.health_records.find_one({'_id': mongo_id}) if mongo_id else None
            
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
                'record_info': {
                    'title': mongo_record['title'] if mongo_record else None,
                    'record_type': mongo_record['record_type'] if mongo_record else None,
                    'record_date': mongo_record['record_date'].isoformat() if mongo_record and 'record_date' in mongo_record and isinstance(mongo_record['record_date'], datetime) else None
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
            # 获取MongoDB中的记录信息
            mongo_id = format_mongo_id(shared.record_id)
            mongo_record = mongo.db.health_records.find_one({'_id': mongo_id}) if mongo_id else None
            
            # 获取共享用户信息
            owner_user = User.query.get(shared.owner_id)
            
            record_info = {
                'shared_id': shared.id,
                'record_id': shared.record_id,
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
                    'title': mongo_record['title'] if mongo_record else None,
                    'record_type': mongo_record['record_type'] if mongo_record else None,
                    'record_date': mongo_record['record_date'].isoformat() if mongo_record and 'record_date' in mongo_record and isinstance(mongo_record['record_date'], datetime) else None
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
                'message': '您没有权限访问此共享记录'
            }), 403
            
        # 检查是否过期
        if not shared_record.is_valid() and shared_record.shared_with == current_user.id:
            return jsonify({
                'success': False,
                'message': '此共享记录已过期'
            }), 403
            
        # 获取MongoDB中的记录信息
        mongo_id = format_mongo_id(shared_record.record_id)
        if not mongo_id:
            return jsonify({
                'success': False,
                'message': '无效的记录ID'
            }), 400
            
        mongo_record = mongo.db.health_records.find_one({'_id': mongo_id})
        if not mongo_record:
            return jsonify({
                'success': False,
                'message': '记录不存在'
            }), 404
            
        # 如果是被共享用户访问，记录访问情况
        if shared_record.shared_with == current_user.id:
            shared_record.record_access()
            
            # 创建访问通知给记录所有者
            notification = Notification(
                user_id=shared_record.owner_id,
                sender_id=current_user.id,
                notification_type=NotificationType.RECORD_ACCESS,
                title="您共享的记录被访问",
                message=f"{current_user.username} 访问了您共享的健康记录: {mongo_record['title']}",
                related_id=shared_record.record_id
            )
            db.session.add(notification)
            db.session.commit()
            
        # 获取用户信息
        owner = User.query.get(shared_record.owner_id)
        shared_with = User.query.get(shared_record.shared_with)
        
        # 组装返回数据
        record_data = mongo_health_record_to_dict(mongo_record)
        
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
                    'id': shared_with.id if shared_with else None,
                    'username': shared_with.username if shared_with else None,
                    'full_name': shared_with.full_name if shared_with else None
                }
            },
            'record': record_data
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

# 撤销共享
@health_bp.route('/shared/<shared_id>', methods=['DELETE'])
@login_required
def revoke_shared_record(shared_id):
    try:
        # 获取共享记录
        shared_record = SharedRecord.query.get(shared_id)
        if not shared_record:
            return jsonify({
                'success': False,
                'message': '共享记录不存在'
            }), 404
            
        # 验证权限
        if shared_record.owner_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '您没有权限撤销此共享记录'
            }), 403
            
        # 删除共享记录
        db.session.delete(shared_record)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '共享记录已撤销'
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"撤销共享记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'撤销共享记录失败: {str(e)}'
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
        
        # 转换记录为字典格式
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
        if export_format not in ['json', 'csv']:
            return jsonify({
                'success': False,
                'message': '不支持的导出格式，请使用 json 或 csv'
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
            # 转换为字典列表
            export_data = []
            for record in records:
                record_dict = mongo_health_record_to_dict(record)
                export_data.append(record_dict)
                
            # 创建导出文件名
            filename = f"health_records_export_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
            filepath = os.path.join(UPLOAD_FOLDER, "..", "exports", filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # 写入JSON文件
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
                
            return jsonify({
                'success': True,
                'message': '健康记录导出成功',
                'data': {
                    'export_format': 'json',
                    'filename': filename,
                    'record_count': len(records),
                    'download_url': f"/api/health/export/download/{filename}"
                }
            })
        
        elif export_format == 'csv':
            # 确定CSV字段
            fields = [
                'record_id', 'title', 'record_type', 'description', 'record_date',
                'institution', 'doctor_name', 'tags', 'created_at'
            ]
            
            # 创建导出文件名
            filename = f"health_records_export_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
            filepath = os.path.join(UPLOAD_FOLDER, "..", "exports", filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # 写入CSV文件
            import csv
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                
                for record in records:
                    # 将MongoDB记录转换为CSV行
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
                    writer.writerow(row)
                    
            return jsonify({
                'success': True,
                'message': '健康记录导出成功',
                'data': {
                    'export_format': 'csv',
                    'filename': filename,
                    'record_count': len(records),
                    'download_url': f"/api/health/export/download/{filename}"
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
@login_required
@role_required(Role.PATIENT)
def download_exported_records(filename):
    try:
        # 安全检查，确保只有当前用户可以下载自己的导出文件
        if f"health_records_export_{current_user.id}_" not in filename:
            return jsonify({
                'success': False,
                'message': '没有权限下载此文件'
            }), 403
            
        # 导出目录
        export_dir = os.path.join(UPLOAD_FOLDER, "..", "exports")
        
        # 获取文件类型
        file_ext = filename.split('.')[-1].lower()
        mime_type = 'application/json' if file_ext == 'json' else 'text/csv'
        
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
        if file_ext not in ['json', 'csv']:
            return jsonify({
                'success': False,
                'message': '不支持的文件格式，请上传JSON或CSV文件'
            }), 400
            
        # 保存上传的文件
        upload_dir = os.path.join(UPLOAD_FOLDER, "..", "imports")
        os.makedirs(upload_dir, exist_ok=True)
        
        filename = f"import_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{file_ext}"
        filepath = os.path.join(upload_dir, filename)
        file.save(filepath)
        
        # 处理导入数据
        imported_records = []
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
                    continue
                    
                # 确保记录类型有效
                if record_data['record_type'] not in [t.value for t in RecordType]:
                    record_data['record_type'] = RecordType.OTHER.value
                    
                # 设置患者ID为当前用户
                record_data['patient_id'] = current_user.id
                
                # 设置记录日期
                if 'record_date' not in record_data or not record_data['record_date']:
                    record_data['record_date'] = datetime.now()
                else:
                    try:
                        record_date = datetime.fromisoformat(record_data['record_date'])
                        record_data['record_date'] = record_date
                    except ValueError:
                        record_data['record_date'] = datetime.now()
                
                # 存储到MongoDB
                result = mongo.db.health_records.insert_one(record_data)
                
                # 添加到已导入列表
                imported_records.append({
                    'id': str(result.inserted_id),
                    'title': record_data['title'],
                    'record_type': record_data['record_type']
                })
                
        elif file_ext == 'csv':
            import csv
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    # 确保记录数据有效
                    if not row or 'title' not in row or 'record_type' not in row:
                        continue
                        
                    # 转换为记录数据
                    record_data = {
                        'title': row.get('title', ''),
                        'record_type': row.get('record_type', RecordType.OTHER.value),
                        'description': row.get('description', ''),
                        'patient_id': current_user.id,
                        'created_at': datetime.now(),
                        'updated_at': datetime.now()
                    }
                    
                    # 设置记录日期
                    if 'record_date' in row and row['record_date']:
                        try:
                            record_date = datetime.fromisoformat(row['record_date'])
                            record_data['record_date'] = record_date
                        except ValueError:
                            record_data['record_date'] = datetime.now()
                    else:
                        record_data['record_date'] = datetime.now()
                        
                    # 添加其他字段
                    for field in ['institution', 'doctor_name', 'tags']:
                        if field in row and row[field]:
                            record_data[field] = row[field]
                    
                    # 存储到MongoDB
                    result = mongo.db.health_records.insert_one(record_data)
                    
                    # 添加到已导入列表
                    imported_records.append({
                        'id': str(result.inserted_id),
                        'title': record_data['title'],
                        'record_type': record_data['record_type']
                    })
        
        return jsonify({
            'success': True,
            'message': f'成功导入 {len(imported_records)} 条健康记录',
            'data': {
                'imported_records': imported_records,
                'count': len(imported_records)
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"导入健康记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'导入健康记录失败: {str(e)}'
        }), 500 