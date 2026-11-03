# Django Auditlog Sample Project

This is a minimal Django project for testing and demonstrating `django-auditlog` during development.
It includes comprehensive models with various field types and relationships (ForeignKey, ManyToMany) 
to showcase how Auditlog tracks changes across different scenarios.

## Project Structure

This sample project includes the following models:

- **Post**: Blog post model with title, content, author, category, and tags
- **Category**: Category model for organizing posts
- **Tag**: Tag model for labeling posts with keywords

All models are registered for audit logging through the `AUDITLOG_INCLUDE_TRACKING_MODELS` setting.

## Setup and Installation

### 1. Create Virtual Environment and Install Dependencies

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Linux/Mac:
source venv/bin/activate

# Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Database Setup

```bash
# Run migrations
python manage.py migrate

# Load sample data
python manage.py loaddata initial_data
```

### 3. Create Superuser

```bash
python manage.py createsuperuser
```

### 4. Start Development Server

```bash
python manage.py runserver
```

## Testing Auditlog Features

1. **Access Admin Interface**: Visit `http://127.0.0.1:8000/admin/` and log in with your superuser credentials

2. **Edit Models**: Try editing `Post`, `Category`, and `Tag` objects:
   - Create new posts
   - Edit existing post titles, content
   - Change categories
   - Add/remove tags
   - Change authors

3. **View Audit Logs**: 
   - Navigate to `auditlog > Log entries` in the Django admin
   - Filter by content type to see logs for specific models
   - View detailed change information for each modification
   - Use the date hierarchy to find logs from specific time periods

## File Structure

```
sample_project/
├── demo/                   # Demo application
│   ├── models.py           # Post, Category, Tag models
│   ├── admin.py            # Django admin configuration
│   ├── fixtures/           # Sample data
│   └── migrations/         # Database migrations
├── sample_project/         # Django project settings
│   └── settings.py         # Includes Auditlog configuration
└── manage.py
```

## Auditlog Configuration

The project demonstrates configuration via settings:

```python
# In settings.py
AUDITLOG_INCLUDE_TRACKING_MODELS = (
    {"model": "demo.Post", "m2m_fields": ["tags"]},
    "demo.Category",
    "demo.Tag",
)
```
