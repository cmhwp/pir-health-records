from flask import Blueprint, request, jsonify, current_app, g, redirect, url_for
from flask_login import login_required, current_user
from ..models import db, User, Role, HealthRecord, RecordFile, RecordType, RecordVisibility, QueryHistory
from ..models.health_records import (
    format_mongo_id, mongo_health_record_to_dict, get_mongo_health_record,
    batch_get_mongo_records
)
from ..models.prescription import Prescription, PrescriptionStatus, PrescriptionItem
from ..models.role_models import PatientInfo, DoctorInfo
from ..routers.auth import role_required
from ..utils.mongo_utils import mongo, get_mongo_db
from ..utils.log_utils import log_record
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import json
from sqlalchemy import func, distinct
from ..models.notification import Notification, NotificationType
from ..models.institution import Institution, CustomRecordType

# 创建蓝图
patient_bp = Blueprint('patient', __name__, url_prefix='/api/patient')

# 获取患者的处方列表
@patient_bp.route('/prescriptions', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_patient_prescriptions():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 状态筛选
        status = request.args.get('status')  # ACTIVE, COMPLETED, EXPIRED, REVOKED
        
        # 构建查询
        query = Prescription.query.filter_by(patient_id=current_user.id)
        
        if status:
            try:
                status_enum = PrescriptionStatus(status)
                query = query.filter_by(status=status_enum)
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
            doctor = User.query.get(prescription.doctor_id)
            
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
                'doctor_id': prescription.doctor_id,
                'doctor_name': doctor.full_name if doctor else "未知医生",
                'diagnosis': prescription.diagnosis,
                'instructions': prescription.instructions,
                'status': prescription.status.value,
                'items': prescription_items,
                'created_at': prescription.created_at.isoformat() if prescription.created_at else None,
                'valid_until': prescription.valid_until.isoformat() if prescription.valid_until else None
            }
            
            # 添加医生信息
            if doctor and doctor.doctor_info:
                prescription_data['doctor_info'] = {
                    'hospital': doctor.doctor_info.hospital,
                    'department': doctor.doctor_info.department,
                    'specialty': doctor.doctor_info.specialty
                }
                
            result.append(prescription_data)
        
        # 获取状态计数
        status_counts = {}
        for status in PrescriptionStatus:
            status_counts[status.value] = Prescription.query.filter_by(
                patient_id=current_user.id,
                status=status
            ).count()
        
        return jsonify({
            'success': True,
            'data': {
                'prescriptions': result,
                'status_counts': status_counts,
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

# 获取医生列表
@patient_bp.route('/doctors', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_doctors():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 搜索条件
        search_term = request.args.get('search', '')
        hospital = request.args.get('hospital', '')
        department = request.args.get('department', '')
        specialty = request.args.get('specialty', '')
        
        # 构建查询
        query = User.query.join(DoctorInfo, User.id == DoctorInfo.user_id).filter(User.role == Role.DOCTOR)
        
        # 应用搜索条件
        if search_term:
            query = query.filter(User.full_name.like(f'%{search_term}%') | User.username.like(f'%{search_term}%'))
        
        if hospital:
            query = query.filter(DoctorInfo.hospital.like(f'%{hospital}%'))
            
        if department:
            query = query.filter(DoctorInfo.department.like(f'%{department}%'))
            
        if specialty:
            query = query.filter(DoctorInfo.specialty.like(f'%{specialty}%'))
        
        # 排序
        sort_by = request.args.get('sort_by', 'name')
        sort_order = request.args.get('sort_order', 'asc')
        
        if sort_by == 'name':
            if sort_order == 'desc':
                query = query.order_by(User.full_name.desc())
            else:
                query = query.order_by(User.full_name.asc())
        elif sort_by == 'experience':
            if sort_order == 'desc':
                query = query.order_by(DoctorInfo.years_of_experience.desc())
            else:
                query = query.order_by(DoctorInfo.years_of_experience.asc())
        else:
            if sort_order == 'desc':
                query = query.order_by(User.created_at.desc())
            else:
                query = query.order_by(User.created_at.asc())
        
        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        doctors = pagination.items
        
        # 处理结果
        result = []
        for doctor in doctors:
            doctor_data = {
                'id': doctor.id,
                'username': doctor.username,
                'full_name': doctor.full_name,
                'avatar': doctor.avatar
            }
            
            # 添加医生专业信息
            if doctor.doctor_info:
                doctor_data['info'] = {
                    'specialty': doctor.doctor_info.specialty,
                    'hospital': doctor.doctor_info.hospital,
                    'department': doctor.doctor_info.department,
                    'years_of_experience': doctor.doctor_info.years_of_experience,
                    'bio': doctor.doctor_info.bio
                }
                
            # 查询患者与该医生的记录历史
            record_count = HealthRecord.query.filter_by(
                patient_id=current_user.id,
                doctor_id=doctor.id
            ).count()
            
            doctor_data['interaction'] = {
                'record_count': record_count,
                'has_interaction': (record_count > 0)
            }
            
            result.append(doctor_data)
        
        # 获取筛选选项
        hospitals = db.session.query(DoctorInfo.hospital, func.count(DoctorInfo.id))\
            .filter(DoctorInfo.hospital.isnot(None))\
            .group_by(DoctorInfo.hospital)\
            .all()
            
        departments = db.session.query(DoctorInfo.department, func.count(DoctorInfo.id))\
            .filter(DoctorInfo.department.isnot(None))\
            .group_by(DoctorInfo.department)\
            .all()
            
        specialties = db.session.query(DoctorInfo.specialty, func.count(DoctorInfo.id))\
            .filter(DoctorInfo.specialty.isnot(None))\
            .group_by(DoctorInfo.specialty)\
            .all()
        
        filter_options = {
            'hospitals': {h: c for h, c in hospitals if h},
            'departments': {d: c for d, c in departments if d},
            'specialties': {s: c for s, c in specialties if s}
        }
        
        return jsonify({
            'success': True,
            'data': {
                'doctors': result,
                'filters': filter_options,
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
        current_app.logger.error(f"获取医生列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取医生列表失败: {str(e)}'
        }), 500

# 患者申请处方API
@patient_bp.route('/prescriptions/request', methods=['POST'])
@login_required
@role_required(Role.PATIENT)
def request_prescription():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供申请数据'
            }), 400
        
        # 验证必要字段
        required_fields = ['doctor_id', 'symptoms']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'message': f'缺少必要字段: {field}'
                }), 400
        
        # 验证医生存在
        doctor = User.query.get(data['doctor_id'])
        if not doctor or not doctor.has_role(Role.DOCTOR):
            return jsonify({
                'success': False,
                'message': '指定的医生不存在或无效'
            }), 404
        
        # 验证药品项目（选填）
        medications = data.get('medications', [])
        if medications and not isinstance(medications, list):
            return jsonify({
                'success': False,
                'message': '药品项目格式错误'
            }), 400
        
        # 创建处方请求
        prescription = Prescription(
            patient_id=current_user.id,
            doctor_id=data['doctor_id'],
            symptoms=data['symptoms'],  # 保存患者症状描述
            diagnosis='待医生诊断',  # 初始状态由医生诊断
            instructions=data.get('instructions', ''),
            status=PrescriptionStatus.PENDING,  # 设置为待确认状态
            valid_until=None  # 待医生确认后设置
        )
        
        db.session.add(prescription)
        db.session.flush()  # 获取处方ID
        
        # 添加患者建议的药品（如果有）
        for med_data in medications:
            if not isinstance(med_data, dict) or 'name' not in med_data:
                continue
                
            item = PrescriptionItem(
                prescription_id=prescription.id,
                medicine_name=med_data['name'],
                dosage=med_data.get('dosage', '待医生确认'),
                frequency=med_data.get('frequency', ''),
                duration=med_data.get('duration', ''),
                notes=med_data.get('notes', '')
            )
            db.session.add(item)
        
        # 记录申请信息
        patient_notes = data.get('notes', '')
        
        # 创建通知给医生
        notification = Notification(
            user_id=data['doctor_id'],
            notification_type=NotificationType.PRESCRIPTION_REQUEST,
            title=f"患者{current_user.full_name}申请处方",
            message=f"症状: {prescription.symptoms}\n患者备注: {patient_notes}",
            related_id=str(prescription.id),
            is_read=False
        )
        
        db.session.add(notification)
        
        # 记录操作
        log_record(
            message=f'患者{current_user.full_name}向医生{doctor.full_name}申请处方',
            details={
                'patient_id': current_user.id,
                'doctor_id': data['doctor_id'],
                'prescription_id': prescription.id,
                'symptoms': prescription.symptoms,
                'medications_count': len(medications),
                'request_time': datetime.now().isoformat()
            }
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '处方申请已提交，等待医生确认',
            'data': {
                'prescription_id': prescription.id,
                'status': 'PENDING'
            }
        }), 201
        
    except Exception as e:
        current_app.logger.error(f"申请处方失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'申请处方失败: {str(e)}'
        }), 500

# 患者根据医生ID获取处方历史
@patient_bp.route('/prescriptions/doctor/<int:doctor_id>', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_prescriptions_by_doctor(doctor_id):
    try:
        # 验证医生存在
        doctor = User.query.get(doctor_id)
        if not doctor or not doctor.has_role(Role.DOCTOR):
            return jsonify({
                'success': False,
                'message': '指定的医生不存在或无效'
            }), 404
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 状态筛选
        status = request.args.get('status')  # ACTIVE, COMPLETED, EXPIRED, REVOKED, PENDING
        
        # 构建查询 - 必须是当前患者且由指定医生开具的处方
        query = Prescription.query.filter_by(
            patient_id=current_user.id,
            doctor_id=doctor_id
        )
        
        if status:
            try:
                status_enum = PrescriptionStatus(status)
                query = query.filter_by(status=status_enum)
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
                'doctor_id': prescription.doctor_id,
                'doctor_name': doctor.full_name,
                'symptoms': prescription.symptoms,  # 添加症状字段
                'diagnosis': prescription.diagnosis,
                'instructions': prescription.instructions,
                'status': prescription.status.value,
                'items': prescription_items,
                'created_at': prescription.created_at.isoformat() if prescription.created_at else None,
                'valid_until': prescription.valid_until.isoformat() if prescription.valid_until else None
            }
            
            # 添加医生信息
            if doctor.doctor_info:
                prescription_data['doctor_info'] = {
                    'hospital': doctor.doctor_info.hospital,
                    'department': doctor.doctor_info.department,
                    'specialty': doctor.doctor_info.specialty
                }
                
            result.append(prescription_data)
        
        # 获取状态计数
        status_counts = {}
        for status in PrescriptionStatus:
            status_counts[status.value] = Prescription.query.filter_by(
                patient_id=current_user.id,
                doctor_id=doctor_id,
                status=status
            ).count()
        
        return jsonify({
            'success': True,
            'data': {
                'prescriptions': result,
                'doctor': {
                    'id': doctor.id,
                    'name': doctor.full_name,
                    'hospital': doctor.doctor_info.hospital if doctor.doctor_info else None,
                    'department': doctor.doctor_info.department if doctor.doctor_info else None,
                    'specialty': doctor.doctor_info.specialty if doctor.doctor_info else None
                },
                'status_counts': status_counts,
                'total_count': pagination.total,
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
        current_app.logger.error(f"获取医生处方历史失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取医生处方历史失败: {str(e)}'
        }), 500

# 获取医疗机构列表
@patient_bp.route('/institutions', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_institutions():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        search = request.args.get('search', '')
        
        # 构建查询
        query = Institution.query.filter_by(is_active=True)
        
        # 应用搜索过滤
        if search:
            query = query.filter(Institution.name.contains(search) | 
                                Institution.code.contains(search) | 
                                Institution.address.contains(search))
        
        # 获取分页结果
        pagination = query.order_by(Institution.name).paginate(page=page, per_page=per_page, error_out=False)
        institutions = pagination.items
        
        # 格式化响应
        result = [inst.to_dict() for inst in institutions]
        
        return jsonify({
            'success': True,
            'data': {
                'institutions': result,
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
        current_app.logger.error(f"获取医疗机构列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取医疗机构列表失败: {str(e)}'
        }), 500

# 获取单个医疗机构详情
@patient_bp.route('/institutions/<int:institution_id>', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_institution(institution_id):
    try:
        institution = Institution.query.get(institution_id)
        if not institution:
            return jsonify({
                'success': False,
                'message': '医疗机构不存在'
            }), 404
            
        if not institution.is_active:
            return jsonify({
                'success': False,
                'message': '该医疗机构已停用'
            }), 403
            
        return jsonify({
            'success': True,
            'data': institution.to_dict()
        })
        
    except Exception as e:
        current_app.logger.error(f"获取医疗机构详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取医疗机构详情失败: {str(e)}'
        }), 500

# 获取指定医疗机构的医生列表
@patient_bp.route('/institutions/<int:institution_id>/doctors', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_institution_doctors(institution_id):
    try:
        # 确认机构存在且激活
        institution = Institution.query.get(institution_id)
        if not institution:
            return jsonify({
                'success': False,
                'message': '医疗机构不存在'
            }), 404
            
        if not institution.is_active:
            return jsonify({
                'success': False,
                'message': '该医疗机构已停用'
            }), 403
        
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 搜索条件
        search_term = request.args.get('search', '')
        department = request.args.get('department', '')
        specialty = request.args.get('specialty', '')
        
        # 构建查询 - 筛选指定机构的医生
        query = User.query.join(DoctorInfo, User.id == DoctorInfo.user_id).filter(
            User.role == Role.DOCTOR,
            DoctorInfo.institution_id == institution_id
        )
        
        # 应用搜索条件
        if search_term:
            query = query.filter(User.full_name.like(f'%{search_term}%') | User.username.like(f'%{search_term}%'))
            
        if department:
            query = query.filter(DoctorInfo.department.like(f'%{department}%'))
            
        if specialty:
            query = query.filter(DoctorInfo.specialty.like(f'%{specialty}%'))
        
        # 排序
        sort_by = request.args.get('sort_by', 'name')
        sort_order = request.args.get('sort_order', 'asc')
        
        if sort_by == 'name':
            if sort_order == 'desc':
                query = query.order_by(User.full_name.desc())
            else:
                query = query.order_by(User.full_name.asc())
        elif sort_by == 'experience':
            if sort_order == 'desc':
                query = query.order_by(DoctorInfo.years_of_experience.desc())
            else:
                query = query.order_by(DoctorInfo.years_of_experience.asc())
        else:
            if sort_order == 'desc':
                query = query.order_by(User.created_at.desc())
            else:
                query = query.order_by(User.created_at.asc())
        
        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        doctors = pagination.items
        
        # 处理结果
        result = []
        for doctor in doctors:
            doctor_data = {
                'id': doctor.id,
                'username': doctor.username,
                'full_name': doctor.full_name,
                'avatar': doctor.avatar
            }
            
            # 添加医生专业信息
            if doctor.doctor_info:
                doctor_data['info'] = {
                    'specialty': doctor.doctor_info.specialty,
                    'department': doctor.doctor_info.department,
                    'years_of_experience': doctor.doctor_info.years_of_experience,
                    'bio': doctor.doctor_info.bio
                }
                
            # 查询患者与该医生的记录历史
            record_count = HealthRecord.query.filter_by(
                patient_id=current_user.id,
                doctor_id=doctor.id
            ).count()
            
            doctor_data['interaction'] = {
                'record_count': record_count,
                'has_interaction': (record_count > 0)
            }
            
            result.append(doctor_data)
        
        # 获取机构中的部门和专科筛选选项
        departments = db.session.query(DoctorInfo.department, func.count(DoctorInfo.id))\
            .filter(DoctorInfo.institution_id == institution_id, DoctorInfo.department.isnot(None))\
            .group_by(DoctorInfo.department)\
            .all()
            
        specialties = db.session.query(DoctorInfo.specialty, func.count(DoctorInfo.id))\
            .filter(DoctorInfo.institution_id == institution_id, DoctorInfo.specialty.isnot(None))\
            .group_by(DoctorInfo.specialty)\
            .all()
        
        filter_options = {
            'departments': {d: c for d, c in departments if d},
            'specialties': {s: c for s, c in specialties if s}
        }
        
        return jsonify({
            'success': True,
            'data': {
                'institution': institution.to_dict(),
                'doctors': result,
                'filters': filter_options,
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
        current_app.logger.error(f"获取机构医生列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取机构医生列表失败: {str(e)}'
        }), 500

# 获取记录类型列表
@patient_bp.route('/record-types', methods=['GET'])
@login_required
def get_record_types():
    try:
        # 获取所有启用的记录类型
        record_types = CustomRecordType.query.filter_by(is_active=True).all()
        
        # 转换为字典列表
        result = [record_type.to_dict() for record_type in record_types]
        
        # 按名称排序
        result.sort(key=lambda x: x['name'])
        
        return jsonify({
            'success': True,
            'data': {
                'record_types': result
            }
        })
    
    except Exception as e:
        current_app.logger.error(f"获取记录类型列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取记录类型列表失败: {str(e)}'
        }), 500

# 获取记录类型详情
@patient_bp.route('/record-types/<int:type_id>', methods=['GET'])
@login_required
def get_record_type(type_id):
    try:
        record_type = CustomRecordType.query.get(type_id)
        if not record_type:
            return jsonify({
                'success': False,
                'message': '记录类型不存在'
            }), 404
            
        if not record_type.is_active:
            return jsonify({
                'success': False,
                'message': '该记录类型已停用'
            }), 403
            
        return jsonify({
            'success': True,
            'data': record_type.to_dict()
        })
        
    except Exception as e:
        current_app.logger.error(f"获取记录类型详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取记录类型详情失败: {str(e)}'
        }), 500

# 获取系统通用代码列表(未使用)
@patient_bp.route('/system-codes', methods=['GET'])
@login_required
def get_system_codes():
    """获取系统通用代码列表，包括记录类型、医疗机构等"""
    try:
        code_type = request.args.get('type', '')
        
        response = {
            'success': True,
            'data': {}
        }
        
        # 获取记录类型
        if not code_type or code_type == 'record_types':
            record_types = CustomRecordType.query.filter_by(is_active=True).all()
            response['data']['record_types'] = [rt.to_dict() for rt in record_types]
        
        # 获取医疗机构
        if not code_type or code_type == 'institutions':
            institutions = Institution.query.filter_by(is_active=True).all()
            response['data']['institutions'] = [inst.to_dict() for inst in institutions]
        
        # 获取处方状态
        if not code_type or code_type == 'prescription_statuses':
            from ..models.prescription import PrescriptionStatus
            response['data']['prescription_statuses'] = [
                {'code': status.value, 'name': status.name} 
                for status in PrescriptionStatus
            ]
        
        # 获取记录可见性选项
        if not code_type or code_type == 'record_visibilities':
            response['data']['record_visibilities'] = [
                {'code': visibility.value, 'name': visibility.name, 'description': get_visibility_description(visibility)} 
                for visibility in RecordVisibility
            ]
        
        return jsonify(response)
        
    except Exception as e:
        current_app.logger.error(f"获取系统代码列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取系统代码列表失败: {str(e)}'
        }), 500

def get_visibility_description(visibility):
    """获取可见性的中文描述"""
    descriptions = {
        RecordVisibility.PRIVATE: "仅患者可见",
        RecordVisibility.DOCTOR: "医生可见",
        RecordVisibility.RESEARCHER: "研究人员可见",
        RecordVisibility.PUBLIC: "公开"
    }
    return descriptions.get(visibility, "")
