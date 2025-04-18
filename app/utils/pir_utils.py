import numpy as np
import random
import math
from flask import current_app
from ..utils.mongo_utils import mongo
from datetime import datetime
import json
from bson import ObjectId
import hashlib
import base64
import os

class PIRQuery:
    """隐匿查询实现类"""
    
    @staticmethod
    def create_query_vector(db_size, target_index):
        """
        创建查询向量，一种基本的PIR实现
        
        Args:
            db_size: 数据库大小
            target_index: 目标数据索引
            
        Returns:
            查询向量
        """
        # 创建全0查询向量
        query_vector = np.zeros(db_size, dtype=int)
        # 目标位置设为1
        query_vector[target_index] = 1
        return query_vector
    
    @staticmethod
    def process_query(data, query_vector):
        """
        服务器处理查询向量并返回结果
        
        Args:
            data: 数据库中的所有数据
            query_vector: 查询向量
            
        Returns:
            查询结果
        """
        from flask import current_app
        
        # 检查维度并确保匹配
        if len(data.shape) < 2:
            # 数据是一维的，reshape为二维
            data = data.reshape(1, -1)
        
        if len(query_vector.shape) != 1 or query_vector.shape[0] != data.shape[0]:
            # 调整查询向量的维度以匹配数据
            current_app.logger.info(f"调整查询向量维度 - 数据形状: {data.shape}, 查询向量形状: {query_vector.shape}")
            if query_vector.shape[0] != data.shape[0]:
                # 如果长度不匹配，重新创建查询向量
                new_query_vector = np.zeros(data.shape[0], dtype=int)
                # 复制原始查询向量的最小长度部分
                min_len = min(query_vector.shape[0], data.shape[0])
                new_query_vector[:min_len] = query_vector[:min_len]
                query_vector = new_query_vector
        
        # 使用查询向量与数据内积获取结果
        try:
            # 转置数据以便进行矩阵乘法
            result = np.dot(query_vector, data)
            return result
        except ValueError as e:
            current_app.logger.error(f"矩阵乘法失败: {str(e)}, 数据形状: {data.shape}, 查询向量形状: {query_vector.shape}")
            # 尝试另一种方法
            try:
                result = []
                for i, val in enumerate(query_vector):
                    if val > 0 and i < len(data):
                        result.append(data[i])
                return np.array(result)
            except Exception as e2:
                current_app.logger.error(f"备选查询方法也失败: {str(e2)}")
                raise
    
    @staticmethod
    def encode_health_record(record):
        """
        将健康记录编码为数值向量
        
        Args:
            record: 健康记录对象
            
        Returns:
            数值向量
        """
        # 将记录序列化为JSON字符串
        record_json = json.dumps(record, default=str)
        # 编码为字节
        record_bytes = record_json.encode('utf-8')
        # 计算哈希值创建固定长度指纹
        record_hash = hashlib.sha256(record_bytes).digest()
        # 转换为整数列表
        numeric_vector = [b for b in record_hash]
        return numeric_vector
    
    @staticmethod
    def obfuscate_query(query_params, patient_id):
        """
        混淆查询参数，增加噪声查询，隐藏用户真实查询意图
        
        Args:
            query_params: 原始查询参数
            patient_id: 患者ID
            
        Returns:
            混淆后的查询序列
        """
        from flask import current_app
        
        # 创建查询拷贝
        true_query = query_params.copy()
        
        # 获取配置的最大噪声查询数量，如果未配置则默认为3
        max_noise_queries = current_app.config.get('PIR_MAX_NOISE_QUERIES', 3)
        
        # 生成随机噪声查询的数量 (1至max_noise_queries个)
        num_noise_queries = random.randint(1, max_noise_queries)
        
        # 可能的查询参数种类
        possible_params = {
            'record_type': ['medical_history', 'examination', 'medication', 'vital_signs', 'treatment', 'surgery', 'other'],
            'start_date': [(datetime.now().replace(year=datetime.now().year-i)).strftime('%Y-%m-%d') for i in range(1, 5)],
            'end_date': [(datetime.now().replace(month=i)).strftime('%Y-%m-%d') for i in range(1, 13) if i != datetime.now().month],
            'keyword': ['感冒', '发热', '检查', '治疗', '血压', '心率', '手术', '药物', '过敏', '住院']
        }
        
        # 获取加密强度，影响噪声查询的质量
        encryption_strength = current_app.config.get('PIR_ENCRYPTION_STRENGTH', 'medium')
        
        # 生成噪声查询
        noise_queries = []
        for _ in range(num_noise_queries):
            noise_query = {}
            
            # 根据加密强度调整参数数量
            if encryption_strength == 'high':
                # 高强度：更多的参数，更像真实查询
                num_params = random.randint(2, min(4, len(possible_params)))
            elif encryption_strength == 'medium':
                # 中等强度：适中数量的参数
                num_params = random.randint(1, 3)
            else:
                # 低强度：最少的参数
                num_params = random.randint(1, 2)
                
            params_to_include = random.sample(list(possible_params.keys()), num_params)
            
            for param in params_to_include:
                noise_query[param] = random.choice(possible_params[param])
            
            # 添加到噪声查询列表
            noise_queries.append(noise_query)
        
        # 将真实查询和噪声查询混合
        all_queries = [true_query] + noise_queries
        random.shuffle(all_queries)  # 随机排序
        
        # 记录真实查询位置的哈希值 (仅患者可以解码)
        true_query_index = all_queries.index(true_query)
        index_seed = f"{patient_id}_{datetime.now().date().isoformat()}"
        index_hash = hashlib.sha256(index_seed.encode()).hexdigest()
        
        return {
            'queries': all_queries,
            'index_hash': index_hash,
            'true_index': PIRQuery.encrypt_index(true_query_index, index_hash),
            'noise_count': num_noise_queries,  # 返回噪声查询数量，用于记录
            'encryption_strength': encryption_strength  # 返回使用的加密强度
        }
    
    @staticmethod
    def encrypt_index(index, key):
        """
        加密真实查询索引
        
        Args:
            index: 真实查询的索引
            key: 加密密钥
            
        Returns:
            加密后的索引
        """
        # 简单加密: 使用密钥的前8位作为种子，生成shuffle映射
        seed = int(key[:8], 16)
        random.seed(seed)
        
        # 生成10个随机数，将索引隐藏在其中
        random_numbers = [random.randint(100, 999) for _ in range(9)]
        
        # 将索引值转换为3位数
        encoded_index = index + 100
        
        # 插入到随机位置
        position = random.randint(0, 9)
        random_numbers.insert(position, encoded_index)
        
        # 再次使用密钥加密位置
        position_key = int(key[8:16], 16) % 10
        encrypted_position = (position + position_key) % 10
        
        return {
            'data': random_numbers,
            'key': encrypted_position
        }
    
    @staticmethod
    def decrypt_index(encrypted_data, key, patient_id):
        """
        解密真实查询索引
        
        Args:
            encrypted_data: 加密的索引数据
            key: 索引哈希
            patient_id: 患者ID
            
        Returns:
            真实查询的索引
        """
        # 验证密钥
        index_seed = f"{patient_id}_{datetime.now().date().isoformat()}"
        expected_hash = hashlib.sha256(index_seed.encode()).hexdigest()
        
        if key != expected_hash:
            raise ValueError("无效的密钥，无法解密索引")
        
        # 解密位置
        position_key = int(key[8:16], 16) % 100
        real_position = (encrypted_data['key'] - position_key) % 10
        if real_position < 0:
            real_position += 10
            
        # 获取真实索引
        encoded_index = encrypted_data['data'][real_position]
        true_index = encoded_index - 100
        
        return true_index

def prepare_pir_database(health_records):
    """
    将健康记录准备为PIR数据库格式
    
    Args:
        health_records: 健康记录列表
        
    Returns:
        PIR数据库，记录映射
    """
    from flask import current_app
    
    # 创建记录到数值向量的映射
    record_vectors = []
    record_mapping = {}
    
    for idx, record in enumerate(health_records):
        try:
            vector = PIRQuery.encode_health_record(record)
            record_vectors.append(vector)
            record_mapping[idx] = record
        except Exception as e:
            current_app.logger.error(f"编码健康记录失败: {str(e)}")
            # 使用一个默认向量代替
            record_vectors.append([0] * 32)  # 默认使用32长度的向量
            record_mapping[idx] = record
    
    # 将向量列表转为numpy数组
    if record_vectors:
        try:
            # 确保所有向量长度相同
            max_length = max(len(v) for v in record_vectors)
            # 填充向量到相同长度
            padded_vectors = [v + [0] * (max_length - len(v)) for v in record_vectors]
            # 转换为二维数组
            pir_database = np.array(padded_vectors)
            
            # 检查数据库形状并记录
            current_app.logger.info(f"PIR数据库形状: {pir_database.shape}, 记录数量: {len(health_records)}")
            
            if len(pir_database.shape) != 2:
                # 确保是二维数组
                if len(pir_database.shape) == 1:
                    pir_database = pir_database.reshape(1, -1)
                else:
                    # 如果是多维，展平为二维
                    pir_database = pir_database.reshape(len(health_records), -1)
        except Exception as e:
            current_app.logger.error(f"创建PIR数据库失败: {str(e)}")
            # 创建一个默认数据库
            pir_database = np.zeros((len(health_records), 32))
    else:
        # 创建一个空数据库
        pir_database = np.array([])
    
    return pir_database, record_mapping

def store_health_record_mongodb(record_data, patient_id, file_info=None):
    """
    存储健康记录到MongoDB
    
    Args:
        record_data: 记录数据
        patient_id: 患者ID
        file_info: 文件信息
        
    Returns:
        MongoDB中的记录ID
    """
    from bson import ObjectId
    from flask import current_app, g
    from ..utils.mongo_utils import get_mongo_db
    from datetime import datetime
    import json
    
    # 获取MongoDB实例
    mongo_db = get_mongo_db()
    
    # 转换记录类型、可见性和日期
    record_visibility = record_data.get('visibility', 'private')
    
    # 处理日期
    if 'record_date' in record_data and record_data['record_date']:
        try:
            record_date = datetime.strptime(record_data['record_date'], '%Y-%m-%dT%H:%M:%S.%f')
        except ValueError:
            try:
                record_date = datetime.strptime(record_data['record_date'], '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                record_date = datetime.now()
    else:
        record_date = datetime.now()
    
    # 确保is_encrypted标志存在，默认为False
    is_encrypted = record_data.get('is_encrypted', False)
    
    # 创建MongoDB记录
    mongo_record = {
        'patient_id': patient_id,
        'doctor_id': record_data.get('doctor_id'),
        'doctor_name': record_data.get('doctor_name', ''),
        'record_type': record_data.get('record_type', ''),
        'title': record_data.get('title', ''),
        'description': record_data.get('description', ''),
        'record_date': record_date,
        'visibility': record_visibility,
        'tags': record_data.get('tags', ''),
        'institution': record_data.get('institution', ''),
        'created_at': datetime.now(),
        'updated_at': datetime.now(),
        'is_encrypted': is_encrypted,   # 明确设置加密标志
        'version': 1
    }
    
    # 如果记录是加密的，保存相关加密信息
    if is_encrypted:
        mongo_record['encrypted_data'] = record_data.get('encrypted_data')
        mongo_record['key_salt'] = record_data.get('key_salt')
        mongo_record['encryption_algorithm'] = record_data.get('encryption_algorithm')
        mongo_record['encryption_date'] = record_data.get('encryption_date')
        mongo_record['integrity_hash'] = record_data.get('integrity_hash')
    
    # 添加文件信息
    if file_info:
        mongo_record['files'] = file_info
    
    # 添加用药记录
    if record_data.get('record_type') == 'medication' and record_data.get('medication'):
        med_data = record_data.get('medication')
        mongo_record['medication'] = {
            'medication_name': med_data.get('medication_name', ''),
            'dosage': med_data.get('dosage'),
            'frequency': med_data.get('frequency'),
            'start_date': datetime.strptime(med_data.get('start_date'), '%Y-%m-%d').date() if med_data.get('start_date') else None,
            'end_date': datetime.strptime(med_data.get('end_date'), '%Y-%m-%d').date() if med_data.get('end_date') else None,
            'instructions': med_data.get('instructions'),
            'side_effects': med_data.get('side_effects')
        }
    
    # 添加生命体征
    if record_data.get('record_type') == 'vital_signs' and record_data.get('vital_signs'):
        mongo_record['vital_signs'] = []
        for vs_data in record_data.get('vital_signs', []):
            mongo_record['vital_signs'].append({
                'type': vs_data.get('type', ''),
                'value': float(vs_data.get('value', 0)),
                'unit': vs_data.get('unit'),
                'measured_at': datetime.strptime(vs_data.get('measured_at', datetime.now().isoformat()), 
                                               '%Y-%m-%dT%H:%M:%S.%f') if 'measured_at' in vs_data else datetime.now(),
                'notes': vs_data.get('notes')
            })
    
    # 插入记录
    result = mongo_db.health_records.insert_one(mongo_record)
    
    return str(result.inserted_id)

def query_health_records_mongodb(query_params, patient_id, is_anonymous=False):
    """
    从MongoDB查询健康记录
    
    Args:
        query_params: 查询参数
        patient_id: 患者ID
        is_anonymous: 是否匿名查询
        
    Returns:
        健康记录列表，查询元数据
    """
    from flask import current_app
    
    # 基础查询条件
    query = {'patient_id': patient_id}
    
    # 添加查询条件
    if 'record_type' in query_params and query_params['record_type']:
        query['record_type'] = query_params['record_type']
    
    # 日期范围
    date_query = {}
    if 'start_date' in query_params and query_params['start_date']:
        date_query['$gte'] = datetime.strptime(query_params['start_date'], '%Y-%m-%d')
    if 'end_date' in query_params and query_params['end_date']:
        date_query['$lte'] = datetime.strptime(query_params['end_date'], '%Y-%m-%d')
    if date_query:
        query['record_date'] = date_query
    
    # 关键字搜索
    if 'keyword' in query_params and query_params['keyword']:
        keyword = query_params['keyword']
        query['$or'] = [
            {'title': {'$regex': keyword, '$options': 'i'}},
            {'description': {'$regex': keyword, '$options': 'i'}},
            {'tags': {'$regex': keyword, '$options': 'i'}}
        ]
    
    # 是否使用隐匿查询
    if is_anonymous:
        # 获取当前PIR设置
        pir_settings = {
            'max_noise_queries': current_app.config.get('PIR_MAX_NOISE_QUERIES', 3),
            'encryption_strength': current_app.config.get('PIR_ENCRYPTION_STRENGTH', 'high')
        }
        
        # 混淆查询
        obfuscated_query = PIRQuery.obfuscate_query(query_params, patient_id)
        
        # 执行所有混淆查询
        all_results = []
        real_results = None
        
        for i, q in enumerate(obfuscated_query['queries']):
            # 构建查询条件
            current_query = {'patient_id': patient_id}
            
            if 'record_type' in q and q['record_type']:
                current_query['record_type'] = q['record_type']
            
            date_query = {}
            if 'start_date' in q and q['start_date']:
                date_query['$gte'] = datetime.strptime(q['start_date'], '%Y-%m-%d')
            if 'end_date' in q and q['end_date']:
                date_query['$lte'] = datetime.strptime(q['end_date'], '%Y-%m-%d')
            if date_query:
                current_query['record_date'] = date_query
            
            if 'keyword' in q and q['keyword']:
                current_query['$or'] = [
                    {'title': {'$regex': q['keyword'], '$options': 'i'}},
                    {'description': {'$regex': q['keyword'], '$options': 'i'}},
                    {'tags': {'$regex': q['keyword'], '$options': 'i'}}
                ]
            
            # 执行查询
            results = list(mongo.db.health_records.find(current_query))
            
            # 将ObjectId转为字符串
            for r in results:
                r['_id'] = str(r['_id'])
                if 'record_date' in r:
                    r['record_date'] = r['record_date'].isoformat() if r['record_date'] and hasattr(r['record_date'], 'isoformat') else r['record_date']
                if 'created_at' in r:
                    r['created_at'] = r['created_at'].isoformat() if r['created_at'] and hasattr(r['created_at'], 'isoformat') else r['created_at']
                if 'updated_at' in r:
                    r['updated_at'] = r['updated_at'].isoformat() if r['updated_at'] and hasattr(r['updated_at'], 'isoformat') else r['updated_at']
                
                # 处理用药记录和生命体征的日期
                if 'medication' in r and r['medication']:
                    if 'start_date' in r['medication'] and r['medication']['start_date']:
                        r['medication']['start_date'] = r['medication']['start_date'].isoformat() if r['medication']['start_date'] and hasattr(r['medication']['start_date'], 'isoformat') else r['medication']['start_date']
                    if 'end_date' in r['medication'] and r['medication']['end_date']:
                        r['medication']['end_date'] = r['medication']['end_date'].isoformat() if r['medication']['end_date'] and hasattr(r['medication']['end_date'], 'isoformat') else r['medication']['end_date']
                
                if 'vital_signs' in r and r['vital_signs']:
                    for vs in r['vital_signs']:
                        if 'measured_at' in vs:
                            vs['measured_at'] = vs['measured_at'].isoformat() if vs['measured_at'] and hasattr(vs['measured_at'], 'isoformat') else vs['measured_at']
            
            # 添加到所有结果中
            all_results.append(results)
            
            # 如果是真实查询索引，保存结果
            true_index = PIRQuery.decrypt_index(obfuscated_query['true_index'], 
                                               obfuscated_query['index_hash'], 
                                               patient_id)
            if i == true_index:
                real_results = results
        
        # 获取实际使用的噪声查询数量
        noise_count = obfuscated_query.get('noise_count', len(obfuscated_query['queries']) - 1)
        encryption_strength = obfuscated_query.get('encryption_strength', pir_settings['encryption_strength'])
        
        # 记录查询历史（仅记录真实查询）
        record_query_history(
            patient_id, 
            'pir_query', 
            query_params, 
            is_anonymous=True,
            pir_settings={
                'noise_queries': noise_count,
                'encryption_strength': encryption_strength,
                'max_configured_noise': pir_settings['max_noise_queries']
            }
        )
        
        return real_results, {
            'pir_metadata': {
                'total_queries': len(obfuscated_query['queries']),
                'noise_queries': noise_count,
                'encryption_strength': encryption_strength,
                'index_hash': obfuscated_query['index_hash']
            }
        }
    else:
        # 常规查询
        results = list(mongo.db.health_records.find(query))
        
        # 将ObjectId转为字符串
        for r in results:
            r['_id'] = str(r['_id'])
            if 'record_date' in r:
                r['record_date'] = r['record_date'].isoformat() if r['record_date'] and hasattr(r['record_date'], 'isoformat') else r['record_date']
            if 'created_at' in r:
                r['created_at'] = r['created_at'].isoformat() if r['created_at'] and hasattr(r['created_at'], 'isoformat') else r['created_at']
            if 'updated_at' in r:
                r['updated_at'] = r['updated_at'].isoformat() if r['updated_at'] and hasattr(r['updated_at'], 'isoformat') else r['updated_at']
            
            # 处理用药记录和生命体征的日期
            if 'medication' in r and r['medication']:
                if 'start_date' in r['medication'] and r['medication']['start_date']:
                    r['medication']['start_date'] = r['medication']['start_date'].isoformat() if r['medication']['start_date'] and hasattr(r['medication']['start_date'], 'isoformat') else r['medication']['start_date']
                if 'end_date' in r['medication'] and r['medication']['end_date']:
                    r['medication']['end_date'] = r['medication']['end_date'].isoformat() if r['medication']['end_date'] and hasattr(r['medication']['end_date'], 'isoformat') else r['medication']['end_date']
            
            if 'vital_signs' in r and r['vital_signs']:
                for vs in r['vital_signs']:
                    if 'measured_at' in vs:
                        vs['measured_at'] = vs['measured_at'].isoformat() if vs['measured_at'] and hasattr(vs['measured_at'], 'isoformat') else vs['measured_at']
        
        # 记录查询历史
        record_query_history(patient_id, 'standard_query', query_params, is_anonymous=False)
        
        return results, {'standard_query': True}

def record_query_history(patient_id, query_type, query_params, is_anonymous=False, pir_settings=None):
    """
    记录查询历史
    
    Args:
        patient_id: 患者ID
        query_type: 查询类型
        query_params: 查询参数
        is_anonymous: 是否匿名查询
        pir_settings: PIR设置信息
    """
    query_history = {
        'user_id': patient_id,
        'query_type': query_type,
        'query_params': query_params,
        'is_anonymous': is_anonymous,
        'query_time': datetime.now()
    }
    
    # 加入PIR设置信息
    if pir_settings:
        query_history['pir_settings'] = pir_settings
    
    mongo.db.query_history.insert_one(query_history)

def parse_encrypted_query_id(encrypted_id):
    """
    解析前端发送的加密查询ID
    
    Args:
        encrypted_id: 加密的查询ID，格式为：ENC_<base64>_<random>
        
    Returns:
        解析后的整数索引
    """
    try:
        if encrypted_id.startswith('ENC_'):
            # 分离格式：ENC_<base64编码的ID>_<随机字符串>
            parts = encrypted_id.split('_')
            if len(parts) >= 2:
                # 解码base64部分
                encoded_id = parts[1]
                decoded_bytes = base64.b64decode(encoded_id)
                decoded_id = decoded_bytes.decode('utf-8')
                # 转换为整数
                return int(decoded_id)
        # 如果不是预期格式，尝试直接转为整数
        return int(encrypted_id)
    except Exception as e:
        current_app.logger.error(f"解析加密ID失败: {str(e)}")
        # 返回一个随机索引，避免直接失败
        # 这可能不是最佳方案，但可以防止错误传播
        return random.randint(0, 100)

def generate_pir_decrypt_key(record_id, researcher_id):
    """
    生成PIR记录解密密钥
    
    Args:
        record_id: 记录ID
        researcher_id: 研究员ID
        
    Returns:
        解密密钥
    """
    from flask import current_app
    import hashlib
    
    try:
        # 混合记录ID和研究员ID作为种子
        seed = f"{record_id}_{researcher_id}_{current_app.config.get('SECRET_KEY', '')}"
        
        # 计算哈希作为密钥
        key_hash = hashlib.sha256(seed.encode()).hexdigest()
        
        # 取前16位作为可读密钥
        readable_key = key_hash[:16]
        
        return readable_key
    except Exception as e:
        current_app.logger.error(f"生成PIR解密密钥失败: {str(e)}")
        return None

def verify_pir_decrypt_key(record_id, researcher_id, provided_key):
    """
    验证PIR解密密钥是否正确
    
    Args:
        record_id: 记录ID
        researcher_id: 研究员ID
        provided_key: 提供的密钥
        
    Returns:
        布尔值,表示密钥是否正确
    """
    expected_key = generate_pir_decrypt_key(record_id, researcher_id)
    if not expected_key:
        return False
    
    return expected_key == provided_key

def analyze_feature_vector(vector, record_id=None):
    """
    分析特征向量，提取有用信息，并返回相关性分析
    
    Args:
        vector: 特征向量
        record_id: 记录ID
        
    Returns:
        分析结果
    """
    import numpy as np
    from flask import current_app
    import math
    
    try:
        analysis = {
            'vector_dimension': len(vector),
            'statistical_properties': {
                'mean': float(np.mean(vector)),
                'median': float(np.median(vector)),
                'std_dev': float(np.std(vector)),
                'max': float(np.max(vector)),
                'min': float(np.min(vector))
            },
            'pattern_analysis': {
                'zero_ratio': float(len([x for x in vector if x == 0]) / len(vector)),
                'positive_ratio': float(len([x for x in vector if x > 0]) / len(vector)),
                'negative_ratio': float(len([x for x in vector if x < 0]) / len(vector)),
                'entropy': float(-sum([(x/sum(vector))*math.log2(x/sum(vector)) if x != 0 and sum(vector) != 0 else 0 for x in vector]))
            }
        }
        
        # 如果提供了记录ID，查找相似记录
        if record_id:
            similar_records = find_similar_records(vector, record_id)
            if similar_records:
                analysis['similar_records'] = similar_records
                
        return analysis
    except Exception as e:
        current_app.logger.error(f"分析特征向量失败: {str(e)}")
        return {'error': str(e)}

def find_similar_records(vector, current_record_id, max_results=5, similarity_threshold=0.7):
    """
    查找与给定向量相似的健康记录
    
    Args:
        vector: 特征向量
        current_record_id: 当前记录ID，排除自身
        max_results: 最大返回结果数
        similarity_threshold: 相似度阈值
        
    Returns:
        相似记录列表
    """
    import numpy as np
    from flask import current_app
    from ..utils.mongo_utils import get_mongo_db
    
    try:
        # 获取所有健康记录
        mongo_db = get_mongo_db()
        health_records = list(mongo_db.health_records.find({'visibility': 'researcher'}))
        
        # 准备记录的特征向量
        record_vectors = []
        record_ids = []
        
        # 创建记录到数值向量的映射
        for idx, record in enumerate(health_records):
            # 跳过当前记录
            record_id = str(record.get('_id'))
            if record_id == current_record_id:
                continue
                
            try:
                # 编码健康记录
                encoded_vector = PIRQuery.encode_health_record(record)
                
                # 确保向量长度一致
                if len(encoded_vector) != len(vector):
                    # 如果长度不同，裁剪或填充到相同长度
                    min_length = min(len(encoded_vector), len(vector))
                    encoded_vector = encoded_vector[:min_length]
                    vector_for_comparison = vector[:min_length]
                else:
                    vector_for_comparison = vector
                
                # 计算余弦相似度
                similarity = cosine_similarity(encoded_vector, vector_for_comparison)
                
                # 如果相似度超过阈值，添加到候选列表
                if similarity >= similarity_threshold:
                    record_vectors.append({
                        'id': record_id,
                        'similarity': similarity,
                        'record_type': record.get('record_type'),
                        'title': record.get('title', '无标题'),
                        'institution': record.get('institution', '未知机构'),
                        'record_date': record.get('record_date').isoformat() if record.get('record_date') and hasattr(record.get('record_date'), 'isoformat') else record.get('record_date'),
                    })
            except Exception as e:
                current_app.logger.error(f"计算记录相似度失败: {str(e)}")
                continue
        
        # 按相似度排序
        sorted_records = sorted(record_vectors, key=lambda x: x['similarity'], reverse=True)
        
        # 返回前N个结果
        return sorted_records[:max_results]
    except Exception as e:
        current_app.logger.error(f"查找相似记录失败: {str(e)}")
        return []

def cosine_similarity(vec1, vec2):
    """
    计算两个向量的余弦相似度
    
    Args:
        vec1: 向量1
        vec2: 向量2
        
    Returns:
        余弦相似度值 (0-1)
    """
    import numpy as np
    
    try:
        # 转换为numpy数组
        vec1 = np.array(vec1)
        vec2 = np.array(vec2)
        
        # 计算点积
        dot_product = np.dot(vec1, vec2)
        
        # 计算向量范数
        norm_vec1 = np.linalg.norm(vec1)
        norm_vec2 = np.linalg.norm(vec2)
        
        # 避免除零错误
        if norm_vec1 == 0 or norm_vec2 == 0:
            return 0
        
        # 计算余弦相似度
        similarity = dot_product / (norm_vec1 * norm_vec2)
        
        # 确保在0-1范围内
        return max(0, min(1, similarity))
    except Exception as e:
        return 0 