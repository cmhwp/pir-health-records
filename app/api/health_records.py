from flask import Blueprint, request, jsonify, session
from app.models.mysql import db, User, HealthRecord, PrivacyPolicy
from app.models.mongo import mongo_client
from app.models.redis import redis_client
from app.utils.security import login_required, role_required, validate_health_record, hash_content, encrypt_data
from app.pir.pir_service import pir_system
import json
from datetime import datetime

health_records_bp = Blueprint('health_records', __name__)

@health_records_bp.route('', methods=['POST'])
@login_required
@role_required(['doctor', 'admin'])
def create_health_record():
    """创建带有隐私保护的新健康记录"""
    data = request.get_json()
    
    # 验证健康记录数据
    is_valid, message = validate_health_record(data)
    if not is_valid:
        return jsonify({'message': message}), 400
    
    # 获取当前用户（医生或管理员）
    current_user_id = session.get('user_id')
    
    # 检查患者是否存在
    patient = User.query.get(data['patient_id'])
    if not patient:
        return jsonify({'message': '患者未找到'}), 404
    
    # 检查患者的隐私政策
    privacy_policy = PrivacyPolicy.query.filter_by(user_id=data['patient_id']).first()
    if privacy_policy:
        # 根据政策检查当前用户是否有访问权限
        # 这是一个简化的检查；在实际系统中会更复杂
        if privacy_policy.allowed_users and str(current_user_id) not in json.loads(privacy_policy.allowed_users):
            return jsonify({'message': '被患者隐私政策拒绝访问'}), 403
    
    # 准备内容以便存储到MongoDB（加密存储）
    content = data['content']
    
    # 创建内容哈希以进行完整性验证
    content_hash = hash_content(content)
    
    # 在MySQL中创建新的健康记录
    new_record = HealthRecord(
        patient_id=data['patient_id'],
        doctor_id=current_user_id,
        record_type=data['record_type'],
        content_hash=content_hash,
        mongo_id='pending',  # 将在MongoDB存储后更新
        created_at=datetime.now()
    )
    
    db.session.add(new_record)
    db.session.flush()  # 获取ID而不提交
    
    # 将记录添加到PIR系统并存储在MongoDB中
    mongo_id = pir_system.add_record(new_record.id, content)
    
    # 更新MongoDB ID
    new_record.mongo_id = mongo_id
    
    # 提交到数据库
    db.session.commit()
    
    return jsonify({
        'message': '健康记录创建成功',
        'record': {
            'id': new_record.id,
            'patient_id': new_record.patient_id,
            'doctor_id': new_record.doctor_id,
            'record_type': new_record.record_type,
            'created_at': new_record.created_at.isoformat()
        }
    }), 201

@health_records_bp.route('/<int:record_id>', methods=['GET'])
@login_required
def get_health_record(record_id):
    """获取带有隐私保护的健康记录"""
    current_user_id = session.get('user_id')
    current_user = User.query.get(current_user_id)
    
    # 在MySQL中查找记录
    record = HealthRecord.query.get(record_id)
    if not record:
        return jsonify({'message': '记录未找到'}), 404
    
    # 检查授权
    # 用户可以查看自己的记录，医生可以查看他们创建的记录，管理员可以查看所有
    if current_user.role != 'admin' and current_user_id != record.patient_id and current_user_id != record.doctor_id:
        return jsonify({'message': '未授权访问'}), 403
    
    # 检查隐私政策
    privacy_policy = PrivacyPolicy.query.filter_by(user_id=record.patient_id).first()
    if privacy_policy and current_user_id != record.patient_id:
        # 如果不是管理员或创建记录的医生，检查允许的用户
        if current_user.role != 'admin' and current_user_id != record.doctor_id:
            if privacy_policy.allowed_users and str(current_user_id) not in json.loads(privacy_policy.allowed_users):
                return jsonify({'message': '被患者隐私政策拒绝访问'}), 403
    
    # 使用PIR检索记录内容
    query_params = {
        'query_type': 'record_id',
        'record_id': record_id,
        'user_id': current_user_id
    }
    
    result = pir_system.query(query_params)
    
    if 'error' in result:
        return jsonify({'message': result['error']}), 400
    
    # 验证内容完整性
    if 'content' in result:
        content_hash = hash_content(result['content'])
        if content_hash != record.content_hash:
            return jsonify({'message': '记录完整性验证失败'}), 500
    
    # 构建响应
    record_data = {
        'id': record.id,
        'patient_id': record.patient_id,
        'doctor_id': record.doctor_id,
        'record_type': record.record_type,
        'content': result.get('content'),
        'created_at': record.created_at.isoformat(),
        'updated_at': record.updated_at.isoformat() if record.updated_at else None
    }
    
    return jsonify({'record': record_data}), 200

@health_records_bp.route('/user/<int:user_id>', methods=['GET'])
@login_required
def get_user_health_records(user_id):
    """获取特定用户的所有健康记录"""
    current_user_id = session.get('user_id')
    current_user = User.query.get(current_user_id)
    
    # 授权检查
    # 用户可以查看自己的记录，医生可以查看其患者的记录，管理员可以查看所有
    if current_user.role != 'admin' and current_user_id != user_id:
        if current_user.role == 'doctor':
            # 检查医生是否有此患者的任何记录
            doctor_patient_records = HealthRecord.query.filter_by(doctor_id=current_user_id, patient_id=user_id).first()
            if not doctor_patient_records:
                return jsonify({'message': '未授权访问'}), 403
        else:
            return jsonify({'message': '未授权访问'}), 403
    
    # 从MySQL中检索记录
    records = HealthRecord.query.filter_by(patient_id=user_id).all()
    
    # 构建不含内容的响应（内容将使用PIR单独检索）
    records_data = [{
        'id': record.id,
        'patient_id': record.patient_id,
        'doctor_id': record.doctor_id,
        'record_type': record.record_type,
        'created_at': record.created_at.isoformat(),
        'updated_at': record.updated_at.isoformat() if record.updated_at else None
    } for record in records]
    
    return jsonify({'records': records_data}), 200

@health_records_bp.route('/<int:record_id>', methods=['PUT'])
@login_required
@role_required(['doctor', 'admin'])
def update_health_record(record_id):
    """使用隐私保护更新健康记录"""
    current_user_id = session.get('user_id')
    current_user = User.query.get(current_user_id)
    
    # 在MySQL中查找记录
    record = HealthRecord.query.get(record_id)
    if not record:
        return jsonify({'message': '记录未找到'}), 404
    
    # 检查授权 - 只有创建记录的医生或管理员可以更新
    if current_user.role != 'admin' and current_user_id != record.doctor_id:
        return jsonify({'message': '未授权访问'}), 403
    
    data = request.get_json()
    
    # 验证健康记录数据（简化以用于更新）
    if not data or 'content' not in data:
        return jsonify({'message': '没有提供用于更新的内容'}), 400
    
    # 在MongoDB和PIR系统中更新记录
    # 首先检索现有记录以与更新合并
    query_params = {
        'query_type': 'record_id',
        'record_id': record_id,
        'user_id': current_user_id
    }
    
    existing_result = pir_system.query(query_params)
    
    if 'error' in existing_result:
        return jsonify({'message': existing_result['error']}), 400
    
    existing_content = existing_result.get('content', {})
    
    # 合并现有内容与更新（简单的字典更新）
    if isinstance(data['content'], dict) and isinstance(existing_content, dict):
        updated_content = {**existing_content, **data['content']}
    else:
        updated_content = data['content']
    
    # 为更新后的内容创建新的哈希
    content_hash = hash_content(updated_content)
    
    # 更新MongoDB和PIR索引
    mongo_id = pir_system.add_record(record.id, updated_content)
    
    # 更新MySQL记录
    record.content_hash = content_hash
    record.mongo_id = mongo_id
    record.updated_at = datetime.now()
    
    if 'record_type' in data:
        record.record_type = data['record_type']
    
    db.session.commit()
    
    return jsonify({
        'message': '健康记录更新成功',
        'record': {
            'id': record.id,
            'patient_id': record.patient_id,
            'doctor_id': record.doctor_id,
            'record_type': record.record_type,
            'updated_at': record.updated_at.isoformat()
        }
    }), 200

@health_records_bp.route('/privacy-policy', methods=['POST'])
@login_required
def set_privacy_policy():
    """为用户的健康记录设置隐私政策"""
    current_user_id = session.get('user_id')
    
    data = request.get_json()
    
    if not data or not data.get('policy_type') or not data.get('access_level'):
        return jsonify({'message': '缺少必填字段'}), 400
    
    # 验证政策设置
    valid_policy_types = ['default', 'custom']
    valid_access_levels = ['high', 'medium', 'low']
    
    if data['policy_type'] not in valid_policy_types:
        return jsonify({'message': f"政策类型必须是以下之一: {', '.join(valid_policy_types)}"}), 400
    
    if data['access_level'] not in valid_access_levels:
        return jsonify({'message': f"访问级别必须是以下之一: {', '.join(valid_access_levels)}"}), 400
    
    # 检查用户是否已有政策
    existing_policy = PrivacyPolicy.query.filter_by(user_id=current_user_id).first()
    
    if existing_policy:
        # 更新现有政策
        existing_policy.policy_type = data['policy_type']
        existing_policy.access_level = data['access_level']
        existing_policy.allowed_users = json.dumps(data.get('allowed_users', []))
        existing_policy.updated_at = datetime.now()
    else:
        # 创建新政策
        new_policy = PrivacyPolicy(
            user_id=current_user_id,
            policy_type=data['policy_type'],
            access_level=data['access_level'],
            allowed_users=json.dumps(data.get('allowed_users', [])),
            created_at=datetime.now()
        )
        db.session.add(new_policy)
    
    db.session.commit()
    
    return jsonify({'message': '隐私政策设置成功'}), 200

@health_records_bp.route('/privacy-policy', methods=['GET'])
@login_required
def get_privacy_policy():
    """获取当前用户的隐私政策"""
    current_user_id = session.get('user_id')
    
    policy = PrivacyPolicy.query.filter_by(user_id=current_user_id).first()
    
    if not policy:
        return jsonify({
            'message': '未找到隐私政策',
            'default_policy': {
                'policy_type': 'default',
                'access_level': 'medium',
                'allowed_users': []
            }
        }), 404
    
    policy_data = {
        'id': policy.id,
        'policy_type': policy.policy_type,
        'access_level': policy.access_level,
        'allowed_users': json.loads(policy.allowed_users) if policy.allowed_users else [],
        'created_at': policy.created_at.isoformat() if policy.created_at else None,
        'updated_at': policy.updated_at.isoformat() if policy.updated_at else None
    }
    
    return jsonify({'policy': policy_data}), 200 