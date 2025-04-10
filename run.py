import os
from dotenv import load_dotenv
from app import create_app

# 从.env文件加载环境变量
load_dotenv()

# 使用指定的配置创建Flask应用
config_name = os.environ.get('FLASK_CONFIG', 'development')
app = create_app(config_name)

if __name__ == '__main__':
    # 从环境变量获取主机和端口，如果未定义则使用默认值
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    
    app.run(host=host, port=port, debug=debug) 