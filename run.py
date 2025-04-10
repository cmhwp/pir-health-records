import os
from dotenv import load_dotenv
from app import create_app

# 从.env文件加载环境变量
load_dotenv()

# 使用指定的配置创建Flask应用
config_name = os.environ.get('FLASK_CONFIG', 'development')
app = create_app(config_name)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', 5000))) 