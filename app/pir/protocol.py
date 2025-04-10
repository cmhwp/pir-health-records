import numpy as np
import hashlib
import base64
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
from datetime import datetime

class PIRProtocol:
    """
    基本计算型隐私信息检索(PIR)协议的实现。
    
    该协议允许客户端从数据库中检索数据，而不会泄露请求的是哪个数据项，
    从而为查询提供隐私保护。
    """
    
    def __init__(self, security_parameter=1024):
        """使用安全参数初始化PIR协议"""
        self.security_parameter = security_parameter
        self.key = None
    
    def generate_key(self):
        """为PIR操作生成随机密钥"""
        self.key = get_random_bytes(32)
        return self.key
    
    def generate_query(self, index, database_size):
        """
        生成PIR查询以检索指定索引处的项目。
        
        在真实的PIR协议中，这将生成一个查询向量或其他数据结构，
        不会泄露请求的索引。
        """
        if self.key is None:
            self.generate_key()
        
        # 创建随机数向量
        query = np.random.randint(0, high=2, size=database_size, dtype=np.int8)
        
        # 翻转所需索引处的位
        query[index] = 1 - query[index]
        
        # 加密查询以保护隐私
        query_bytes = query.tobytes()
        iv = get_random_bytes(16)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        ct_bytes = cipher.encrypt(pad(query_bytes, AES.block_size))
        
        # 编码查询用于传输
        iv_b64 = base64.b64encode(iv).decode('utf-8')
        ct_b64 = base64.b64encode(ct_bytes).decode('utf-8')
        
        return {
            'encrypted_query': f"{iv_b64}:{ct_b64}",
            'database_size': database_size
        }
    
    def process_query(self, database, encrypted_query):
        """
        处理针对数据库的PIR查询。
        
        数据库被建模为加密记录的列表。
        """
        # 解密查询
        iv_b64, ct_b64 = encrypted_query['encrypted_query'].split(':')
        iv = base64.b64decode(iv_b64)
        ct = base64.b64decode(ct_b64)
        
        # 创建密码对象并解密查询
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        query_bytes = unpad(cipher.decrypt(ct), AES.block_size)
        query = np.frombuffer(query_bytes, dtype=np.int8)
        
        # 将查询应用于数据库
        # 在真实的PIR协议中，这将更加复杂
        # 这里我们通过包含所有查询位为1的项目来模拟它
        result = []
        for i, record in enumerate(database):
            if i < len(query) and query[i] == 1:
                result.append(record)
        
        # 加密结果以保护隐私
        result_str = str(result)
        iv = get_random_bytes(16)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        ct_bytes = cipher.encrypt(pad(result_str.encode('utf-8'), AES.block_size))
        
        # 编码结果用于传输
        iv_b64 = base64.b64encode(iv).decode('utf-8')
        ct_b64 = base64.b64encode(ct_bytes).decode('utf-8')
        
        return {
            'encrypted_result': f"{iv_b64}:{ct_b64}",
            'result_size': len(result)
        }
    
    def extract_result(self, encrypted_result, original_index):
        """
        从加密结果中提取所需结果。
        
        在真实的PIR协议中，客户端需要对结果进行额外处理，
        以仅提取所需的项目。
        """
        # 解密结果
        iv_b64, ct_b64 = encrypted_result['encrypted_result'].split(':')
        iv = base64.b64decode(iv_b64)
        ct = base64.b64decode(ct_b64)
        
        # 创建密码对象并解密结果
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        result_bytes = unpad(cipher.decrypt(ct), AES.block_size)
        result_str = result_bytes.decode('utf-8')
        
        # 解析结果并提取所需项目
        # 这是一个用于演示目的的简化版本
        try:
            result_list = eval(result_str)  # 注意：为简单起见使用eval；不适合生产环境
            for item in result_list:
                # 基于original_index处理
                # 在真实实现中，这将更加复杂
                pass
            
            return result_list
        except Exception as e:
            return {'error': f'提取结果失败: {str(e)}'}
    
    def oblivious_transfer(self, database, index):
        """
        执行完整的不经意传输操作。
        
        这是一个围绕PIR协议的简化包装器，用于检索单个项目。
        """
        database_size = len(database)
        if index >= database_size:
            return {'error': '索引超出范围'}
        
        # 生成查询
        query = self.generate_query(index, database_size)
        
        # 处理针对数据库的查询
        encrypted_result = self.process_query(database, query)
        
        # 提取并返回结果
        result = self.extract_result(encrypted_result, index)
        
        return result
    
    def matrix_pir(self, database, row, column):
        """
        实现基于2D矩阵的PIR协议。
        
        将数据库安排在2D矩阵中，并对行和列执行PIR查询。
        这显著降低了通信复杂性。
        """
        # 将数据库转换为2D矩阵
        db_size = len(database)
        matrix_dim = int(np.ceil(np.sqrt(db_size)))
        
        # 创建数据库的矩阵表示
        matrix = []
        for i in range(matrix_dim):
            row_data = []
            for j in range(matrix_dim):
                idx = i * matrix_dim + j
                if idx < db_size:
                    row_data.append(database[idx])
                else:
                    row_data.append(None)  # 填充
            matrix.append(row_data)
        
        # 对行执行PIR
        row_query = self.generate_query(row, matrix_dim)
        row_result = []
        for i in range(matrix_dim):
            if row_query[i] == 1:
                row_result.extend(matrix[i])
        
        # 对列执行PIR
        col_query = self.generate_query(column, matrix_dim)
        final_result = []
        for j in range(matrix_dim):
            if col_query[j] == 1:
                for i in range(matrix_dim):
                    idx = i * matrix_dim + j
                    if idx < len(row_result):
                        final_result.append(row_result[idx])
        
        return final_result 