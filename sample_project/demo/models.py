from django.conf import settings
from django.db import models


class Category(models.Model):
    """Blog post category model for organizing content."""

    name = models.CharField(max_length=50, help_text="Category name")

    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Tag(models.Model):
    """Tag model for labeling posts with keywords."""

    name = models.CharField(max_length=50, help_text="Tag name")

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Post(models.Model):
    """Blog post model demonstrating auditlog tracking with various field types."""

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Post author",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Post category",
    )
    tags = models.ManyToManyField(Tag, blank=True, help_text="Post tags")
    title = models.CharField(max_length=200, help_text="Post title")
    content = models.TextField(help_text="Post content")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Post"
        verbose_name_plural = "Posts"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
