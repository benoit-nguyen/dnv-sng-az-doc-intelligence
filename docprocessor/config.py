"""
Configuration management for Document Processor.

Handles environment variables, settings validation, and Azure resource configuration.
"""

import os
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Azure Configuration
    azure_subscription_id: Optional[str] = Field(default=None, description="Azure subscription ID")
    azure_resource_group: str = Field(
        default="rg-doc-intelligence", description="Azure resource group name"
    )
    azure_location: str = Field(default="southeastasia", description="Azure region")

    # Document Intelligence
    document_intelligence_endpoint: str = Field(
        ..., description="Document Intelligence service endpoint"
    )
    document_intelligence_key: Optional[str] = Field(
        default=None, description="API key (optional if using managed identity)"
    )

    # Storage Account
    storage_account_name: Optional[str] = Field(default=None, description="Storage account name")
    storage_container_source: str = Field(
        default="source-documents", description="Source container name"
    )
    storage_container_results: str = Field(
        default="results", description="Results container name"
    )
    storage_container_translations: str = Field(
        default="translations", description="Translations container name"
    )
    storage_connection_string: Optional[str] = Field(
        default=None, description="Connection string (optional if using managed identity)"
    )

    # Key Vault
    key_vault_name: Optional[str] = Field(
        default=None, description="Key Vault name for secrets"
    )
    key_vault_uri: Optional[str] = Field(
        default=None, description="Key Vault URI"
    )

    # Processing Configuration
    batch_size_limit: int = Field(
        default=10000, description="Maximum documents per batch"
    )
    supported_formats: str = Field(
        default=".pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.tiff,.bmp,.html,.txt",
        description="Comma-separated list of supported file extensions",
    )
    max_file_size_mb: int = Field(
        default=100, description="Maximum file size in MB"
    )
    parallel_upload_workers: int = Field(
        default=4, description="Number of parallel upload workers"
    )
    
    # Blob Upload Configuration
    blob_max_block_size: int = Field(
        default=4 * 1024 * 1024, description="Maximum block size for blob uploads (4 MB)"
    )
    blob_max_single_put_size: int = Field(
        default=8 * 1024 * 1024, description="Maximum size for single put operations (8 MB)"
    )
    blob_upload_concurrency: int = Field(
        default=2, description="Concurrency for individual blob uploads"
    )

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")
    log_file: str = Field(
        default="logs/docprocessor.log", description="Log file path"
    )

    # Retry Configuration
    retry_max_attempts: int = Field(default=3, description="Maximum retry attempts")
    retry_backoff_factor: int = Field(default=2, description="Exponential backoff factor")
    retry_initial_wait_seconds: int = Field(
        default=1, description="Initial retry wait time"
    )

    # Optional Services (Phase 2+)
    translator_endpoint: Optional[str] = Field(
        default=None, description="Azure Translator endpoint"
    )
    translator_key: Optional[str] = Field(default=None, description="Translator API key")
    translator_region: Optional[str] = Field(default=None, description="Translator region")
    translation_default_locales: List[str] = Field(
        default_factory=lambda: [
            "en",
            "nb-NO",
            "es-ES",
            "ko-KR",
            "zh-Hans",
            "id-ID",
            "ja-JP",
            "fr-FR",
            "vi-VN",
            "zh-Hant-TW",
        ],
        description="Default translation target locales",
    )
    translation_overwrite_existing: bool = Field(
        default=False, description="Overwrite existing translation artifacts"
    )
    translation_max_chars_per_request: int = Field(
        default=4500,
        description="Maximum characters per translation request payload",
    )
    translation_request_batch_size: int = Field(
        default=20,
        description="Maximum number of text segments per translation request",
    )
    translation_manifest_filename: str = Field(
        default="manifest.json", description="Manifest filename for translation outputs"
    )

    openai_endpoint: Optional[str] = Field(
        default=None, description="Azure OpenAI endpoint"
    )
    openai_key: Optional[str] = Field(default=None, description="Azure OpenAI API key")
    openai_deployment_name: Optional[str] = Field(
        default=None, description="Azure OpenAI deployment name"
    )

    @field_validator("supported_formats")
    @classmethod
    def parse_supported_formats(cls, v: str) -> List[str]:
        """Parse comma-separated formats into a list."""
        if isinstance(v, str):
            return [fmt.strip().lower() for fmt in v.split(",")]
        return v

    @field_validator("translation_default_locales", mode="before")
    @classmethod
    def parse_translation_locales(cls, value):
        """Normalize translation locales from comma-separated strings."""
        if isinstance(value, str):
            return [
                locale.strip()
                for locale in value.split(",")
                if locale and locale.strip()
            ]
        return value

    @property
    def storage_account_url(self) -> str:
        """Generate storage account URL."""
        if not self.storage_account_name:
            raise ValueError("Storage account name is required for blob storage operations")
        return f"https://{self.storage_account_name}.blob.core.windows.net/"

    @property
    def max_file_size_bytes(self) -> int:
        """Convert max file size to bytes."""
        return self.max_file_size_mb * 1024 * 1024

    def get_supported_extensions(self) -> List[str]:
        """Get list of supported file extensions."""
        if isinstance(self.supported_formats, list):
            return self.supported_formats
        return [fmt.strip().lower() for fmt in self.supported_formats.split(",")]

    def ensure_log_directory(self) -> None:
        """Ensure log directory exists."""
        log_path = Path(self.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def get_translation_locales(self) -> List[str]:
        """Return configured translation locales."""
        return list(self.translation_default_locales)


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_log_directory()
    return _settings


def reload_settings() -> Settings:
    """Reload settings from environment."""
    global _settings
    _settings = Settings()
    _settings.ensure_log_directory()
    return _settings
