import os
from dotenv import load_dotenv
from app import create_app

# 加载环境变量
load_dotenv()

# 获取配置模式，默认为开发模式
config_name = os.getenv('FLASK_ENV', 'development')

# 创建应用实例
app = create_app(config_name)

if __name__ == "__main__":
    # 从环境变量获取主机地址，默认为0.0.0.0（所有接口）
    host = os.getenv('HOST', '0.0.0.0')
    # 从环境变量获取端口，默认为5000
    port = int(os.getenv('PORT', 5000))
    # 从环境变量获取是否开启调试模式，默认跟随配置名
    debug = os.getenv('FLASK_DEBUG', config_name == 'development').lower() in ('true', '1', 't')
    
    app.run(host=host, port=port, debug=debug)
