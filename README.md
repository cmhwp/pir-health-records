# Flask Backend Project

A Flask backend API project with a modular structure.

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

## Getting Started

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

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

### Running the Application

To run the application in development mode:

```
python run.py
```

The server will start at http://localhost:5000

## API Endpoints

- `GET /`: Welcome message
- `GET /api/health`: Health check endpoint

## Adding Environment Variables

Create a `.env` file in the root directory with the following variables:

```
SECRET_KEY=your-secret-key
DEV_DATABASE_URL=your-dev-db-url
TEST_DATABASE_URL=your-test-db-url
DATABASE_URL=your-prod-db-url
``` 