from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Import models here to make them available when importing db
from .user import User 