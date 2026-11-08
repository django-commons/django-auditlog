Installation
============

Installing Auditlog is simple and straightforward. First of all, you need a copy of Auditlog on your system. The easiest
way to do this is by using the Python Package Index (PyPI). Simply run the following command:

``pip install django-auditlog``

Instead of installing Auditlog via PyPI, you can also clone the Git repository or download the source code via GitHub.
The repository can be found at https://github.com/django-commons/django-auditlog/.

**Requirements**

- Python 3.10 or higher
- Django 4.2, 5.0, 5.1, 5.2, 6.0, and 6.1

Auditlog is currently tested with Python 3.10+ and Django 4.2, 5.0, 5.1, 5.2, 6.0, and 6.1. The latest test report can be found
at https://github.com/django-commons/django-auditlog/actions.

Adding Auditlog to your Django application
------------------------------------------

To use Auditlog in your application, just add ``'auditlog'`` to your project's ``INSTALLED_APPS`` setting and run
``manage.py migrate`` to create/upgrade the necessary database structure.

If you want Auditlog to automatically set the actor for log entries you also need to enable the middleware by adding
``'auditlog.middleware.AuditlogMiddleware'`` to your ``MIDDLEWARE`` setting. Please check :doc:`usage` for more
information.
