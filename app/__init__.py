from flask import Flask
from flask_cors import CORS
from .config.config import config
from .models import db

def create_app(config_name="development"):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # Initialize extensions
    db.init_app(app)
    CORS(app)
    
    # Create database tables
    with app.app_context():
        db.create_all()
    
    # Register blueprints
    from .routers.main import main_bp
    app.register_blueprint(main_bp)
    
    return app 