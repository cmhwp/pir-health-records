import random
import numpy as np
import time
import json
from datetime import datetime, timedelta
from flask import current_app
import hashlib
import base64
from bson import ObjectId

# 添加自定义JSON编码器
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, ObjectId):
            return str(obj)
        return super(DateTimeEncoder, self).default(obj)

from ..models import db
from ..models.health_records import HealthRecord, RecordVisibility
from ..utils.mongo_utils import get_mongo_db
from ..utils.log_utils import log_research, log_pir

# PIR 协议类型
class PIRProtocolType:
    BASIC = "basic"                # 基本PIR协议
    HOMOMORPHIC = "homomorphic"    # 同态加密PIR
    HYBRID = "hybrid"              # 混合PIR协议
    ONION = "onion"                # 洋葱路由PIR

# PIR性能指标
class PIRPerformanceMetric:
    QUERY_TIME = "query_time"          # 查询时间
    ACCURACY = "accuracy"              # 准确率
    COMMUNICATION_COST = "comm_cost"   # 通信开销
    SERVER_LOAD = "server_load"        # 服务器负载
    CLIENT_LOAD = "client_load"        # 客户端负载
    PRIVACY_LEVEL = "privacy_level"    # 隐私保护级别

def generate_mock_health_data(count=100, structured=True, record_types=None):
    """
    生成模拟健康数据，用于PIR实验
    
    Args:
        count: 生成的记录数量
        structured: 是否使用结构化数据（True）或随机数据（False）
        record_types: 指定生成的记录类型列表，为None则使用所有类型
        
    Returns:
        生成的模拟健康数据列表
    """
    mock_data = []
    
    # 如果未指定记录类型，使用一些常见类型
    if not record_types:
        record_types = [
            "GENERAL_CHECKUP",
            "LAB_RESULT",
            "IMAGING",
            "PRESCRIPTION",
            "SURGICAL",
            "DIAGNOSIS",
            "VITAL_SIGN",
            "VACCINATION",
            "ALLERGY",
            "MEDICAL_HISTORY",
            "FOLLOW_UP"
        ]
    
    # 模拟患者和医生ID范围
    patient_ids = list(range(1001, 1050))
    doctor_ids = list(range(2001, 2020))
    
    # 模拟疾病列表
    diseases = ["高血压", "糖尿病", "冠心病", "肺炎", "胃炎", "头痛", "感冒", "过敏", "哮喘", "抑郁症", "肺结核", "脑卒中"]
    
    # 模拟药物列表
    medications = ["阿司匹林", "布洛芬", "氯雷他定", "二甲双胍", "苯磺酸氨氯地平", "辛伐他汀", "甲状腺素钠", "氨苯砜", "呋塞米", "氯沙坦钾"]
    
    # 模拟疫苗列表
    vaccines = ["新冠疫苗", "流感疫苗", "乙肝疫苗", "麻疹疫苗", "肺炎疫苗", "百白破疫苗", "脊髓灰质炎疫苗", "水痘疫苗"]
    
    # 模拟手术类型
    surgery_types = ["阑尾切除术", "胆囊切除术", "心脏搭桥术", "髋关节置换术", "白内障手术", "剖腹产", "扁桃体切除术", "胃旁路术", "腹腔镜手术"]
    
    # 模拟过敏源
    allergies = ["花粉", "尘螨", "海鲜", "花生", "牛奶", "青霉素", "小麦", "鸡蛋", "大豆", "乳胶"]
    
    # 模拟病史
    medical_histories = ["哮喘病史", "心脏病史", "高血压病史", "糖尿病病史", "癫痫病史", "肿瘤病史", "抑郁症病史", "骨折病史", "家庭遗传病史"]
    
    # 模拟随访类型
    follow_up_types = ["术后随访", "药物治疗随访", "康复治疗随访", "疾病管理随访", "精神健康随访", "慢性病随访", "血糖控制随访"]
        
    # 生成记录的起始时间和结束时间
    start_date = datetime(2020, 1, 1)
    end_date = datetime.now()
    date_range = (end_date - start_date).days
    
    for i in range(count):
        if structured:
            # 生成结构化的模拟健康数据
            record_type = random.choice(record_types)
            patient_id = random.choice(patient_ids)
            doctor_id = random.choice(doctor_ids)
            
            # 模拟记录日期
            random_days = random.randint(0, date_range)
            record_date = start_date + timedelta(days=random_days)
            
            # 基础记录数据
            record = {
                "_id": ObjectId(),
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "record_type": record_type,
                "title": f"{random.choice(diseases)}检查记录",
                "description": f"患者{patient_id}的{record_type}记录，记录日期为{record_date}",
                "record_date": record_date,
                "created_at": datetime.now(),
                "visibility": "researcher",  # 设为研究员可见
                "is_encrypted": random.choice([True, False]),
                "pir_protected": True  # 默认设置为PIR保护
            }
            
            # 根据记录类型添加特定字段
            if record_type == "LAB_RESULT":
                record.update({
                    "blood_pressure": f"{random.randint(90, 140)}/{random.randint(60, 90)}",
                    "heart_rate": random.randint(60, 100),
                    "blood_sugar": round(random.uniform(3.9, 10.0), 1),
                    "cholesterol": round(random.uniform(2.8, 6.5), 1)
                })
            elif record_type == "PRESCRIPTION":
                record.update({
                    "medications": random.sample(medications, random.randint(1, 3)),
                    "dosage": [f"{random.randint(1, 3)}次/天" for _ in range(random.randint(1, 3))],
                    "duration": f"{random.randint(1, 14)}天"
                })
            elif record_type == "DIAGNOSIS":
                record.update({
                    "diagnosis": random.choice(diseases),
                    "severity": random.choice(["轻度", "中度", "重度"]),
                    "notes": f"患者表现出{random.choice(diseases)}的典型症状"
                })  
            elif record_type == "VITAL_SIGN":
                record.update({
                    "temperature": round(random.uniform(36.5, 37.5), 1),
                    "blood_pressure": f"{random.randint(90, 140)}/{random.randint(60, 90)}",
                    "heart_rate": random.randint(60, 100)
                })
            elif record_type == "VACCINATION":
                record.update({
                    "vaccine_type": random.choice(vaccines),
                    "date": record_date,
                    "notes": f"接种了{random.choice(vaccines)}疫苗"
                })      
            elif record_type == "SURGICAL":
                record.update({
                    "surgery_type": random.choice(surgery_types),
                    "notes": f"进行了{random.choice(surgery_types)}手术"
                })
            elif record_type == "ALLERGY":
                record.update({
                    "allergy_type": random.choice(allergies),
                    "notes": f"对{random.choice(allergies)}过敏"
                })      
            elif record_type == "MEDICAL_HISTORY":
                record.update({
                    "medical_history": random.choice(medical_histories),
                    "notes": f"有{random.choice(medical_histories)}病史"
                })
            elif record_type == "FOLLOW_UP":
                record.update({
                    "follow_up_date": record_date,
                    "notes": f"进行了{random.choice(follow_up_types)}随访"
                })

            # 转换datetime和ObjectId为可序列化格式
            serializable_record = json.loads(json.dumps(record, cls=DateTimeEncoder))
            serializable_record["_id"] = ObjectId(serializable_record["_id"])
            mock_data.append(serializable_record)
        else:
            # 生成随机数据(仅用于PIR算法测试，不包含有意义的医疗信息)
            random_data = np.random.rand(50)  # 生成50维随机向量
            mock_data.append({
                "_id": ObjectId(),
                "data_vector": random_data.tolist(),
                "record_type": random.choice(record_types),
                "pir_protected": True
            })
    
    return mock_data

def configure_pir_protocol(protocol_type, params=None):
    """
    配置PIR协议参数
    
    Args:
        protocol_type: PIR协议类型
        params: 协议特定参数
        
    Returns:
        配置好的PIR协议参数
    """
    default_params = {
        PIRProtocolType.BASIC: {
            "noise_level": 0.0,
            "query_expansion": 1,
            "database_padding": 0
        },
        PIRProtocolType.HOMOMORPHIC: {
            "encryption_bits": 1024,
            "noise_level": 0.1,
            "polynomial_degree": 4096
        },
        PIRProtocolType.HYBRID: {
            "encryption_bits": 2048,
            "noise_level": 0.05,
            "compression_ratio": 0.8,
            "database_partitions": 4
        },
        PIRProtocolType.ONION: {
            "layers": 3,
            "nodes_per_layer": 5,
            "timeout_ms": 500
        }
    }
    
    # 如果未提供协议参数，使用默认值
    if not params:
        params = {}
    
    # 合并默认参数和提供的参数
    if protocol_type in default_params:
        config = default_params[protocol_type].copy()
        config.update(params)
    else:
        # 如果未知协议类型，使用基本PIR协议
        config = default_params[PIRProtocolType.BASIC].copy()
        config.update(params)
    
    # 添加协议类型到配置中
    config["protocol_type"] = protocol_type
    
    return config

def execute_pir_query_experiment(data, target_indices, protocol_config):
    """
    执行PIR查询实验
    
    Args:
        data: 用于查询的数据
        target_indices: 要查询的目标索引列表
        protocol_config: PIR协议配置
        
    Returns:
        查询结果和性能指标
    """
    from ..utils.pir_utils import PIRQuery
    
    results = []
    metrics = {
        PIRPerformanceMetric.QUERY_TIME: [],
        PIRPerformanceMetric.ACCURACY: [],
        PIRPerformanceMetric.COMMUNICATION_COST: [],
        PIRPerformanceMetric.SERVER_LOAD: [],
        PIRPerformanceMetric.CLIENT_LOAD: [],
        PIRPerformanceMetric.PRIVACY_LEVEL: []
    }
    
    # 将数据转换为矩阵形式
    data_matrix = []
    for item in data:
        # 检查是否有data_vector字段
        if "data_vector" in item:
            data_matrix.append(item["data_vector"])
        else:
            # 如果没有，将对象转换为向量
            vector = PIRQuery.encode_health_record(item)
            data_matrix.append(vector)
    
    # 转换为numpy数组
    data_matrix = np.array(data_matrix)
    
    # 获取数据维度信息
    num_records = len(data_matrix)
    vector_size = data_matrix.shape[1] if len(data_matrix.shape) > 1 else 1
    
    # 按协议类型设置加密密钥大小（单位：比特）
    key_sizes = {
        PIRProtocolType.BASIC: 128,
        PIRProtocolType.HOMOMORPHIC: protocol_config.get("encryption_bits", 2048),
        PIRProtocolType.HYBRID: protocol_config.get("encryption_bits", 1024),
        PIRProtocolType.ONION: 256
    }
    
    # 根据不同的协议类型执行查询
    protocol_type = protocol_config.get("protocol_type", PIRProtocolType.BASIC)
    
    for target_idx in target_indices:
        if target_idx >= num_records:
            current_app.logger.warning(f"目标索引{target_idx}超出数据范围{num_records}，将跳过")
            continue
            
        start_time = time.time()
        result = None
        comm_cost = 0
        expected_result = data_matrix[target_idx] if target_idx < len(data_matrix) else None
        accuracy = 1.0  # 默认准确率
        
        try:
            # 基本PIR协议
            if protocol_type == PIRProtocolType.BASIC:
                # 创建查询向量
                query_vector = PIRQuery.create_query_vector(num_records, target_idx)
                
                # 添加可配置的噪声
                noise_level = protocol_config.get("noise_level", 0.0)
                if noise_level > 0:
                    noise = np.random.normal(0, noise_level, query_vector.shape)
                    query_vector = query_vector + noise
                    query_vector = np.clip(query_vector, 0, 1)
                
                # 执行查询
                result = PIRQuery.process_query(data_matrix, query_vector)
                
                # 估算通信成本
                comm_cost = (num_records + vector_size) * 4  # 查询向量大小 + 返回向量大小（字节）
                
            # 同态加密PIR - 基于Paillier同态加密
            elif protocol_type == PIRProtocolType.HOMOMORPHIC:
                # 模拟同态加密PIR
                # 在实际实现中应使用Paillier或其他同态加密库
                
                # 创建查询向量
                query_vector = PIRQuery.create_query_vector(num_records, target_idx)
                
                # 模拟加密开销 - 同态加密通常比较耗时
                key_bit_size = key_sizes[PIRProtocolType.HOMOMORPHIC]
                encryption_time = (key_bit_size / 1024) * 0.05  # 模拟密钥大小对加密时间的影响
                time.sleep(encryption_time)  # 模拟加密耗时
                
                # 执行同态加密下的查询
                result = PIRQuery.process_query(data_matrix, query_vector)
                
                # 模拟解密开销
                decryption_time = (key_bit_size / 1024) * 0.03
                time.sleep(decryption_time)  # 模拟解密耗时
                
                # 添加同态加密的随机误差
                result = result + np.random.normal(0, 0.001, result.shape)
                
                # 估算通信成本 - 同态加密通常有较大开销
                per_element_size = key_bit_size / 8  # 每个加密元素的字节数
                comm_cost = (num_records * per_element_size) + (vector_size * 4)
                
                # 计算准确率（同态加密有轻微误差）
                if expected_result is not None:
                    diff = np.abs(result - expected_result)
                    if np.max(diff) > 0.01:  # 误差阈值
                        accuracy = 0.99  # 仍然很高，但存在微小误差
                
            # 混合PIR协议 - 结合局部数据库和加密方法
            elif protocol_type == PIRProtocolType.HYBRID:
                # 划分数据库
                partitions = protocol_config.get("database_partitions", 4)
                partition_size = max(1, num_records // partitions)
                
                # 确定目标在哪个分区
                target_partition = target_idx // partition_size
                local_idx = target_idx % partition_size
                
                # 创建局部查询向量
                query_vector = PIRQuery.create_query_vector(partition_size, local_idx)
                
                # 混合PIR需要一次轻量级加密
                key_bit_size = key_sizes[PIRProtocolType.HYBRID]
                encryption_time = (key_bit_size / 1024) * 0.01
                time.sleep(encryption_time)  # 模拟加密耗时
                
                # 提取目标分区数据
                start_idx = target_partition * partition_size
                end_idx = min(start_idx + partition_size, len(data_matrix))
                partition_data = data_matrix[start_idx:end_idx]
                
                # 执行查询
                if len(partition_data) > 0:
                    result = PIRQuery.process_query(partition_data, query_vector)
                else:
                    # 处理边界情况
                    result = np.zeros(vector_size)
                    accuracy = 0.0
                
                # 估算通信成本 - 混合方案的成本较低
                per_element_size = key_bit_size / 16  # 轻量级加密
                comm_cost = (partition_size * per_element_size) + (vector_size * 4) + 8  # 额外8字节用于索引信息
                
            # 洋葱路由PIR - 多层加密路由
            elif protocol_type == PIRProtocolType.ONION:
                # 创建查询向量
                query_vector = PIRQuery.create_query_vector(num_records, target_idx)
                
                # 获取洋葱路由层数
                layers = protocol_config.get("layers", 3)
                
                # 模拟洋葱路由多层加密
                for layer in range(layers):
                    layer_encryption_time = 0.01 * (layer + 1)  # 模拟每层加密时间递增
                    time.sleep(layer_encryption_time)
                
                # 执行查询
                result = PIRQuery.process_query(data_matrix, query_vector)
                
                # 模拟多层解密
                for layer in range(layers):
                    layer_decryption_time = 0.008 * (layers - layer)  # 模拟每层解密时间递减
                    time.sleep(layer_decryption_time)
                
                # 估算通信成本 - 洋葱路由中间节点增加了开销
                nodes_per_layer = protocol_config.get("nodes_per_layer", 3)
                per_node_overhead = 32  # 每个节点的额外字节数
                comm_cost = (num_records * 4) + (vector_size * 4) + (layers * nodes_per_layer * per_node_overhead)
            
            else:
                # 未知协议类型，使用基本PIR
                query_vector = PIRQuery.create_query_vector(num_records, target_idx)
                result = PIRQuery.process_query(data_matrix, query_vector)
                comm_cost = (num_records + vector_size) * 4
        
        except Exception as e:
            current_app.logger.error(f"执行查询失败: {str(e)}")
            result = np.zeros(vector_size if vector_size > 1 else 1)
            accuracy = 0.0
        
        end_time = time.time()
        query_time = end_time - start_time
        
        # 记录性能指标
        metrics[PIRPerformanceMetric.QUERY_TIME].append(query_time)
        metrics[PIRPerformanceMetric.COMMUNICATION_COST].append(comm_cost)
        
        # 估算服务器负载 (基于查询时间和数据大小)
        if protocol_type == PIRProtocolType.BASIC:
            server_load = num_records * 0.00001 * query_time  # 基本PIR服务器计算最简单
        elif protocol_type == PIRProtocolType.HOMOMORPHIC:
            server_load = num_records * 0.0002 * query_time  # 同态加密需要更多服务器计算
        elif protocol_type == PIRProtocolType.HYBRID:
            server_load = (num_records / protocol_config.get("database_partitions", 4)) * 0.00015 * query_time
        elif protocol_type == PIRProtocolType.ONION:
            server_load = num_records * 0.0001 * query_time * protocol_config.get("layers", 3)
        else:
            server_load = num_records * 0.00005 * query_time
        
        metrics[PIRPerformanceMetric.SERVER_LOAD].append(server_load)
        
        # 估算客户端负载 (基于协议类型和数据大小)
        if protocol_type == PIRProtocolType.BASIC:
            client_load = 0.1 * query_time  # 基本协议客户端负载最低
        elif protocol_type == PIRProtocolType.HOMOMORPHIC:
            client_load = 0.6 * query_time  # 同态加密客户端需要更多计算
        elif protocol_type == PIRProtocolType.HYBRID:
            client_load = 0.3 * query_time  # 混合协议客户端负载适中
        elif protocol_type == PIRProtocolType.ONION:
            client_load = 0.4 * query_time * protocol_config.get("layers", 3)  # 洋葱路由客户端负载随层数增加
        else:
            client_load = 0.2 * query_time
            
        metrics[PIRPerformanceMetric.CLIENT_LOAD].append(client_load)
        
        # 估算隐私保护级别 (1-10分，基于协议类型和参数)
        if protocol_type == PIRProtocolType.BASIC:
            # 基本PIR隐私级别受噪声影响
            noise_level = protocol_config.get("noise_level", 0.0)
            privacy_level = 3 + min(2, noise_level * 10)
        elif protocol_type == PIRProtocolType.HOMOMORPHIC:
            # 同态加密PIR隐私级别受密钥大小影响
            key_bits = key_sizes[PIRProtocolType.HOMOMORPHIC]
            privacy_level = 7 + min(3, (key_bits - 1024) / 1024)
        elif protocol_type == PIRProtocolType.HYBRID:
            # 混合PIR隐私级别受分区数和密钥影响
            partitions = protocol_config.get("database_partitions", 4)
            privacy_level = 5 + min(2, (partitions - 2) / 4) + min(1, (key_sizes[PIRProtocolType.HYBRID] - 512) / 1024)
        elif protocol_type == PIRProtocolType.ONION:
            # 洋葱路由PIR隐私级别受层数影响
            layers = protocol_config.get("layers", 3)
            privacy_level = 7 + min(3, (layers - 1) / 2)
        else:
            privacy_level = 5
            
        privacy_level = min(10, max(1, privacy_level))  # 确保在1-10范围内
        metrics[PIRPerformanceMetric.PRIVACY_LEVEL].append(privacy_level)
        
        # 记录准确率
        metrics[PIRPerformanceMetric.ACCURACY].append(accuracy)
        
        # 添加到结果列表
        results.append({
            "target_index": target_idx,
            "result": result.tolist() if isinstance(result, np.ndarray) else result,
            "original_data": data[target_idx] if target_idx < len(data) else None,
            "query_time": query_time,
            "comm_cost": comm_cost,
            "accuracy": accuracy,
            "privacy_level": privacy_level
        })
    
    # 计算平均指标
    avg_metrics = {}
    for metric_name, values in metrics.items():
        if values:
            avg_metrics[metric_name] = sum(values) / len(values)
    
    # 记录实验日志
    log_pir(
        message=f"执行PIR实验查询，协议类型: {protocol_type}",
        details={
            "protocol_config": protocol_config,
            "data_size": len(data),
            "target_count": len(target_indices),
            "metrics": avg_metrics
        }
    )
    
    return {
        "results": results,
        "metrics": avg_metrics,
        "protocol": protocol_config
    }

def analyze_experiment_results(experiment_results, baseline_results=None):
    """
    分析实验结果，比较不同协议性能
    
    Args:
        experiment_results: 当前实验结果
        baseline_results: 基准实验结果(可选)
        
    Returns:
        分析报告
    """
    report = {
        "summary": {},
        "comparisons": {},
        "recommendations": []
    }
    
    # 提取当前实验指标
    current_metrics = experiment_results.get("metrics", {})
    current_protocol = experiment_results.get("protocol", {}).get("protocol_type", "unknown")
    
    # 汇总当前实验数据
    report["summary"] = {
        "protocol": current_protocol,
        "query_time": current_metrics.get(PIRPerformanceMetric.QUERY_TIME, 0),
        "accuracy": current_metrics.get(PIRPerformanceMetric.ACCURACY, 0),
        "comm_cost": current_metrics.get(PIRPerformanceMetric.COMMUNICATION_COST, 0),
        "privacy_level": current_metrics.get(PIRPerformanceMetric.PRIVACY_LEVEL, 0),
        "timestamp": datetime.now().isoformat()
    }
    
    # 如果有基准结果，进行比较分析
    if baseline_results:
        baseline_metrics = baseline_results.get("metrics", {})
        baseline_protocol = baseline_results.get("protocol", {}).get("protocol_type", "unknown")
        
        # 计算性能差异
        for metric in [PIRPerformanceMetric.QUERY_TIME, 
                       PIRPerformanceMetric.ACCURACY,
                       PIRPerformanceMetric.COMMUNICATION_COST,
                       PIRPerformanceMetric.SERVER_LOAD,
                       PIRPerformanceMetric.PRIVACY_LEVEL]:
            
            current_value = current_metrics.get(metric, 0)
            baseline_value = baseline_metrics.get(metric, 0)
            
            if baseline_value != 0:
                diff_percent = ((current_value - baseline_value) / baseline_value) * 100
            else:
                diff_percent = 0
                
            is_improvement = False
            # 对于查询时间和通信成本，较低的值更好
            if metric in [PIRPerformanceMetric.QUERY_TIME, 
                         PIRPerformanceMetric.COMMUNICATION_COST,
                         PIRPerformanceMetric.SERVER_LOAD,
                         PIRPerformanceMetric.CLIENT_LOAD]:
                is_improvement = diff_percent < 0
            else:  # 对于准确率和隐私级别，较高的值更好
                is_improvement = diff_percent > 0
                
            report["comparisons"][metric] = {
                "current": current_value,
                "baseline": baseline_value,
                "diff_percent": diff_percent,
                "is_improvement": is_improvement
            }
            
        # 生成建议
        if report["comparisons"].get(PIRPerformanceMetric.QUERY_TIME, {}).get("diff_percent", 0) > 20:
            report["recommendations"].append(
                f"当前协议 {current_protocol} 比 {baseline_protocol} 查询时间增加超过20%，"
                "考虑优化查询参数或使用更高效的协议"
            )
            
        if report["comparisons"].get(PIRPerformanceMetric.PRIVACY_LEVEL, {}).get("diff_percent", 0) < -10:
            report["recommendations"].append(
                f"当前协议 {current_protocol} 隐私保护级别比 {baseline_protocol} 低10%以上，"
                "考虑使用更强的隐私保护协议"
            )
            
        if report["comparisons"].get(PIRPerformanceMetric.COMMUNICATION_COST, {}).get("is_improvement", False):
            report["recommendations"].append(
                f"当前协议 {current_protocol} 通信成本比 {baseline_protocol} 更优，"
                "适合带宽有限的环境"
            )
    else:
        # 没有基准结果时，根据绝对指标生成建议
        query_time = current_metrics.get(PIRPerformanceMetric.QUERY_TIME, 0)
        if query_time > 1.0:  # 查询时间超过1秒
            report["recommendations"].append(
                "查询时间较长，考虑使用数据库分区或缓存优化"
            )
            
        privacy_level = current_metrics.get(PIRPerformanceMetric.PRIVACY_LEVEL, 0)
        if privacy_level < 5:  # 隐私保护级别较低
            report["recommendations"].append(
                "隐私保护级别较低，建议升级到同态加密或混合PIR协议"
            )
    
    return report 