import json
import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding, hashes, hmac
from cryptography.hazmat.backends import default_backend
from flask import current_app
from datetime import datetime
from bson import ObjectId
import random

# 在encryption_utils中直接实现DateTimeEncoder
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, ObjectId):
            return str(obj)
        return super(DateTimeEncoder, self).default(obj)

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
    # 确保key_material是字符串
    if not isinstance(key_material, str):
        key_material = str(key_material)
    
    if not salt:
        salt = os.urandom(16)  # 生成随机盐值
    elif isinstance(salt, str):
        try:
            # 尝试Base64解码
            salt = base64.b64decode(salt)
        except Exception as e:
            salt = salt.encode('utf-8')
    
    # 使用PBKDF2进行密钥派生
    try:
        backend = default_backend()
        kdf = hashlib.pbkdf2_hmac(
            'sha256',
            key_material.encode('utf-8'),
            salt,
            iterations=100000,  # 高迭代次数增强安全性
            dklen=key_length
        )
        
        encoded_salt = base64.b64encode(salt).decode('utf-8')
        return kdf, encoded_salt
    except Exception as e:
        raise ValueError(f"密钥派生失败: {str(e)}")

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
    try:
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
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        
        return plaintext
    except Exception as e:
        raise ValueError(f"解密数据失败: {str(e)}")

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
    try:
        # 验证记录是否已加密
        if not encrypted_record.get('is_encrypted', False):
            raise ValueError("记录未加密或格式无效")
        
        # 检查所需的加密字段
        if 'encrypted_data' not in encrypted_record:
            raise ValueError("记录格式无效，缺少加密数据")
            
        if 'key_salt' not in encrypted_record:
            raise ValueError("记录格式无效，缺少密钥盐值")
        
        # 获取加密数据
        encrypted_data = encrypted_record['encrypted_data']
        salt = encrypted_record['key_salt']
        
        # 导出解密密钥
        derived_key, _ = derive_key(encryption_key, salt)
        
        # 检查加密数据结构
        required_fields = ['ciphertext', 'iv', 'tag', 'aad']
        for field in required_fields:
            if field not in encrypted_data:
                raise ValueError(f"加密数据格式无效，缺少 {field} 字段")
        
        # 解密数据
        decrypted_json = decrypt_data(encrypted_data, derived_key)
        
        try:
            # 解析JSON
            decrypted_data = json.loads(decrypted_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"解密数据不是有效的JSON: {str(e)}")
        
        # 创建解密记录
        decrypted_record = encrypted_record.copy()
        del decrypted_record['encrypted_data']
        
        # 合并解密后的数据
        decrypted_record.update(decrypted_data)
        
        # 标记为已解密
        decrypted_record['is_encrypted'] = False
        
        return decrypted_record
        
    except Exception as e:
        raise ValueError(f"解密失败，可能是密钥错误: {str(e)}")

def decrypt_structured_data(encrypted_record_data, encryption_key=None):
    """
    解密结构化健康记录数据（API专用）
    
    Args:
        encrypted_record_data: 结构化加密记录数据, 可能包含以下字段:
            - encrypted_data: 加密数据字典，包含ciphertext, iv, tag, aad
            - key_salt: 密钥盐值
            - encryption_algorithm: 加密算法
            - integrity_hash: 完整性哈希
        encryption_key: 解密密钥, 如果为None, 则尝试使用默认密钥
    
    Returns:
        解密后的数据
    """
    try:
        # 验证数据结构
        if not isinstance(encrypted_record_data, dict):
            raise ValueError("无效的数据结构: 预期字典类型")
            
        # 检查必要字段
        if 'encrypted_data' not in encrypted_record_data:
            raise ValueError("数据缺少encrypted_data字段")
            
        if 'key_salt' not in encrypted_record_data:
            raise ValueError("数据缺少key_salt字段")
            
        # 验证算法
        algorithm = encrypted_record_data.get('encryption_algorithm')
        if algorithm != 'AES-GCM-256':
            raise ValueError(f"不支持的加密算法: {algorithm}")
            
        # 如果没有提供密钥，使用默认密钥或生成一个派生自记录特征的密钥
        if not encryption_key:
            # 使用系统密钥和完整性哈希派生解密密钥
            try:
                hash_value = encrypted_record_data.get('integrity_hash', '')
                if not hash_value:
                    # 尝试使用record_id或其他唯一标识符
                    record_id = encrypted_record_data.get('_id', '')
                    salt = encrypted_record_data.get('key_salt', '')
                    hash_value = f"{record_id}_{salt}"
                    
                system_key = current_app.config.get('SECRET_KEY', 'default-key')
                encryption_key = f"{system_key}_{hash_value[:16]}"
            except Exception as e:
                # 如果派生失败，使用默认密钥
                current_app.logger.warning(f"无法派生解密密钥: {str(e)}")
                encryption_key = current_app.config.get('DEFAULT_ENCRYPTION_KEY', 'default-encryption-key')
                
        # 获取加密数据
        encrypted_data = encrypted_record_data['encrypted_data']
        salt = encrypted_record_data['key_salt']
        
        # 派生解密密钥
        derived_key, _ = derive_key(encryption_key, salt)
        
        # 检查加密数据结构
        required_fields = ['ciphertext', 'iv', 'tag', 'aad']
        for field in required_fields:
            if field not in encrypted_data:
                raise ValueError(f"加密数据格式无效，缺少 {field} 字段")
        
        # 解密数据
        decrypted_bytes = decrypt_data(encrypted_data, derived_key)
        decrypted_str = decrypted_bytes.decode('utf-8')
        
        try:
            # 尝试解析为JSON
            decrypted_data = json.loads(decrypted_str)
            
            # 创建结果数据
            result = {
                'decrypted_data': decrypted_data,
                'decryption_success': True,
                'metadata': {
                    'original_hash': encrypted_record_data.get('integrity_hash', ''),
                    'encryption_date': encrypted_record_data.get('encryption_date', ''),
                    'decryption_date': datetime.now().isoformat()
                }
            }
            
            return result
            
        except json.JSONDecodeError:
            # 如果不是有效的JSON，返回原始字符串
            return {
                'decrypted_data': decrypted_str,
                'decryption_success': True,
                'metadata': {
                    'format': 'string',
                    'original_hash': encrypted_record_data.get('integrity_hash', ''),
                    'encryption_date': encrypted_record_data.get('encryption_date', ''),
                    'decryption_date': datetime.now().isoformat()
                }
            }
    
    except Exception as e:
        # 捕获所有解密过程中的错误
        current_app.logger.error(f"解密结构化数据失败: {str(e)}")
        return {
            'decryption_success': False,
            'error_message': str(e),
            'metadata': {
                'original_hash': encrypted_record_data.get('integrity_hash', '') if isinstance(encrypted_record_data, dict) else ''
            }
        }

def verify_record_integrity(record):
    """
    验证记录完整性
    
    Args:
        record: 健康记录
        
    Returns:
        完整性哈希
    """
    try:
        # 创建记录副本，移除完整性哈希和一些不影响内容的元数据
        record_copy = record.copy()
        excluded_fields = ['integrity_hash', '_id']
        for field in excluded_fields:
            if field in record_copy:
                del record_copy[field]
        
        # 序列化为JSON
        try:
            json_data = json.dumps(record_copy, sort_keys=True, cls=DateTimeEncoder)
        except TypeError as e:
            # 如果有特殊类型无法序列化，尝试进行简单处理
            current_app.logger.warning(f"记录包含无法直接序列化的字段: {str(e)}")
            # 简单处理：将所有日期转为字符串
            for key, value in record_copy.items():
                if isinstance(value, datetime):
                    record_copy[key] = value.isoformat()
            
            json_data = json.dumps(record_copy, sort_keys=True)
        
        # 计算哈希
        return hashlib.sha256(json_data.encode()).hexdigest()
        
    except Exception as e:
        current_app.logger.error(f"计算记录完整性哈希失败: {str(e)}")
        # 返回空哈希
        return ""

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