import os

from setuptools import setup

# Readme as long description
with open(os.path.join(os.path.dirname(__file__), "README.md")) as readme_file:
    long_description = readme_file.read()

setup(
    name="django-auditlog",
    use_scm_version={"version_scheme": "post-release"},
    setup_requires=["setuptools_scm"],
    include_package_data=True,
    packages=[
        "auditlog",
        "auditlog.migrations",
        "auditlog.management",
        "auditlog.management.commands",
        "auditlog.templatetags",
    ],
    url="https://github.com/django-commons/django-auditlog",
    project_urls={
        "Documentation": "https://django-auditlog.readthedocs.io",
        "Source": "https://github.com/django-commons/django-auditlog",
        "Tracker": "https://github.com/django-commons/django-auditlog/issues",
    },
    license="MIT",
    author="Jan-Jelle Kester",
    description="Audit log app for Django",
    long_description=long_description,
    long_description_content_type="text/markdown",
    python_requires=">=3.10",
    install_requires=["Django>=4.2", "python-dateutil>=2.7.0"],
    zip_safe=False,
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Framework :: Django",
        "Framework :: Django :: 4.2",
        "Framework :: Django :: 5.0",
        "Framework :: Django :: 5.1",
        "Framework :: Django :: 5.2",
        "Framework :: Django :: 6.0",
        "Framework :: Django :: 6.1",
        "License :: OSI Approved :: MIT License",
    ],
)
