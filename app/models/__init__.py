from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# 在此导入模型，使其在导入db时可用
from .user import User
from .product import Product 