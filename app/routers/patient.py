from flask import Blueprint, request, jsonify, current_app, g, redirect, url_for
from flask_login import login_required, current_user
from ..models import db, User, Role, HealthRecord, RecordFile, RecordType, RecordVisibility, QueryHistory
from ..models.health_records import (
    format_mongo_id, mongo_health_record_to_dict, get_mongo_health_record,
    batch_get_mongo_records
)
from ..models.appointment import Appointment, AppointmentStatus
from ..models.prescription import Prescription, PrescriptionStatus, PrescriptionItem
from ..models.role_models import PatientInfo, DoctorInfo
from ..routers.auth import role_required
from ..utils.mongo_utils import mongo, get_mongo_db
from ..utils.log_utils import log_record
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import json
from sqlalchemy import func, distinct

# 创建蓝图
patient_bp = Blueprint('patient', __name__, url_prefix='/api/patient')

# 获取患者的预约列表
@patient_bp.route('/appointments', methods=['GET'])
@login_required
@role_required(Role.PATIENT)
def get_patient_appointments():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # 日期筛选
        date_filter = request.args.get('date_filter', 'all')  # all, upcoming, past
        
        # 状态筛选
        status = request.args.get('status')  # pending, confirmed, cancelled, completed
        
        # 构建查询
        query = Appointment.query.filter_by(patient_id=current_user.id)
        
        # 日期过滤
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        if date_filter == 'upcoming':
            query = query.filter(Appointment.appointment_time >= today)
        elif date_filter == 'past':
            query = query.filter(Appointment.appointment_time < today)
            
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
            doctor = User.query.get(appointment.doctor_id)
            
            appointment_data = {
                'id': appointment.id,
                'doctor_id': appointment.doctor_id,
                'doctor_name': doctor.full_name if doctor else "未知医生",
                'appointment_time': appointment.appointment_time.isoformat() if appointment.appointment_time else None,
                'duration': appointment.duration,
                'purpose': appointment.purpose,
                'status': appointment.status.value,
                'notes': appointment.notes,
                'created_at': appointment.created_at.isoformat() if appointment.created_at else None
            }
            
            # 添加医生信息
            if doctor and doctor.doctor_info:
                appointment_data['doctor_info'] = {
                    'hospital': doctor.doctor_info.hospital,
                    'department': doctor.doctor_info.department,
                    'specialty': doctor.doctor_info.specialty
                }
                
            result.append(appointment_data)
        
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
                },
                'upcoming_count': Appointment.query.filter_by(patient_id=current_user.id).filter(
                    Appointment.appointment_time >= today,
                    Appointment.status != AppointmentStatus.CANCELLED
                ).count()
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取预约列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取预约列表失败: {str(e)}'
        }), 500

# 创建预约
@patient_bp.route('/appointments', methods=['POST'])
@login_required
@role_required(Role.PATIENT)
def create_appointment():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '未提供预约数据'
            }), 400
        
        required_fields = ['doctor_id', 'appointment_time', 'duration', 'purpose']
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
        
        # 解析预约时间
        try:
            appointment_time = datetime.fromisoformat(data['appointment_time'].replace('Z', '+00:00'))
        except ValueError:
            return jsonify({
                'success': False,
                'message': '预约时间格式无效'
            }), 400
        
        # 检查预约时间是否在未来
        if appointment_time < datetime.now():
            return jsonify({
                'success': False,
                'message': '预约时间必须在未来'
            }), 400
        
        # 检查医生的时间冲突
        doctor_conflict = Appointment.query.filter(
            Appointment.doctor_id == data['doctor_id'],
            Appointment.status != AppointmentStatus.CANCELLED,
            Appointment.appointment_time <= appointment_time + timedelta(minutes=data['duration']),
            Appointment.appointment_time + timedelta(minutes=Appointment.duration) >= appointment_time
        ).first()
        
        if doctor_conflict:
            return jsonify({
                'success': False,
                'message': '医生在该时间段已有其他预约'
            }), 409
        
        # 检查患者的时间冲突
        patient_conflict = Appointment.query.filter(
            Appointment.patient_id == current_user.id,
            Appointment.status != AppointmentStatus.CANCELLED,
            Appointment.appointment_time <= appointment_time + timedelta(minutes=data['duration']),
            Appointment.appointment_time + timedelta(minutes=Appointment.duration) >= appointment_time
        ).first()
        
        if patient_conflict:
            return jsonify({
                'success': False,
                'message': '您在该时间段已有其他预约'
            }), 409
        
        # 创建预约
        appointment = Appointment(
            doctor_id=data['doctor_id'],
            patient_id=current_user.id,
            appointment_time=appointment_time,
            duration=data['duration'],
            purpose=data['purpose'],
            notes=data.get('notes', ''),
            status=AppointmentStatus.PENDING  # 患者创建的预约默认为待确认状态
        )
        
        db.session.add(appointment)
        
        # 记录操作
        log_record(
            message=f'患者{current_user.full_name}向医生{doctor.full_name}发起了预约',
            details={
                'doctor_id': data['doctor_id'],
                'patient_id': current_user.id,
                'appointment_time': appointment_time.isoformat(),
                'creation_time': datetime.now().isoformat()
            }
        )
        
        # 添加医生通知
        from ..models.notification import Notification, NotificationType
        
        notification = Notification(
            user_id=data['doctor_id'],
            type=NotificationType.APPOINTMENT,
            title=f"患者{current_user.full_name}向您发起了预约请求",
            content=f"预约时间: {appointment_time.strftime('%Y-%m-%d %H:%M')}, 目的: {data['purpose']}",
            data={
                'patient_id': current_user.id,
                'patient_name': current_user.full_name,
                'appointment_time': appointment_time.isoformat(),
                'status': AppointmentStatus.PENDING.value
            },
            is_read=False
        )
        
        db.session.add(notification)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '预约请求已发送，等待医生确认',
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

# 取消预约
@patient_bp.route('/appointments/<int:appointment_id>/cancel', methods=['POST'])
@login_required
@role_required(Role.PATIENT)
def cancel_appointment(appointment_id):
    try:
        # 获取预约
        appointment = Appointment.query.get(appointment_id)
        if not appointment:
            return jsonify({
                'success': False,
                'message': '预约不存在'
            }), 404
        
        # 验证患者是否有权限操作
        if appointment.patient_id != current_user.id:
            return jsonify({
                'success': False,
                'message': '没有权限操作此预约'
            }), 403
        
        # 检查预约是否已经取消或完成
        if appointment.status == AppointmentStatus.CANCELLED:
            return jsonify({
                'success': False,
                'message': '预约已经取消'
            }), 400
            
        if appointment.status == AppointmentStatus.COMPLETED:
            return jsonify({
                'success': False,
                'message': '预约已经完成，无法取消'
            }), 400
        
        # 获取医生信息用于通知
        doctor = User.query.get(appointment.doctor_id)
        
        # 更新预约状态
        appointment.status = AppointmentStatus.CANCELLED
        appointment.updated_at = datetime.now()
        
        # 记录操作
        log_record(
            message=f'患者{current_user.full_name}取消了预约',
            details={
                'patient_id': current_user.id,
                'doctor_id': appointment.doctor_id,
                'appointment_id': appointment.id,
                'appointment_time': appointment.appointment_time.isoformat() if appointment.appointment_time else None,
                'cancellation_time': datetime.now().isoformat()
            }
        )
        
        # 添加医生通知
        from ..models.notification import Notification, NotificationType
        
        if doctor:
            notification = Notification(
                user_id=doctor.id,
                type=NotificationType.APPOINTMENT,
                title=f"患者{current_user.full_name}取消了预约",
                content=f"原定预约时间: {appointment.appointment_time.strftime('%Y-%m-%d %H:%M')}, 目的: {appointment.purpose}",
                data={
                    'appointment_id': appointment.id,
                    'patient_id': current_user.id,
                    'patient_name': current_user.full_name,
                    'status': 'CANCELLED'
                },
                is_read=False
            )
            
            db.session.add(notification)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '预约已取消'
        })
        
    except Exception as e:
        current_app.logger.error(f"取消预约失败: {str(e)}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'取消预约失败: {str(e)}'
        }), 500

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
                
            # 查询患者与该医生的预约和记录历史
            appointment_count = Appointment.query.filter_by(
                patient_id=current_user.id,
                doctor_id=doctor.id
            ).count()
            
            record_count = HealthRecord.query.filter_by(
                patient_id=current_user.id,
                doctor_id=doctor.id
            ).count()
            
            doctor_data['interaction'] = {
                'appointment_count': appointment_count,
                'record_count': record_count,
                'has_interaction': (appointment_count > 0 or record_count > 0)
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
