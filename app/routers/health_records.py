from flask import Blueprint, request, jsonify, current_app, g, send_from_directory
from flask_login import login_required, current_user
from ..models import db, User, Role, HealthRecord, RecordFile, MedicationRecord, VitalSign, QueryHistory
from ..models import RecordType, RecordVisibility
from ..routers.auth import role_required
import os
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
import json
from sqlalchemy import desc, func, distinct, or_

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
        
        # 创建健康记录
        record = HealthRecord(
            patient_id=current_user.id,
            record_type=RecordType(record_data.get('record_type')),
            title=record_data.get('title'),
            description=record_data.get('description'),
            record_date=datetime.strptime(record_data.get('record_date', datetime.now().isoformat()), '%Y-%m-%dT%H:%M:%S.%f') if 'record_date' in record_data else datetime.now(),
            institution=record_data.get('institution'),
            doctor_name=record_data.get('doctor_name'),
            visibility=RecordVisibility(record_data.get('visibility', 'private')),
            tags=record_data.get('tags'),
            data=record_data.get('data')
        )
        
        db.session.add(record)
        db.session.flush()  # 获取记录ID
        
        # 处理上传的文件
        files = request.files.getlist('files')
        for file in files:
            file_info = save_uploaded_file(file)
            if file_info:
                record_file = RecordFile(
                    record_id=record.id,
                    file_name=file_info['original_name'],
                    file_path=file_info['saved_name'],
                    file_type=file_info['type'],
                    file_size=file_info['size'],
                    description=request.form.get('file_description', '')
                )
                db.session.add(record_file)
        
        # 处理用药记录
        if record.record_type == RecordType.MEDICATION and record_data.get('medication'):
            med_data = record_data.get('medication')
            medication = MedicationRecord(
                record_id=record.id,
                medication_name=med_data.get('medication_name', ''),
                dosage=med_data.get('dosage'),
                frequency=med_data.get('frequency'),
                start_date=datetime.strptime(med_data.get('start_date'), '%Y-%m-%d').date() if med_data.get('start_date') else None,
                end_date=datetime.strptime(med_data.get('end_date'), '%Y-%m-%d').date() if med_data.get('end_date') else None,
                instructions=med_data.get('instructions'),
                side_effects=med_data.get('side_effects')
            )
            db.session.add(medication)
        
        # 处理生命体征
        if record.record_type == RecordType.VITAL_SIGNS and record_data.get('vital_signs'):
            for vs_data in record_data.get('vital_signs', []):
                vital_sign = VitalSign(
                    record_id=record.id,
                    type=vs_data.get('type', ''),
                    value=float(vs_data.get('value', 0)),
                    unit=vs_data.get('unit'),
                    measured_at=datetime.strptime(vs_data.get('measured_at', datetime.now().isoformat()), '%Y-%m-%dT%H:%M:%S.%f') if 'measured_at' in vs_data else datetime.now(),
                    notes=vs_data.get('notes')
                )
                db.session.add(vital_sign)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '健康记录创建成功',
            'data': record.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
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
        
        # 基础查询
        query = HealthRecord.query
        
        # 根据用户角色过滤
        if current_user.has_role(Role.PATIENT):
            # 患者只能查看自己的记录
            query = query.filter(HealthRecord.patient_id == current_user.id)
        elif current_user.has_role(Role.DOCTOR):
            # 医生可以查看对医生可见的或公开的记录
            query = query.filter(or_(
                HealthRecord.visibility == RecordVisibility.DOCTOR,
                HealthRecord.visibility == RecordVisibility.PUBLIC
            ))
        elif current_user.has_role(Role.RESEARCHER):
            # 研究人员可以查看对研究人员可见的或公开的记录
            query = query.filter(or_(
                HealthRecord.visibility == RecordVisibility.RESEARCHER,
                HealthRecord.visibility == RecordVisibility.PUBLIC
            ))
        elif current_user.has_role(Role.ADMIN):
            # 管理员可以查看所有记录
            pass
        else:
            # 其他角色只能查看公开记录
            query = query.filter(HealthRecord.visibility == RecordVisibility.PUBLIC)
        
        # 按记录类型过滤
        if record_type:
            query = query.filter(HealthRecord.record_type == RecordType(record_type))
        
        # 按日期范围过滤
        if start_date:
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(HealthRecord.record_date >= start_date)
        if end_date:
            end_date = datetime.strptime(end_date, '%Y-%m-%d')
            query = query.filter(HealthRecord.record_date <= end_date)
        
        # 按关键字搜索
        if keyword:
            query = query.filter(or_(
                HealthRecord.title.ilike(f'%{keyword}%'),
                HealthRecord.description.ilike(f'%{keyword}%'),
                HealthRecord.tags.ilike(f'%{keyword}%')
            ))
        
        # 记录查询历史
        if current_user.has_role(Role.PATIENT):
            query_history = QueryHistory(
                user_id=current_user.id,
                query_type='record_list',
                is_anonymous=is_anonymous,
                query_params={
                    'record_type': record_type,
                    'start_date': start_date.isoformat() if start_date else None,
                    'end_date': end_date.isoformat() if end_date else None,
                    'keyword': keyword
                }
            )
            db.session.add(query_history)
            db.session.commit()
        
        # 分页
        pagination = query.order_by(desc(HealthRecord.record_date)).paginate(page=page, per_page=per_page)
        records = pagination.items
        
        return jsonify({
            'success': True,
            'data': {
                'records': [record.to_dict() for record in records],
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取健康记录列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取健康记录列表失败: {str(e)}'
        }), 500

# 获取单条健康记录详情
@health_bp.route('/records/<int:record_id>', methods=['GET'])
@login_required
def get_health_record(record_id):
    try:
        record = HealthRecord.query.get_or_404(record_id)
        
        # 检查访问权限
        if record.patient_id != current_user.id and record.visibility == RecordVisibility.PRIVATE:
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
            
        if record.visibility == RecordVisibility.DOCTOR and not current_user.has_role(Role.DOCTOR):
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
            
        if record.visibility == RecordVisibility.RESEARCHER and not current_user.has_role(Role.RESEARCHER):
            return jsonify({
                'success': False,
                'message': '没有权限访问此记录'
            }), 403
        
        # 获取关联数据
        record_data = record.to_dict()
        
        # 获取用药记录
        if record.record_type == RecordType.MEDICATION:
            medication = MedicationRecord.query.filter_by(record_id=record.id).first()
            if medication:
                record_data['medication'] = medication.to_dict()
        
        # 获取生命体征记录
        if record.record_type == RecordType.VITAL_SIGNS:
            vital_signs = VitalSign.query.filter_by(record_id=record.id).all()
            if vital_signs:
                record_data['vital_signs'] = [vs.to_dict() for vs in vital_signs]
        
        # 记录查询历史
        is_anonymous = request.args.get('anonymous', 'false').lower() == 'true'
        if current_user.has_role(Role.PATIENT):
            query_history = QueryHistory(
                user_id=current_user.id,
                record_id=record.id,
                query_type='record_detail',
                is_anonymous=is_anonymous
            )
            db.session.add(query_history)
            db.session.commit()
        
        return jsonify({
            'success': True,
            'data': record_data
        })
    except Exception as e:
        current_app.logger.error(f"获取健康记录详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取健康记录详情失败: {str(e)}'
        }), 500

# 更新健康记录
@health_bp.route('/records/<int:record_id>', methods=['PUT'])
@login_required
def update_health_record(record_id):
    try:
        record = HealthRecord.query.get_or_404(record_id)
        
        # 检查是否有权限修改（只有患者自己或管理员可以修改）
        if record.patient_id != current_user.id and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '没有权限修改此记录'
            }), 403
        
        record_data = request.json
        
        # 更新基本信息
        if 'title' in record_data:
            record.title = record_data['title']
        if 'description' in record_data:
            record.description = record_data['description']
        if 'record_date' in record_data:
            record.record_date = datetime.strptime(record_data['record_date'], '%Y-%m-%dT%H:%M:%S.%f')
        if 'institution' in record_data:
            record.institution = record_data['institution']
        if 'doctor_name' in record_data:
            record.doctor_name = record_data['doctor_name']
        if 'visibility' in record_data:
            record.visibility = RecordVisibility(record_data['visibility'])
        if 'tags' in record_data:
            record.tags = record_data['tags']
        if 'data' in record_data:
            record.data = record_data['data']
        
        # 更新用药记录
        if record.record_type == RecordType.MEDICATION and 'medication' in record_data:
            med_data = record_data['medication']
            medication = MedicationRecord.query.filter_by(record_id=record.id).first()
            
            if medication:
                if 'medication_name' in med_data:
                    medication.medication_name = med_data['medication_name']
                if 'dosage' in med_data:
                    medication.dosage = med_data['dosage']
                if 'frequency' in med_data:
                    medication.frequency = med_data['frequency']
                if 'start_date' in med_data:
                    medication.start_date = datetime.strptime(med_data['start_date'], '%Y-%m-%d').date() if med_data['start_date'] else None
                if 'end_date' in med_data:
                    medication.end_date = datetime.strptime(med_data['end_date'], '%Y-%m-%d').date() if med_data['end_date'] else None
                if 'instructions' in med_data:
                    medication.instructions = med_data['instructions']
                if 'side_effects' in med_data:
                    medication.side_effects = med_data['side_effects']
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '健康记录更新成功',
            'data': record.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新健康记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新健康记录失败: {str(e)}'
        }), 500

# 删除健康记录
@health_bp.route('/records/<int:record_id>', methods=['DELETE'])
@login_required
def delete_health_record(record_id):
    try:
        record = HealthRecord.query.get_or_404(record_id)
        
        # 检查是否有权限删除（只有患者自己或管理员可以删除）
        if record.patient_id != current_user.id and not current_user.has_role(Role.ADMIN):
            return jsonify({
                'success': False,
                'message': '没有权限删除此记录'
            }), 403
        
        # 获取所有关联文件，准备删除物理文件
        record_files = RecordFile.query.filter_by(record_id=record.id).all()
        
        # 删除数据库记录（关联的文件记录、用药记录等会通过级联删除）
        db.session.delete(record)
        db.session.commit()
        
        # 删除物理文件
        for file in record_files:
            file_path = os.path.join(UPLOAD_FOLDER, file.file_path)
            if os.path.exists(file_path):
                os.remove(file_path)
        
        return jsonify({
            'success': True,
            'message': '健康记录删除成功'
        })
    except Exception as e:
        db.session.rollback()
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
        # 查询所有记录类型的数量
        record_types = db.session.query(
            HealthRecord.record_type,
            func.count(HealthRecord.id)
        ).filter(
            HealthRecord.patient_id == current_user.id
        ).group_by(
            HealthRecord.record_type
        ).all()
        
        # 计算每月记录数量
        current_year = datetime.now().year
        monthly_records = db.session.query(
            func.extract('month', HealthRecord.record_date).label('month'),
            func.count(HealthRecord.id)
        ).filter(
            HealthRecord.patient_id == current_user.id,
            func.extract('year', HealthRecord.record_date) == current_year
        ).group_by(
            'month'
        ).all()
        
        # 获取所有生命体征数据，用于绘制趋势图
        vital_signs = db.session.query(
            VitalSign.type,
            VitalSign.value,
            VitalSign.unit,
            VitalSign.measured_at
        ).join(
            HealthRecord, VitalSign.record_id == HealthRecord.id
        ).filter(
            HealthRecord.patient_id == current_user.id,
            HealthRecord.record_type == RecordType.VITAL_SIGNS
        ).order_by(
            VitalSign.type,
            VitalSign.measured_at
        ).all()
        
        # 整理生命体征数据，按类型分组
        vital_sign_data = {}
        for vs in vital_signs:
            if vs.type not in vital_sign_data:
                vital_sign_data[vs.type] = {
                    'values': [],
                    'dates': [],
                    'unit': vs.unit
                }
            vital_sign_data[vs.type]['values'].append(vs.value)
            vital_sign_data[vs.type]['dates'].append(vs.measured_at.isoformat())
        
        # 获取用药数据
        medications = db.session.query(
            MedicationRecord.medication_name,
            func.count(MedicationRecord.id)
        ).join(
            HealthRecord, MedicationRecord.record_id == HealthRecord.id
        ).filter(
            HealthRecord.patient_id == current_user.id,
            HealthRecord.record_type == RecordType.MEDICATION
        ).group_by(
            MedicationRecord.medication_name
        ).all()
        
        # 记录查询历史
        is_anonymous = request.args.get('anonymous', 'false').lower() == 'true'
        query_history = QueryHistory(
            user_id=current_user.id,
            query_type='statistics',
            is_anonymous=is_anonymous,
            query_params={'year': current_year}
        )
        db.session.add(query_history)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': {
                'record_types': {r_type.value: count for r_type, count in record_types},
                'monthly_records': {int(month): count for month, count in monthly_records},
                'vital_signs': vital_sign_data,
                'medications': {name: count for name, count in medications}
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取健康数据统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取健康数据统计失败: {str(e)}'
        }), 500 