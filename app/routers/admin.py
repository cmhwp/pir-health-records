from flask import Blueprint, request, jsonify, current_app, send_from_directory
from flask_login import login_required, current_user
from ..models import db, User, Role, PatientInfo, DoctorInfo, ResearcherInfo
from ..routers.auth import role_required, api_login_required
from sqlalchemy import or_
import os
import json
import time
from datetime import datetime, timedelta
from sqlalchemy.sql import func, distinct, desc

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

# 获取所有用户列表
@admin_bp.route('/users', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_users():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search = request.args.get('search', '')
    role_filter = request.args.get('role', '')
    
    query = User.query
    
    # 按角色过滤
    if role_filter:
        try:
            role_enum = next(r for r in Role if r.value == role_filter)
            query = query.filter(User.role == role_enum)
        except (StopIteration, ValueError):
            pass  # 无效角色，忽略过滤
    
    # 搜索
    if search:
        query = query.filter(or_(
            User.username.ilike(f'%{search}%'),
            User.email.ilike(f'%{search}%'),
            User.full_name.ilike(f'%{search}%')
        ))
    
    # 分页
    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=per_page)
    
    return jsonify({
        'success': True,
        'data': {
            'users': [user.to_dict() for user in pagination.items],
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        }
    })

# 获取单个用户详情
@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_user(user_id):
    user = User.query.get_or_404(user_id)
    
    return jsonify({
        'success': True,
        'data': user.to_dict()
    })

# 创建用户（管理员专用）
@admin_bp.route('/users', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def create_user():
    data = request.json
    
    # 基础验证
    if not data or not data.get('username') or not data.get('email') or not data.get('password'):
        return jsonify({
            'success': False,
            'message': '缺少必要字段 (username, email, password)'
        }), 400
        
    # 检查用户名和邮箱是否已存在
    if User.query.filter_by(username=data['username']).first():
        return jsonify({
            'success': False,
            'message': '用户名已存在'
        }), 400
        
    if User.query.filter_by(email=data['email']).first():
        return jsonify({
            'success': False,
            'message': '邮箱已被注册'
        }), 400
    
    try:
        # 创建新用户
        user = User(
            username=data['username'],
            email=data['email'],
            password=data['password'],  # 使用setter方法加密
            full_name=data.get('full_name', ''),
            phone=data.get('phone', ''),
            is_active=data.get('is_active', True)
        )
        
        # 设置角色
        role_str = data.get('role', 'patient').lower()
        if role_str == 'doctor':
            user.role = Role.DOCTOR
        elif role_str == 'researcher':
            user.role = Role.RESEARCHER
        elif role_str == 'admin':
            user.role = Role.ADMIN
        else:
            user.role = Role.PATIENT
            
        db.session.add(user)
        db.session.flush()  # 获取用户ID
        
        # 根据角色创建相应的附加信息
        if user.role == Role.PATIENT and 'patient_info' in data:
            patient_info = PatientInfo(user_id=user.id)
            patient_data = data['patient_info']
            
            for field in ['gender', 'address', 'emergency_contact', 'emergency_phone', 
                         'medical_history', 'allergies']:
                if field in patient_data:
                    setattr(patient_info, field, patient_data[field])
                    
            db.session.add(patient_info)
        
        elif user.role == Role.DOCTOR and 'doctor_info' in data:
            doctor_info = DoctorInfo(user_id=user.id)
            doctor_data = data['doctor_info']
            
            for field in ['specialty', 'license_number', 'years_of_experience', 'education',
                         'hospital', 'department', 'bio']:
                if field in doctor_data:
                    setattr(doctor_info, field, doctor_data[field])
                    
            db.session.add(doctor_info)
        
        elif user.role == Role.RESEARCHER and 'researcher_info' in data:
            researcher_info = ResearcherInfo(user_id=user.id)
            researcher_data = data['researcher_info']
            
            for field in ['institution', 'department', 'research_area', 'education',
                         'publications', 'projects', 'bio']:
                if field in researcher_data:
                    setattr(researcher_info, field, researcher_data[field])
                    
            db.session.add(researcher_info)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '用户创建成功',
            'data': user.to_dict()
        }), 201
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建用户失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'创建用户失败: {str(e)}'
        }), 500

# 更新用户信息（管理员专用）
@admin_bp.route('/users/<int:user_id>', methods=['PUT'])
@api_login_required
@role_required(Role.ADMIN)
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    data = request.json
    
    # 更新基本信息
    if 'username' in data and data['username'] != user.username:
        if User.query.filter_by(username=data['username']).first():
            return jsonify({
                'success': False,
                'message': '用户名已存在'
            }), 400
        user.username = data['username']
        
    if 'email' in data and data['email'] != user.email:
        if User.query.filter_by(email=data['email']).first():
            return jsonify({
                'success': False,
                'message': '邮箱已被注册'
            }), 400
        user.email = data['email']
    
    if 'password' in data:
        user.password = data['password']
        
    if 'full_name' in data:
        user.full_name = data['full_name']
        
    if 'phone' in data:
        user.phone = data['phone']
        
    if 'is_active' in data:
        user.is_active = data['is_active']
    
    # 更新角色
    if 'role' in data:
        role_str = data['role'].lower()
        old_role = user.role
        
        if role_str == 'doctor':
            user.role = Role.DOCTOR
        elif role_str == 'researcher':
            user.role = Role.RESEARCHER
        elif role_str == 'admin':
            user.role = Role.ADMIN
        else:
            user.role = Role.PATIENT
        
        # 如果角色发生变化，创建相应的附加信息
        if old_role != user.role:
            if user.role == Role.PATIENT and not user.patient_info:
                patient_info = PatientInfo(user_id=user.id)
                db.session.add(patient_info)
                
            elif user.role == Role.DOCTOR and not user.doctor_info:
                doctor_info = DoctorInfo(user_id=user.id)
                db.session.add(doctor_info)
                
            elif user.role == Role.RESEARCHER and not user.researcher_info:
                researcher_info = ResearcherInfo(user_id=user.id)
                db.session.add(researcher_info)
    
    # 更新角色特定信息
    if user.role == Role.PATIENT and 'patient_info' in data:
        patient_info = user.patient_info or PatientInfo(user_id=user.id)
        patient_data = data['patient_info']
        
        for field in ['gender', 'address', 'emergency_contact', 'emergency_phone', 
                     'medical_history', 'allergies']:
            if field in patient_data:
                setattr(patient_info, field, patient_data[field])
        
        if patient_info.id is None:
            db.session.add(patient_info)
    
    elif user.role == Role.DOCTOR and 'doctor_info' in data:
        doctor_info = user.doctor_info or DoctorInfo(user_id=user.id)
        doctor_data = data['doctor_info']
        
        for field in ['specialty', 'license_number', 'years_of_experience', 'education',
                     'hospital', 'department', 'bio']:
            if field in doctor_data:
                setattr(doctor_info, field, doctor_data[field])
                
        if doctor_info.id is None:
            db.session.add(doctor_info)
    
    elif user.role == Role.RESEARCHER and 'researcher_info' in data:
        researcher_info = user.researcher_info or ResearcherInfo(user_id=user.id)
        researcher_data = data['researcher_info']
        
        for field in ['institution', 'department', 'research_area', 'education',
                     'publications', 'projects', 'bio']:
            if field in researcher_data:
                setattr(researcher_info, field, researcher_data[field])
                
        if researcher_info.id is None:
            db.session.add(researcher_info)
    
    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'message': '用户信息已更新',
            'data': user.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新用户信息失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新失败: {str(e)}'
        }), 500

# 删除用户
@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@api_login_required
@role_required(Role.ADMIN)
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # 防止删除自己
    if user.id == current_user.id:
        return jsonify({
            'success': False,
            'message': '不能删除当前登录的用户'
        }), 400
    
    try:
        db.session.delete(user)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'用户 {user.username} 已被删除'
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除用户失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'删除用户失败: {str(e)}'
        }), 500

# 获取系统统计数据
@admin_bp.route('/stats', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_stats():
    try:
        total_users = User.query.count()
        patient_count = User.query.filter_by(role=Role.PATIENT).count()
        doctor_count = User.query.filter_by(role=Role.DOCTOR).count()
        researcher_count = User.query.filter_by(role=Role.RESEARCHER).count()
        admin_count = User.query.filter_by(role=Role.ADMIN).count()
        
        active_users = User.query.filter_by(is_active=True).count()
        inactive_users = User.query.filter_by(is_active=False).count()
        
        return jsonify({
            'success': True,
            'data': {
                'total_users': total_users,
                'role_distribution': {
                    'patients': patient_count,
                    'doctors': doctor_count,
                    'researchers': researcher_count,
                    'admins': admin_count
                },
                'active_users': active_users,
                'inactive_users': inactive_users
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取统计数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取统计数据失败: {str(e)}'
        }), 500

# 获取系统日志
@admin_bp.route('/logs', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_system_logs():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        log_type = request.args.get('type', '')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        from ..models.log import SystemLog
        query = SystemLog.query
        
        # 按日志类型筛选
        if log_type:
            query = query.filter(SystemLog.log_type == log_type)
            
        # 按日期范围筛选
        if start_date:
            try:
                start_datetime = datetime.fromisoformat(start_date)
                query = query.filter(SystemLog.created_at >= start_datetime)
            except ValueError:
                pass
                
        if end_date:
            try:
                end_datetime = datetime.fromisoformat(end_date)
                query = query.filter(SystemLog.created_at <= end_datetime)
            except ValueError:
                pass
        
        # 分页
        pagination = query.order_by(SystemLog.created_at.desc()).paginate(page=page, per_page=per_page)
        
        return jsonify({
            'success': True,
            'data': {
                'logs': [log.to_dict() for log in pagination.items],
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': page
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取系统日志失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取系统日志失败: {str(e)}'
        }), 500

# 获取系统健康状态
@admin_bp.route('/system/health', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_system_health():
    try:
        import psutil
        from ..utils.mongo_utils import mongo
        
        # CPU和内存使用情况
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # 数据库连接状态
        mysql_status = True
        mongo_status = True
        
        try:
            # 验证MySQL连接
            db.session.execute("SELECT 1")
        except Exception:
            mysql_status = False
            
        try:
            # 验证MongoDB连接
            mongo.db.command('ping')
        except Exception:
            mongo_status = False
            
        # 系统状态指标
        from ..models.user import User
        from ..models.health_records import HealthRecord
        
        active_users_count = db.session.query(func.count(distinct(User.id))).filter(
            User.last_login_at >= (datetime.now() - timedelta(days=7))
        ).scalar()
        
        total_records = mongo.db.health_records.count_documents({})
        
        return jsonify({
            'success': True,
            'data': {
                'system': {
                    'cpu_usage': cpu_percent,
                    'memory_usage': {
                        'total': memory.total,
                        'available': memory.available,
                        'percent': memory.percent,
                    },
                    'disk_usage': {
                        'total': disk.total,
                        'used': disk.used,
                        'free': disk.free,
                        'percent': disk.percent
                    },
                    'uptime': int(time.time() - psutil.boot_time())
                },
                'database': {
                    'mysql_status': mysql_status,
                    'mongo_status': mongo_status,
                    'record_count': total_records
                },
                'users': {
                    'active_users': active_users_count,
                    'total_users': User.query.count()
                },
                'timestamp': datetime.now().isoformat()
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取系统健康状态失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取系统健康状态失败: {str(e)}'
        }), 500

# 获取用户活动统计
@admin_bp.route('/users/activity', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_user_activity():
    try:
        days = request.args.get('days', 30, type=int)
        user_id = request.args.get('user_id', type=int)
        
        from ..models.user import User
        from ..models import QueryHistory
        
        start_date = datetime.now() - timedelta(days=days)
        
        # 构建查询
        query = db.session.query(
            func.date(QueryHistory.query_time).label('date'),
            func.count().label('count')
        ).filter(QueryHistory.query_time >= start_date)
        
        # 如果指定了用户ID，则只查询该用户的活动
        if user_id:
            user = User.query.get_or_404(user_id)
            query = query.filter(QueryHistory.user_id == user_id)
            
        # 按日期分组
        query = query.group_by(func.date(QueryHistory.query_time)).order_by('date')
        
        # 执行查询
        activity_data = [{'date': str(row.date), 'count': row.count} for row in query.all()]
        
        # 获取用户类型分布
        role_distribution = db.session.query(
            User.role, 
            func.count().label('count')
        ).group_by(User.role).all()
        
        role_data = {str(role.value): count for role, count in role_distribution}
        
        # 获取最活跃的用户
        active_users = db.session.query(
            User.id, 
            User.username,
            User.email,
            User.role,
            func.count(QueryHistory.id).label('activity_count')
        ).join(QueryHistory, User.id == QueryHistory.user_id)\
         .filter(QueryHistory.query_time >= start_date)\
         .group_by(User.id)\
         .order_by(desc('activity_count'))\
         .limit(10).all()
        
        active_users_data = [{
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': str(user.role.value),
            'activity_count': activity_count
        } for user, activity_count in active_users]
        
        return jsonify({
            'success': True,
            'data': {
                'daily_activity': activity_data,
                'role_distribution': role_data,
                'most_active_users': active_users_data,
                'period_days': days
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取用户活动统计失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取用户活动统计失败: {str(e)}'
        }), 500

# 批量管理记录
@admin_bp.route('/records/batch', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def batch_manage_records():
    try:
        data = request.json
        
        if not data or 'action' not in data or 'record_ids' not in data:
            return jsonify({
                'success': False,
                'message': '缺少必要参数 (action, record_ids)'
            }), 400
            
        action = data['action']
        record_ids = data['record_ids']
        
        if not record_ids or not isinstance(record_ids, list):
            return jsonify({
                'success': False,
                'message': 'record_ids必须是非空列表'
            }), 400
            
        # 根据不同的操作执行不同的逻辑
        if action == 'delete':
            # 标记删除记录
            from ..models.health_records import format_mongo_id
            from ..utils.mongo_utils import get_mongo_db
            
            mongo_db = get_mongo_db()
            deleted_count = 0
            
            for record_id in record_ids:
                mongo_id = format_mongo_id(record_id)
                if not mongo_id:
                    continue
                    
                # 获取记录
                record = mongo_db.health_records.find_one({'_id': mongo_id})
                if not record:
                    continue
                    
                # 备份到删除集合
                record['deletion_time'] = datetime.now()
                record['deleted_by'] = current_user.id
                record['deletion_reason'] = data.get('reason', '管理员批量删除')
                
                mongo_db.health_records_deleted.insert_one(record)
                mongo_db.health_records.delete_one({'_id': mongo_id})
                
                deleted_count += 1
                
            return jsonify({
                'success': True,
                'message': f'成功删除{deleted_count}条记录',
                'data': {
                    'deleted_count': deleted_count
                }
            })
        
        elif action == 'visibility':
            # 修改记录可见性
            if 'visibility' not in data:
                return jsonify({
                    'success': False,
                    'message': '缺少visibility参数'
                }), 400
                
            visibility = data['visibility']
            
            # 验证可见性值
            from ..models import RecordVisibility
            try:
                RecordVisibility(visibility)
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': f'无效的可见性值: {visibility}'
                }), 400
                
            # 执行批量更新
            from ..models.health_records import bulk_update_visibility
            
            updated_count = bulk_update_visibility(record_ids, visibility, current_user.id)
            
            return jsonify({
                'success': True,
                'message': f'批量更新可见性成功，共更新{updated_count}条记录',
                'data': {
                    'updated_count': updated_count
                }
            })
            
        else:
            return jsonify({
                'success': False,
                'message': f'不支持的操作: {action}'
            }), 400
            
    except Exception as e:
        current_app.logger.error(f"批量管理记录失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'批量管理记录失败: {str(e)}'
        }), 500

# 导出系统数据
@admin_bp.route('/export/data', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def export_system_data():
    try:
        data = request.json
        if not data or 'export_type' not in data:
            return jsonify({
                'success': False,
                'message': '缺少必要参数 (export_type)'
            }), 400
            
        export_type = data.get('export_type')
        
        # 创建导出目录
        export_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'exports')
        os.makedirs(export_dir, exist_ok=True)
        
        # 生成导出文件名
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        filename = f"system_export_{export_type}_{timestamp}.json"
        filepath = os.path.join(export_dir, filename)
        
        # 根据类型导出不同的数据
        if export_type == 'users':
            # 导出用户数据
            users = User.query.all()
            export_data = [user.to_dict() for user in users]
            
        elif export_type == 'health_records':
            # 导出健康记录数据
            patient_id = data.get('patient_id')
            limit = data.get('limit', 1000)
            
            from ..utils.mongo_utils import get_mongo_db
            
            mongo_db = get_mongo_db()
            query = {}
            
            if patient_id:
                query['patient_id'] = patient_id
                
            cursor = mongo_db.health_records.find(query).limit(limit)
            export_data = [record for record in cursor]
            
            # 处理ObjectId格式
            for record in export_data:
                record['_id'] = str(record['_id'])
                
        elif export_type == 'system_logs':
            # 导出系统日志
            from ..models.log import SystemLog
            
            start_date = data.get('start_date')
            end_date = data.get('end_date')
            
            query = SystemLog.query
            
            if start_date:
                try:
                    start_datetime = datetime.fromisoformat(start_date)
                    query = query.filter(SystemLog.created_at >= start_datetime)
                except ValueError:
                    pass
                    
            if end_date:
                try:
                    end_datetime = datetime.fromisoformat(end_date)
                    query = query.filter(SystemLog.created_at <= end_datetime)
                except ValueError:
                    pass
            
            logs = query.order_by(SystemLog.created_at.desc()).all()
            export_data = [log.to_dict() for log in logs]
            
        else:
            return jsonify({
                'success': False,
                'message': f'不支持的导出类型: {export_type}'
            }), 400
            
        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2, default=str)
            
        # 返回下载链接
        download_url = f"/api/admin/export/download/{filename}"
        
        return jsonify({
            'success': True,
            'message': f'成功导出{export_type}数据',
            'data': {
                'filename': filename,
                'download_url': download_url,
                'record_count': len(export_data)
            }
        })
    except Exception as e:
        current_app.logger.error(f"导出系统数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'导出系统数据失败: {str(e)}'
        }), 500

# 下载导出的数据
@admin_bp.route('/export/download/<filename>', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def download_exported_data(filename):
    try:
        export_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'exports')
        return send_from_directory(export_dir, filename, as_attachment=True)
    except Exception as e:
        current_app.logger.error(f"下载导出数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'下载导出数据失败: {str(e)}'
        }), 500

# 获取系统设置
@admin_bp.route('/settings', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_system_settings():
    try:
        # 从数据库或配置文件获取系统设置
        settings = {}
        
        # 尝试从数据库获取存储的配置
        from ..models.system_settings import SystemSetting
        stored_settings = SystemSetting.query.all()
        
        for setting in stored_settings:
            # 解析JSON值
            try:
                if setting.value_type == 'json':
                    settings[setting.key] = json.loads(setting.value)
                elif setting.value_type == 'int':
                    settings[setting.key] = int(setting.value)
                elif setting.value_type == 'float':
                    settings[setting.key] = float(setting.value)
                elif setting.value_type == 'bool':
                    settings[setting.key] = setting.value.lower() in ('true', 'yes', '1')
                else:
                    settings[setting.key] = setting.value
            except:
                settings[setting.key] = setting.value
        
        # 添加一些应用配置
        settings['pir_enabled'] = current_app.config.get('PIR_ENABLE_OBFUSCATION', False)
        settings['debug_mode'] = current_app.config.get('DEBUG', False)
        settings['upload_limit'] = current_app.config.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024)
        
        # 分组设置
        grouped_settings = {
            'security': {
                'password_policy': settings.get('password_policy', {}),
                'login_attempts': settings.get('login_attempts', 5),
                'session_timeout': settings.get('session_timeout', 30),
                'require_email_confirmation': settings.get('require_email_confirmation', True)
            },
            'privacy': {
                'pir_enabled': settings.get('pir_enabled', False),
                'pir_batch_size': settings.get('pir_batch_size', 10),
                'default_record_visibility': settings.get('default_record_visibility', 'private')
            },
            'system': {
                'debug_mode': settings.get('debug_mode', False),
                'upload_limit': settings.get('upload_limit', 16 * 1024 * 1024),
                'maintenance_mode': settings.get('maintenance_mode', False),
                'max_export_size': settings.get('max_export_size', 1000)
            },
            'notifications': {
                'email_notifications': settings.get('email_notifications', True),
                'system_notifications': settings.get('system_notifications', True),
                'notification_types': settings.get('notification_types', [
                    'record_access', 'record_share', 'system_update'
                ])
            }
        }
        
        return jsonify({
            'success': True,
            'data': {
                'settings': grouped_settings,
                'raw_settings': settings
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取系统设置失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取系统设置失败: {str(e)}'
        }), 500

# 更新系统设置
@admin_bp.route('/settings', methods=['PUT'])
@api_login_required
@role_required(Role.ADMIN)
def update_system_settings():
    try:
        data = request.json
        if not data:
            return jsonify({
                'success': False,
                'message': '缺少设置数据'
            }), 400
            
        from ..models.system_settings import SystemSetting
        
        updated_settings = []
        errors = []
        
        # 遍历提交的设置并更新
        for key, value in data.items():
            try:
                # 确定值类型
                value_type = 'string'
                if isinstance(value, bool):
                    value_type = 'bool'
                    value = str(value).lower()
                elif isinstance(value, int):
                    value_type = 'int'
                    value = str(value)
                elif isinstance(value, float):
                    value_type = 'float'
                    value = str(value)
                elif isinstance(value, (dict, list)):
                    value_type = 'json'
                    value = json.dumps(value)
                
                # 查找现有设置或创建新设置
                setting = SystemSetting.query.filter_by(key=key).first()
                
                if setting:
                    # 更新现有设置
                    setting.value = value
                    setting.value_type = value_type
                    setting.updated_at = datetime.now()
                    setting.updated_by = current_user.id
                else:
                    # 创建新设置
                    setting = SystemSetting(
                        key=key,
                        value=value,
                        value_type=value_type,
                        created_by=current_user.id,
                        updated_by=current_user.id
                    )
                    db.session.add(setting)
                    
                updated_settings.append(key)
                
                # 特殊处理某些设置
                if key == 'maintenance_mode':
                    # 如果启用维护模式，记录日志
                    if value.lower() in ('true', 'yes', '1'):
                        from ..models.log import SystemLog, LogType
                        log = SystemLog(
                            user_id=current_user.id,
                            log_type=LogType.SYSTEM,
                            message=f'系统进入维护模式',
                            details=json.dumps({
                                'enabled_by': current_user.username,
                                'timestamp': datetime.now().isoformat()
                            })
                        )
                        db.session.add(log)
            
            except Exception as e:
                errors.append({
                    'key': key,
                    'error': str(e)
                })
        
        db.session.commit()
        
        # 如果有错误，返回部分成功的响应
        if errors:
            return jsonify({
                'success': True,
                'message': f'部分设置更新成功，{len(errors)}个设置更新失败',
                'data': {
                    'updated': updated_settings,
                    'errors': errors
                }
            }), 207
        
        return jsonify({
            'success': True,
            'message': f'成功更新{len(updated_settings)}个系统设置',
            'data': {
                'updated': updated_settings
            }
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新系统设置失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'更新系统设置失败: {str(e)}'
        }), 500

# 系统维护操作
@admin_bp.route('/maintenance', methods=['POST'])
@api_login_required
@role_required(Role.ADMIN)
def system_maintenance():
    try:
        data = request.json
        if not data or 'action' not in data:
            return jsonify({
                'success': False,
                'message': '缺少必要参数 (action)'
            }), 400
            
        action = data['action']
        
        if action == 'clear_cache':
            # 清除系统缓存
            from ..models.cache_item import CacheItem
            
            # 统计删除前的缓存数量
            cache_count = CacheItem.query.count()
            
            # 删除所有缓存项
            CacheItem.query.delete()
            db.session.commit()
            
            # 记录日志
            from ..models.log import SystemLog, LogType
            log = SystemLog(
                user_id=current_user.id,
                log_type=LogType.SYSTEM,
                message=f'清除系统缓存',
                details=json.dumps({
                    'cleared_by': current_user.username,
                    'items_cleared': cache_count,
                    'timestamp': datetime.now().isoformat()
                })
            )
            db.session.add(log)
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': f'成功清除系统缓存，共删除{cache_count}个缓存项'
            })
            
        elif action == 'vacuum_db':
            # 整理数据库
            # 注意：这个操作可能需要直接执行SQL语句
            try:
                # 对MySQL进行整理
                db.session.execute("ANALYZE TABLE users, health_records, record_files")
                
                # 对MongoDB进行整理
                from ..utils.mongo_utils import mongo
                mongo.db.command('compact', 'health_records')
                
                # 记录日志
                from ..models.log import SystemLog, LogType
                log = SystemLog(
                    user_id=current_user.id,
                    log_type=LogType.SYSTEM,
                    message=f'数据库维护操作',
                    details=json.dumps({
                        'action': 'vacuum_db',
                        'performed_by': current_user.username,
                        'timestamp': datetime.now().isoformat()
                    })
                )
                db.session.add(log)
                db.session.commit()
                
                return jsonify({
                    'success': True,
                    'message': '数据库整理操作完成'
                })
                
            except Exception as e:
                return jsonify({
                    'success': False,
                    'message': f'数据库整理操作失败: {str(e)}'
                }), 500
                
        elif action == 'backup':
            # 数据库备份操作
            # 注意：实际实现可能需要调用外部脚本或命令
            
            backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            
            # 这里是简化的示例，实际应该调用适当的备份命令
            backup_file = f"database_backup_{timestamp}.sql"
            backup_path = os.path.join(backup_dir, backup_file)
            
            # 记录日志
            from ..models.log import SystemLog, LogType
            log = SystemLog(
                user_id=current_user.id,
                log_type=LogType.SYSTEM,
                message=f'创建数据库备份',
                details=json.dumps({
                    'backup_file': backup_file,
                    'backup_path': backup_path,
                    'created_by': current_user.username,
                    'timestamp': datetime.now().isoformat()
                })
            )
            db.session.add(log)
            db.session.commit()
            
            # 记录备份信息到数据库
            # 这里可以添加备份历史记录表的插入操作
            
            return jsonify({
                'success': True,
                'message': '数据库备份请求已提交',
                'data': {
                    'backup_file': backup_file
                }
            })
            
        else:
            return jsonify({
                'success': False,
                'message': f'不支持的维护操作: {action}'
            }), 400
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"系统维护操作失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'系统维护操作失败: {str(e)}'
        }), 500

# 获取系统性能指标
@admin_bp.route('/metrics', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def get_system_metrics():
    try:
        metric_type = request.args.get('type', 'all')
        period = request.args.get('period', '24h')
        
        # 解析时间周期
        if period == '24h':
            start_time = datetime.now() - timedelta(hours=24)
        elif period == '7d':
            start_time = datetime.now() - timedelta(days=7)
        elif period == '30d':
            start_time = datetime.now() - timedelta(days=30)
        else:
            start_time = datetime.now() - timedelta(hours=24)
        
        # 获取不同类型的指标
        metrics = {}
        
        # 用户活动指标
        if metric_type in ['all', 'user']:
            from ..models import QueryHistory
            
            # 查询数量
            query_count = QueryHistory.query.filter(
                QueryHistory.query_time >= start_time
            ).count()
            
            # 活跃用户数量
            active_users = db.session.query(func.count(distinct(QueryHistory.user_id))).filter(
                QueryHistory.query_time >= start_time
            ).scalar()
            
            # 按小时分组的查询活动
            if period == '24h':
                # 按小时分组
                hourly_activity = db.session.query(
                    func.extract('hour', QueryHistory.query_time).label('hour'),
                    func.count().label('count')
                ).filter(
                    QueryHistory.query_time >= start_time
                ).group_by('hour').order_by('hour').all()
                
                activity_data = {
                    'labels': [f"{int(row[0])}:00" for row in hourly_activity],
                    'values': [row[1] for row in hourly_activity]
                }
            else:
                # 按天分组
                daily_activity = db.session.query(
                    func.date(QueryHistory.query_time).label('date'),
                    func.count().label('count')
                ).filter(
                    QueryHistory.query_time >= start_time
                ).group_by('date').order_by('date').all()
                
                activity_data = {
                    'labels': [str(row[0]) for row in daily_activity],
                    'values': [row[1] for row in daily_activity]
                }
            
            metrics['user'] = {
                'query_count': query_count,
                'active_users': active_users,
                'activity_data': activity_data
            }
        
        # 系统性能指标
        if metric_type in ['all', 'system']:
            import psutil
            
            # 当前CPU和内存使用情况
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            
            metrics['system'] = {
                'cpu_usage': cpu_percent,
                'memory_usage': memory.percent,
                'system_uptime': int(time.time() - psutil.boot_time())
            }
            
        # 数据库指标
        if metric_type in ['all', 'database']:
            from ..utils.mongo_utils import mongo
            
            # 获取MongoDB统计信息
            mongo_stats = mongo.db.command('dbStats')
            
            # 获取MySQL表行数
            table_counts = {}
            tables = ['users', 'health_records', 'record_files', 'query_history', 'shared_records']
            
            for table in tables:
                count = db.session.execute(f"SELECT COUNT(*) FROM {table}").scalar()
                table_counts[table] = count
            
            metrics['database'] = {
                'mysql': {
                    'table_counts': table_counts,
                    'total_rows': sum(table_counts.values())
                },
                'mongodb': {
                    'storage_size': mongo_stats.get('storageSize', 0),
                    'data_size': mongo_stats.get('dataSize', 0),
                    'collections': mongo_stats.get('collections', 0),
                    'objects': mongo_stats.get('objects', 0)
                }
            }
            
        return jsonify({
            'success': True,
            'data': {
                'metrics': metrics,
                'period': period,
                'timestamp': datetime.now().isoformat()
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取系统性能指标失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取系统性能指标失败: {str(e)}'
        }), 500

# 获取管理员仪表盘数据
@admin_bp.route('/dashboard', methods=['GET'])
@api_login_required
@role_required(Role.ADMIN)
def admin_dashboard():
    try:
        # 获取各种统计数据
        from ..models.user import User
        from ..models import QueryHistory, HealthRecord, SharedRecord
        from ..models.system_settings import SystemSetting
        from ..models.log import SystemLog
        from ..utils.mongo_utils import mongo
        from datetime import datetime, timedelta
        import json
        
        # 系统概览数据
        system_overview = {
            'total_users': User.query.count(),
            'total_records': mongo.db.health_records.count_documents({}),
            'total_shared_records': SharedRecord.query.count(),
            'total_queries': QueryHistory.query.count()
        }
        
        # 用户类型分布
        user_distribution = db.session.query(
            User.role, 
            func.count().label('count')
        ).group_by(User.role).all()
        
        role_data = {str(role.value): count for role, count in user_distribution}
        
        # 获取最近的活动
        # 1. 最近的系统日志
        recent_logs = SystemLog.query.order_by(SystemLog.created_at.desc()).limit(5).all()
        
        # 2. 最近的用户注册
        recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
        
        # 3. 最近的查询历史
        recent_queries = QueryHistory.query.order_by(QueryHistory.query_time.desc()).limit(5).all()
        
        # 健康记录随时间变化
        now = datetime.now()
        timeline_data = []
        
        # 过去30天的每日健康记录数量
        for i in range(30, 0, -1):
            date = now - timedelta(days=i)
            date_str = date.strftime('%Y-%m-%d')
            
            # 计算该日的记录数量
            count = mongo.db.health_records.count_documents({
                'record_date': {
                    '$gte': datetime(date.year, date.month, date.day, 0, 0, 0),
                    '$lt': datetime(date.year, date.month, date.day, 23, 59, 59)
                }
            })
            
            timeline_data.append({
                'date': date_str,
                'count': count
            })
        
        # 获取系统设置
        maintenance_mode = SystemSetting.get_setting('maintenance_mode', False)
        
        # 获取最近错误日志计数
        error_count = SystemLog.query.filter_by(
            log_type='error'
        ).filter(
            SystemLog.created_at >= (now - timedelta(days=1))
        ).count()
        
        # 检查是否需要数据库备份
        last_backup_log = SystemLog.query.filter(
            SystemLog.message.like('%数据库备份%')
        ).order_by(SystemLog.created_at.desc()).first()
        
        needs_backup = True
        if last_backup_log and last_backup_log.created_at > (now - timedelta(days=7)):
            needs_backup = False
            
        # 系统警报
        alerts = []
        
        if maintenance_mode:
            alerts.append({
                'type': 'warning',
                'message': '系统当前处于维护模式',
                'details': '维护模式已启用，部分用户功能可能受限'
            })
            
        if error_count > 10:
            alerts.append({
                'type': 'error',
                'message': f'过去24小时内发生了{error_count}个错误',
                'details': '请检查系统日志以获取详细信息'
            })
            
        if needs_backup:
            alerts.append({
                'type': 'info',
                'message': '建议进行数据库备份',
                'details': '已有超过7天未进行数据库备份'
            })
        
        # PIR使用情况
        pir_enabled = current_app.config.get('PIR_ENABLE_OBFUSCATION', False)
        pir_query_count = QueryHistory.query.filter_by(is_anonymous=True).count()
        
        return jsonify({
            'success': True,
            'data': {
                'system_overview': system_overview,
                'user_distribution': role_data,
                'recent_activity': {
                    'logs': [log.to_dict() for log in recent_logs],
                    'users': [user.to_dict() for user in recent_users],
                    'queries': [query.to_dict() for query in recent_queries]
                },
                'timeline_data': timeline_data,
                'system_status': {
                    'maintenance_mode': maintenance_mode,
                    'error_count_24h': error_count,
                    'needs_backup': needs_backup,
                    'pir_enabled': pir_enabled,
                    'pir_query_count': pir_query_count
                },
                'alerts': alerts
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取管理员仪表盘数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'获取管理员仪表盘数据失败: {str(e)}'
        }), 500 