"""
Azure Blob Storage uploader module.

This module handles uploading documents to Azure Blob Storage with:
- Managed Identity authentication (no API keys)
- Parallel uploads using ThreadPoolExecutor
- Exponential backoff retry logic
- Progress tracking and reporting
- Batch operations for efficiency
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from azure.core.exceptions import AzureError, ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, BlobClient, ContentSettings
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_settings
from .scanner import ScannedDocument

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    """Result of a document upload operation."""
    
    document: ScannedDocument
    blob_url: str
    success: bool
    error: Optional[str] = None
    bytes_uploaded: int = 0


@dataclass
class BatchUploadResult:
    """Result of a batch upload operation."""
    
    total_files: int
    successful: int
    failed: int
    total_bytes: int
    results: List[UploadResult]
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total_files == 0:
            return 0.0
        return (self.successful / self.total_files) * 100


class BlobUploader:
    """
    Handles uploading documents to Azure Blob Storage.
    
    Uses managed identity for authentication and implements:
    - Parallel uploads for efficiency
    - Retry logic for transient failures
    - Progress tracking
    - Proper error handling and logging
    """
    
    def __init__(self, progress_callback: Optional[Callable[[str, int, int], None]] = None):
        """
        Initialize the BlobUploader.
        
        Args:
            progress_callback: Optional callback function(filename, current, total) 
                             for progress updates
        """
        self.settings = get_settings()
        self.progress_callback = progress_callback
        
        # Create blob service client - use connection string if available, otherwise managed identity
        if self.settings.storage_connection_string:
            logger.info("Using connection string for authentication")
            self.blob_service_client = BlobServiceClient.from_connection_string(
                conn_str=self.settings.storage_connection_string,
                max_block_size=self.settings.blob_max_block_size,
                max_single_put_size=self.settings.blob_max_single_put_size,
            )
        else:
            logger.info("Using managed identity for authentication")
            # Initialize Azure credential (managed identity)
            self.credential = DefaultAzureCredential()
            
            # Create blob service client
            account_url = f"https://{self.settings.storage_account_name}.blob.core.windows.net"
            self.blob_service_client = BlobServiceClient(
                account_url=account_url,
                credential=self.credential,
                max_block_size=self.settings.blob_max_block_size,
                max_single_put_size=self.settings.blob_max_single_put_size,
            )
        
        logger.info(
            f"BlobUploader initialized for account: {self.settings.storage_account_name}"
        )
    
    def _get_content_type(self, file_path: Path) -> str:
        """
        Get the appropriate content type for a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            MIME type string
        """
        extension = file_path.suffix.lower()
        content_types = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".tiff": "image/tiff",
            ".bmp": "image/bmp",
            ".html": "text/html",
            ".txt": "text/plain",
        }
        return content_types.get(extension, "application/octet-stream")
    
    @retry(
        retry=retry_if_exception_type((AzureError,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _upload_single_blob(
        self, 
        document: ScannedDocument,
        overwrite: bool = False,
        blob_prefix: Optional[str] = None,
    ) -> UploadResult:
        """
        Upload a single document to blob storage with retry logic.
        """
        try:
            # Create blob name (preserve folder structure)
            blob_name = document.relative_path.replace("\\", "/")
            if blob_prefix:
                blob_name = f"{blob_prefix.strip('/')}/{blob_name}"
            
            # Get blob client
            blob_client = self.blob_service_client.get_blob_client(
                container=self.settings.storage_container_source,
                blob=blob_name
            )
            
            # Check if blob already exists
            if not overwrite and blob_client.exists():
                logger.info(f"Blob already exists, skipping: {blob_name}")
                return UploadResult(
                    document=document,
                    blob_url=blob_client.url,
                    success=True,
                    bytes_uploaded=0,
                )
            
            # Set content settings
            content_settings = ContentSettings(
                content_type=self._get_content_type(Path(document.file_path))
            )
            
            # Upload the file
            with open(document.file_path, "rb") as data:
                blob_client.upload_blob(
                    data,
                    overwrite=overwrite,
                    content_settings=content_settings,
                    max_concurrency=self.settings.blob_upload_concurrency,
                )
            
            logger.info(f"Uploaded: {blob_name} ({document.file_size_bytes / (1024*1024):.2f} MB)")
            
            return UploadResult(
                document=document,
                blob_url=blob_client.url,
                success=True,
                bytes_uploaded=document.file_size_bytes,
            )
            
        except ResourceExistsError:
            logger.warning(f"Blob already exists: {document.relative_path}")
            return UploadResult(
                document=document,
                blob_url="",
                success=False,
                error="Blob already exists",
            )
            
        except Exception as e:
            logger.error(f"Failed to upload {document.relative_path}: {e}")
            return UploadResult(
                document=document,
                blob_url="",
                success=False,
                error=str(e),
            )
    
    def upload_documents(
        self,
        documents: List[ScannedDocument],
        overwrite: bool = False,
        blob_prefix: Optional[str] = None,
    ) -> BatchUploadResult:
        """
        Upload multiple documents in parallel.
        
        Args:
            documents: List of ScannedDocument objects to upload
            overwrite: Whether to overwrite existing blobs
            blob_prefix: Optional prefix prepended to every blob name for run isolation
            
        Returns:
            BatchUploadResult with summary and individual results
        """
        if not documents:
            logger.warning("No documents to upload")
            return BatchUploadResult(
                total_files=0,
                successful=0,
                failed=0,
                total_bytes=0,
                results=[],
            )
        
        logger.info(f"Starting upload of {len(documents)} documents...")
        
        results: List[UploadResult] = []
        successful = 0
        failed = 0
        total_bytes = 0
        
        # Upload files in parallel
        with ThreadPoolExecutor(
            max_workers=self.settings.parallel_upload_workers
        ) as executor:
            # Submit all upload tasks
            future_to_doc = {
                executor.submit(self._upload_single_blob, doc, overwrite, blob_prefix): doc
                for doc in documents
            }
            
            # Process completed uploads
            for i, future in enumerate(as_completed(future_to_doc), 1):
                result = future.result()
                results.append(result)
                
                if result.success:
                    successful += 1
                    total_bytes += result.bytes_uploaded
                else:
                    failed += 1
                
                # Call progress callback if provided
                if self.progress_callback:
                    self.progress_callback(
                        result.document.file_name,
                        i,
                        len(documents)
                    )
        
        batch_result = BatchUploadResult(
            total_files=len(documents),
            successful=successful,
            failed=failed,
            total_bytes=total_bytes,
            results=results,
        )
        
        logger.info(
            f"Upload complete: {successful}/{len(documents)} successful "
            f"({batch_result.success_rate:.1f}%), "
            f"{total_bytes / (1024 * 1024):.2f} MB uploaded"
        )
        
        return batch_result
    
    def get_blob_url(self, relative_path: str) -> str:
        """
        Get the URL for a blob.
        
        Args:
            relative_path: Relative path of the document
            
        Returns:
            Full blob URL
        """
        blob_name = relative_path.replace("\\", "/")
        blob_client = self.blob_service_client.get_blob_client(
            container=self.settings.storage_container_source,
            blob=blob_name
        )
        return blob_client.url
    
    def list_uploaded_blobs(self, prefix: Optional[str] = None) -> List[str]:
        """
        List all uploaded blobs in the source container.
        
        Args:
            prefix: Optional prefix to filter blobs
            
        Returns:
            List of blob names
        """
        container_client = self.blob_service_client.get_container_client(
            self.settings.storage_container_source
        )
        
        blob_names = [
            blob.name 
            for blob in container_client.list_blobs(name_starts_with=prefix)
        ]
        
        logger.info(f"Found {len(blob_names)} blobs in container")
        return blob_names
    
    def delete_blob(self, relative_path: str) -> bool:
        """
        Delete a blob from storage.
        
        Args:
            relative_path: Relative path of the document
            
        Returns:
            True if successful, False otherwise
        """
        try:
            blob_name = relative_path.replace("\\", "/")
            blob_client = self.blob_service_client.get_blob_client(
                container=self.settings.storage_container_source,
                blob=blob_name
            )
            blob_client.delete_blob()
            logger.info(f"Deleted blob: {blob_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete blob {relative_path}: {e}")
            return False
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup resources."""
        try:
            self.blob_service_client.close()
            self.credential.close()
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
