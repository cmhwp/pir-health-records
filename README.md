# Flask Backend Project

A Flask backend API project with a modular structure and multiple database integrations.

## Project Structure

```
.
├── app/
│   ├── config/         # Configuration settings
│   ├── models/         # Database models
│   ├── routers/        # API routes
│   └── utils/          # Utility functions
├── requirements.txt    # Project dependencies
└── run.py             # Application entry point
```

## Features

- Flask RESTful API framework
- SQLAlchemy ORM with MySQL support
- MongoDB integration through Flask-PyMongo
- Redis for caching and session management
- Environment-based configuration
- CORS support

## Getting Started

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- MySQL (Optional)
- MongoDB (Optional)
- Redis (Optional)

### Installation

1. Clone the repository:
   ```
   git clone <repository-url>
   cd <project-folder>
   ```

2. Create a virtual environment:
   ```
   python -m venv venv
   ```

3. Activate the virtual environment:
   - On Windows:
     ```
     venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```
     source venv/bin/activate
     ```

4. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

### Database Setup

#### MySQL

1. Install MySQL if not already installed
2. Create a database for the project
3. Configure connection string in `.env` file

#### MongoDB

1. Install MongoDB if not already installed
2. MongoDB will automatically create the database on first use
3. Configure connection string in `.env` file

#### Redis

1. Install Redis if not already installed
2. Configure connection string in `.env` file

### Running the Application

To run the application in development mode:

```
python run.py
```

The server will start at http://localhost:5000

## API Endpoints

### Main Endpoints

- `GET /`: Welcome message
- `GET /api/health`: Health check endpoint

### Database Example Endpoints

#### MySQL (SQLAlchemy)

- `GET /api/db/mysql/users`: Get all users
- `POST /api/db/mysql/users`: Create a new user
- `GET /api/db/mysql/products`: Get all products
- `POST /api/db/mysql/products`: Create a new product

#### MongoDB

- `GET /api/db/mongodb/logs`: Get logs with optional filtering
- `POST /api/db/mongodb/logs`: Create a new log entry

#### Redis

- `GET /api/db/redis/cache?key=<key>`: Get cached value
- `POST /api/db/redis/cache`: Set a value in cache
- `DELETE /api/db/redis/cache?key=<key>`: Delete a value from cache

## Adding Environment Variables

Create a `.env` file in the root directory with the following variables:

```
# General
SECRET_KEY=your-secret-key

# SQLite Configurations (Default fallback)
DEV_DATABASE_URL=sqlite:///dev.db
TEST_DATABASE_URL=sqlite:///test.db
DATABASE_URL=sqlite:///prod.db

# MySQL Configurations
DEV_MYSQL_URL=mysql://root:password@localhost/flask_dev
TEST_MYSQL_URL=mysql://root:password@localhost/flask_test
MYSQL_URL=mysql://user:password@localhost/flask_prod

# MongoDB Configurations
DEV_MONGO_URI=mongodb://localhost:27017/flask_dev
TEST_MONGO_URI=mongodb://localhost:27017/flask_test
MONGO_URI=mongodb://localhost:27017/flask_prod

# Redis Configuration
REDIS_URL=redis://localhost:6379/0
``` 