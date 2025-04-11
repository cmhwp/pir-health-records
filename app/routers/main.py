from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from ..models import db, User, Role
from ..routers.auth import role_required, api_login_required

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    return jsonify({
        'message': '欢迎使用医疗系统API',
        'status': 'success'
    })

@main_bp.route('/api/health')
def health_check():
    return jsonify({
        'status': '健康',
        'version': '1.0.0'
    })

# 公共路由 - 无需身份验证
@main_bp.route('/api/public-info')
def public_info():
    return jsonify({
        'success': True,
        'data': {
            'name': '医疗研究系统',
            'description': '用于医疗研究和患者管理的系统',
            'version': '1.0.0'
        }
    })

# 受保护路由 - 需要登录
@main_bp.route('/api/protected')
@api_login_required
def protected():
    return jsonify({
        'success': True,
        'message': '已验证用户',
        'user': {
            'id': current_user.id,
            'username': current_user.username,
            'role': current_user.role.value
        }
    })

# 患者专用路由
@main_bp.route('/api/patient-dashboard')
@login_required
@role_required(Role.PATIENT)
def patient_dashboard():
    return jsonify({
        'success': True,
        'message': '患者控制台',
        'data': {
            'appointments': [
                # 这里未来会提供真实数据
                {'id': 1, 'doctor': '张医生', 'date': '2023-06-15', 'time': '10:00', 'status': '已确认'},
                {'id': 2, 'doctor': '李医生', 'date': '2023-06-20', 'time': '14:30', 'status': '等待确认'}
            ],
            'prescriptions': [
                {'id': 1, 'medication': '阿司匹林', 'dosage': '每日一次', 'doctor': '张医生', 'date': '2023-06-01'}
            ],
            'medical_records': [
                {'id': 1, 'type': '常规检查', 'doctor': '张医生', 'date': '2023-06-01', 'summary': '一切正常'}
            ]
        }
    })

# 医生专用路由
@main_bp.route('/api/doctor-dashboard')
@login_required
@role_required(Role.DOCTOR)
def doctor_dashboard():
    return jsonify({
        'success': True,
        'message': '医生控制台',
        'data': {
            'patients': [
                # 这里未来会提供真实数据
                {'id': 1, 'name': '王患者', 'age': 45, 'last_visit': '2023-06-01'},
                {'id': 2, 'name': '赵患者', 'age': 32, 'last_visit': '2023-05-20'}
            ],
            'appointments': [
                {'id': 1, 'patient': '王患者', 'date': '2023-06-15', 'time': '10:00', 'status': '已确认'},
                {'id': 2, 'patient': '赵患者', 'date': '2023-06-20', 'time': '14:30', 'status': '等待确认'}
            ],
            'schedule': [
                {'day': '周一', 'hours': '9:00-17:00'},
                {'day': '周三', 'hours': '9:00-17:00'},
                {'day': '周五', 'hours': '9:00-12:00'}
            ]
        }
    })

# 研究人员专用路由
@main_bp.route('/api/researcher-dashboard')
@api_login_required
@role_required(Role.RESEARCHER)
def researcher_dashboard():
    return jsonify({
        'success': True,
        'message': '研究人员控制台',
        'data': {
            'studies': [
                # 这里未来会提供真实数据
                {'id': 1, 'title': '高血压新疗法研究', 'status': '进行中', 'participants': 120},
                {'id': 2, 'title': '糖尿病药物副作用分析', 'status': '计划中', 'participants': 0}
            ],
            'data_access': [
                {'id': 1, 'dataset': '心脏病患者数据', 'access_level': '完全'},
                {'id': 2, 'dataset': '匿名患者生活方式调查', 'access_level': '完全'},
                {'id': 3, 'dataset': '基因组数据', 'access_level': '有限'}
            ],
            'publications': [
                {'id': 1, 'title': '高血压治疗新方法', 'journal': '中国医学杂志', 'date': '2023-01'},
                {'id': 2, 'title': '生活方式对心脏健康的影响', 'journal': '预防医学期刊', 'date': '2022-08'}
            ]
        }
    })

# 管理员专用路由
@main_bp.route('/api/admin-dashboard')
@login_required
@role_required(Role.ADMIN)
def admin_dashboard():
    return jsonify({
        'success': True,
        'message': '管理员控制台',
        'data': {
            'system_health': {
                'status': '良好',
                'database_status': '已连接',
                'storage': '45% 已使用',
                'last_backup': '2023-06-01 01:00:00'
            },
            'recent_activities': [
                {'action': '新用户注册', 'count': 12, 'time_period': '过去24小时'},
                {'action': '登录', 'count': 156, 'time_period': '过去24小时'},
                {'action': '新增约诊', 'count': 26, 'time_period': '过去24小时'}
            ],
            'user_stats': {
                'total_users': 1250,
                'active_users': 980,
                'new_users_this_month': 45
            }
        }
    }) 