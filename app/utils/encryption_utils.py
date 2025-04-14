import json
import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding, hashes, hmac
from cryptography.hazmat.backends import default_backend
from flask import current_app
from datetime import datetime

def derive_key(key_material, salt=None, key_length=32):
    """
    使用PBKDF2导出加密密钥
    
    Args:
        key_material: 密钥材料（密码）
        salt: 盐值，如果未提供则生成新的
        key_length: 生成密钥的长度（字节）
    
    Returns:
        (derived_key, salt)
    """
    if not salt:
        salt = os.urandom(16)  # 生成随机盐值
    elif isinstance(salt, str):
        salt = base64.b64decode(salt)
    
    # 使用PBKDF2进行密钥派生
    backend = default_backend()
    kdf = hashlib.pbkdf2_hmac(
        'sha256',
        key_material.encode('utf-8'),
        salt,
        iterations=100000,  # 高迭代次数增强安全性
        dklen=key_length
    )
    
    return kdf, base64.b64encode(salt).decode('utf-8')

def encrypt_data(data, key):
    """
    使用AES-GCM加密数据
    
    Args:
        data: 要加密的数据（字符串或字节）
        key: 加密密钥
    
    Returns:
        加密结果（字典）
    """
    if isinstance(data, str):
        data = data.encode('utf-8')
    
    # 生成随机IV
    iv = os.urandom(12)
    
    # 创建AES-GCM加密器
    encryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(iv),
        backend=default_backend()
    ).encryptor()
    
    # 添加关联数据（AAD）用于完整性保护
    aad = f"pir-health-{datetime.now().strftime('%Y%m%d')}".encode('utf-8')
    encryptor.authenticate_additional_data(aad)
    
    # 加密数据
    ciphertext = encryptor.update(data) + encryptor.finalize()
    
    # 返回加密结果
    return {
        'ciphertext': base64.b64encode(ciphertext).decode('utf-8'),
        'iv': base64.b64encode(iv).decode('utf-8'),
        'tag': base64.b64encode(encryptor.tag).decode('utf-8'),
        'aad': base64.b64encode(aad).decode('utf-8')
    }

def decrypt_data(encrypted_data, key):
    """
    使用AES-GCM解密数据
    
    Args:
        encrypted_data: 加密数据（字典）
        key: 解密密钥
    
    Returns:
        解密后的数据（字节）
    """
    # 解码加密数据
    ciphertext = base64.b64decode(encrypted_data['ciphertext'])
    iv = base64.b64decode(encrypted_data['iv'])
    tag = base64.b64decode(encrypted_data['tag'])
    aad = base64.b64decode(encrypted_data['aad'])
    
    # 创建AES-GCM解密器
    decryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(iv, tag),
        backend=default_backend()
    ).decryptor()
    
    # 添加关联数据（AAD）
    decryptor.authenticate_additional_data(aad)
    
    # 解密数据
    return decryptor.update(ciphertext) + decryptor.finalize()

def encrypt_record(record, encryption_key):
    """
    加密健康记录
    
    Args:
        record: 健康记录（字典）
        encryption_key: 加密密钥
    
    Returns:
        加密后的记录（字典）
    """
    # 创建记录的副本，移除不需要加密的字段
    record_copy = record.copy()
    non_encrypted_fields = ['_id', 'patient_id', 'doctor_id', 'doctor_name', 'created_at', 'updated_at', 
                           'title', 'record_type', 'visibility', 'is_encrypted', 'compliance_verified',
                           'integrity_hash']
    
    # 提取需要加密的数据
    to_encrypt = {}
    for key, value in record_copy.items():
        if key not in non_encrypted_fields:
            to_encrypt[key] = value
    
    # 如果没有需要加密的数据，返回原始记录
    if not to_encrypt:
        return record
    
    # 转换为JSON字符串
    data_json = json.dumps(to_encrypt)
    
    # 导出加密密钥
    derived_key, salt = derive_key(encryption_key)
    
    # 加密数据
    encrypted_data = encrypt_data(data_json, derived_key)
    
    # 创建加密记录
    encrypted_record = {key: value for key, value in record_copy.items() if key in non_encrypted_fields}
    encrypted_record['is_encrypted'] = True
    encrypted_record['encrypted_data'] = encrypted_data
    encrypted_record['key_salt'] = salt
    encrypted_record['encryption_algorithm'] = 'AES-GCM-256'
    encrypted_record['encryption_date'] = datetime.now().isoformat()
    
    return encrypted_record

def decrypt_record(encrypted_record, encryption_key):
    """
    解密健康记录
    
    Args:
        encrypted_record: 加密的健康记录（字典）
        encryption_key: 解密密钥
    
    Returns:
        解密后的记录（字典）
    """
    # 验证记录是否已加密
    if not encrypted_record.get('is_encrypted') or 'encrypted_data' not in encrypted_record:
        raise ValueError("记录未加密或格式无效")
    
    # 获取加密数据
    encrypted_data = encrypted_record['encrypted_data']
    salt = encrypted_record['key_salt']
    
    # 导出解密密钥
    derived_key, _ = derive_key(encryption_key, salt)
    
    try:
        # 解密数据
        decrypted_json = decrypt_data(encrypted_data, derived_key)
        decrypted_data = json.loads(decrypted_json)
        
        # 创建解密记录
        decrypted_record = encrypted_record.copy()
        del decrypted_record['encrypted_data']
        
        # 合并解密后的数据
        decrypted_record.update(decrypted_data)
        
        # 标记为已解密
        decrypted_record['is_encrypted'] = False
        
        return decrypted_record
        
    except Exception as e:
        current_app.logger.error(f"解密记录失败: {str(e)}")
        raise ValueError(f"解密失败，可能是密钥错误: {str(e)}")

def verify_record_integrity(record):
    """
    验证健康记录的完整性，生成记录的完整性哈希值
    
    Args:
        record: 健康记录（字典）
    
    Returns:
        完整性哈希值
    """
    # 创建排序后的记录副本，移除完整性哈希字段
    record_copy = record.copy()
    if 'integrity_hash' in record_copy:
        del record_copy['integrity_hash']
    
    # 转换为标准化的JSON字符串（确保相同顺序的键）
    data_json = json.dumps(record_copy, sort_keys=True)
    
    # 计算SHA-256哈希
    digest = hashlib.sha256(data_json.encode('utf-8')).hexdigest()
    
    return digest

def verify_signature(data, signature, public_key):
    """
    验证数字签名
    
    Args:
        data: 签名的数据
        signature: 签名
        public_key: 公钥
    
    Returns:
        验证结果（布尔值）
    """
    # 此功能需要使用公钥密码学库如cryptography.hazmat.primitives.asymmetric
    # 根据实际需求实现
    # 示例仅作为占位符
    return True  # 返回验证结果

def generate_record_signature(record, private_key):
    """
    为健康记录生成数字签名
    
    Args:
        record: 健康记录（字典）
        private_key: 私钥
    
    Returns:
        签名
    """
    # 此功能需要使用公钥密码学库如cryptography.hazmat.primitives.asymmetric
    # 根据实际需求实现
    # 示例仅作为占位符
    return "signature"  # 返回签名

def hash_sensitive_data(data):
    """
    对敏感数据进行哈希处理（用于存储或索引）
    
    Args:
        data: 敏感数据
    
    Returns:
        哈希值
    """
    if isinstance(data, str):
        data = data.encode('utf-8')
    
    # 使用SHA-256生成哈希
    return hashlib.sha256(data).hexdigest()