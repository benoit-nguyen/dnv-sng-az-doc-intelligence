"""
Azure Document Intelligence batch analyzer module.

This module handles batch analysis of documents using Azure Document Intelligence:
- Submits batch analysis requests with blob source
- Polls for batch operation status
- Retrieves analysis results
- Handles errors and retries
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import (
    AnalyzeBatchDocumentsRequest,
    AnalyzeBatchResult,
    AzureBlobContentSource,
    DocumentContentFormat,
)
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError, HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, generate_container_sas, ContainerSasPermissions
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_settings

logger = logging.getLogger(__name__)


class BatchStatus(Enum):
    """Batch operation status."""
    NOT_STARTED = "notStarted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class BatchAnalysisResult:
    """Result of a batch analysis operation."""
    
    operation_id: str
    status: BatchStatus
    created_at: datetime
    last_updated: datetime
    succeeded_count: int = 0
    failed_count: int = 0
    total_count: int = 0
    result_container_url: str = ""
    result_prefix: str = ""
    errors: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []
    
    @property
    def is_complete(self) -> bool:
        """Check if batch operation is complete."""
        return self.status in [BatchStatus.SUCCEEDED, BatchStatus.FAILED, BatchStatus.CANCELED]
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total_count == 0:
            return 0.0
        return (self.succeeded_count / self.total_count) * 100


class DocumentIntelligenceAnalyzer:
    """
    Handles batch document analysis using Azure Document Intelligence.
    
    Uses managed identity for authentication and implements:
    - Batch submission with blob source
    - Status polling with timeout
    - Result retrieval from blob storage
    - Proper error handling and logging
    """
    
    def __init__(self):
        """Initialize the analyzer with Azure credentials."""
        self.settings = get_settings()
        
        # Initialize credential (managed identity preferred)
        if self.settings.document_intelligence_key:
            # Use API key if provided (fallback)
            self.credential = AzureKeyCredential(self.settings.document_intelligence_key)
            logger.info("Using API key authentication for Document Intelligence")
        else:
            # Use managed identity (preferred)
            self.credential = DefaultAzureCredential()
            logger.info("Using managed identity authentication for Document Intelligence")
        
        # Create Document Intelligence client
        self.client = DocumentIntelligenceClient(
            endpoint=self.settings.document_intelligence_endpoint,
            credential=self.credential
        )
        
        # Create blob service client for SAS token generation
        if self.settings.storage_connection_string:
            logger.info("Using connection string for blob storage access")
            self.blob_service_client = BlobServiceClient.from_connection_string(
                conn_str=self.settings.storage_connection_string
            )
        else:
            logger.info("Using managed identity for blob storage access")
            account_url = f"https://{self.settings.storage_account_name}.blob.core.windows.net"
            if isinstance(self.credential, AzureKeyCredential):
                # For key-based auth, we need DefaultAzureCredential for storage
                storage_credential = DefaultAzureCredential()
            else:
                storage_credential = self.credential
            
            self.blob_service_client = BlobServiceClient(
                account_url=account_url,
                credential=storage_credential
            )
        
        logger.info(
            f"DocumentIntelligenceAnalyzer initialized with endpoint: "
            f"{self.settings.document_intelligence_endpoint}"
        )
    
    def _generate_container_sas(
        self, 
        container_name: str,
        permissions: str = "rwdl",
        expiry_hours: int = 24
    ) -> str:
        """
        Generate a SAS token for a container.
        
        Args:
            container_name: Name of the container
            permissions: Permissions string (r=read, w=write, d=delete, l=list)
            expiry_hours: Hours until SAS token expires
            
        Returns:
            Container URL with SAS token
        """
        try:
            # Get container client
            container_client = self.blob_service_client.get_container_client(container_name)
            
            # Set permissions based on string
            perms = ContainerSasPermissions(
                read='r' in permissions,
                write='w' in permissions,
                delete='d' in permissions,
                list='l' in permissions
            )
            
            start_time = datetime.utcnow()
            expiry_time = start_time + timedelta(hours=expiry_hours)
            sas_token = None

            # 1. Try to use Account Key from connection string if available
            if self.settings.storage_connection_string:
                try:
                    conn_settings = {
                        item.split('=', 1)[0]: item.split('=', 1)[1] 
                        for item in self.settings.storage_connection_string.split(';') 
                        if '=' in item
                    }
                    account_key = conn_settings.get('AccountKey')
                    if account_key:
                        sas_token = generate_container_sas(
                            account_name=self.settings.storage_account_name,
                            container_name=container_name,
                            account_key=account_key,
                            permission=perms,
                            expiry=expiry_time,
                            start=start_time - timedelta(minutes=5)
                        )
                except Exception as e:
                    logger.warning(f"Failed to generate SAS from connection string: {e}")

            # 2. Try to get user delegation key if using managed identity (no SAS yet)
            if not sas_token and not self.settings.storage_connection_string:
                try:
                    ud_key = self.blob_service_client.get_user_delegation_key(
                        key_start_time=start_time - timedelta(minutes=15),
                        key_expiry_time=expiry_time + timedelta(minutes=15)
                    )
                    sas_token = generate_container_sas(
                        account_name=self.settings.storage_account_name,
                        container_name=container_name,
                        user_delegation_key=ud_key,
                        permission=perms,
                        expiry=expiry_time,
                        start=start_time - timedelta(minutes=5)
                    )
                except Exception as e:
                    logger.warning(f"Failed to get user delegation key: {e}")

            # 3. Fallback: try with account_key=None (will fail if no key, but kept for structure)
            if not sas_token:
                sas_token = generate_container_sas(
                    account_name=self.settings.storage_account_name,
                    container_name=container_name,
                    account_key=None,
                    permission=perms,
                    expiry=expiry_time
                )
            
            # Return container URL with SAS
            return f"{container_client.url}?{sas_token}"
        
        except Exception as e:
            logger.warning(f"Failed to generate SAS token, using managed identity: {e}")
            # Return container URL without SAS (will use managed identity)
            return container_client.url
    
    @retry(
        retry=retry_if_exception_type((AzureError, HttpResponseError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def start_batch_analysis(
        self,
        model_id: str = "prebuilt-layout",
        source_container: Optional[str] = None,
        result_container: Optional[str] = None,
        result_prefix: str = "results",
        output_format: str = "markdown",
        source_prefix: Optional[str] = None,
    ) -> str:
        """
        Start a batch analysis operation.

        Args:
            model_id: Document Intelligence model ID (default: prebuilt-layout)
            source_container: Source container name (defaults to config)
            result_container: Result container name (defaults to config)
            result_prefix: Prefix for result files
            output_format: Output content format (text or markdown)
            source_prefix: Optional blob prefix to restrict which source blobs are analysed

        Returns:
            Operation ID (continuation token) for polling
        """
        if not source_container:
            source_container = self.settings.storage_container_source
        if not result_container:
            result_container = self.settings.storage_container_results
        
        logger.info(
            f"Starting batch analysis with model '{model_id}' "
            f"for container '{source_container}'"
        )
        
        # Get container URLs (with or without SAS)
        source_url = self._generate_container_sas(source_container, permissions="rl")
        result_url = self._generate_container_sas(result_container, permissions="rwdl")
        
        # Create batch request
        batch_request = AnalyzeBatchDocumentsRequest(
            azure_blob_source=AzureBlobContentSource(
                container_url=source_url,
                prefix=source_prefix,
            ),
            result_container_url=result_url,
            result_prefix=result_prefix,
            overwrite_existing=False
        )
        
        # Set output content format
        content_format = None
        if output_format.lower() == "markdown":
            content_format = DocumentContentFormat.MARKDOWN
        elif output_format.lower() == "text":
            content_format = DocumentContentFormat.TEXT
        
        # Submit batch analysis request
        poller = self.client.begin_analyze_batch_documents(
            model_id=model_id,
            body=batch_request,
            output_content_format=content_format
        )
        
        # Get operation ID from poller
        operation_id = poller.continuation_token()
        
        logger.info(f"Batch analysis started. Operation ID: {operation_id}")
        
        return operation_id
    
    def get_batch_status(self, operation_id: str) -> BatchAnalysisResult:
        """
        Get the status of a batch operation.
        
        Args:
            operation_id: Operation ID from start_batch_analysis
            
        Returns:
            BatchAnalysisResult with current status
        """
        try:
            # Get batch result using continuation token
            poller = self.client.get_analyze_batch_result(operation_id)
            result: AnalyzeBatchResult = poller.result()
            
            # Parse status
            status_str = getattr(result, 'status', 'notStarted')
            try:
                status = BatchStatus(status_str)
            except ValueError:
                logger.warning(f"Unknown status: {status_str}, treating as RUNNING")
                status = BatchStatus.RUNNING
            
            # Create result object
            batch_result = BatchAnalysisResult(
                operation_id=operation_id,
                status=status,
                created_at=getattr(result, 'created_date_time', datetime.utcnow()),
                last_updated=getattr(result, 'last_updated_date_time', datetime.utcnow()),
                succeeded_count=getattr(result, 'succeeded_count', 0),
                failed_count=getattr(result, 'failed_count', 0),
                total_count=getattr(result, 'total_count', 0),
            )
            
            logger.info(
                f"Batch status: {status.value}, "
                f"{batch_result.succeeded_count}/{batch_result.total_count} succeeded"
            )
            
            return batch_result
            
        except Exception as e:
            logger.error(f"Failed to get batch status: {e}")
            raise
    
    def poll_batch_completion(
        self,
        operation_id: str,
        polling_interval: int = 30,
        timeout_minutes: int = 60
    ) -> BatchAnalysisResult:
        """
        Poll batch operation until completion or timeout.
        
        Args:
            operation_id: Operation ID to poll
            polling_interval: Seconds between status checks
            timeout_minutes: Maximum minutes to wait
            
        Returns:
            Final BatchAnalysisResult
        """
        start_time = datetime.utcnow()
        timeout = timedelta(minutes=timeout_minutes)
        
        logger.info(
            f"Polling batch operation {operation_id} "
            f"(interval: {polling_interval}s, timeout: {timeout_minutes}m)"
        )
        
        while True:
            # Check timeout
            if datetime.utcnow() - start_time > timeout:
                logger.error(f"Batch operation timed out after {timeout_minutes} minutes")
                raise TimeoutError(
                    f"Batch operation did not complete within {timeout_minutes} minutes"
                )
            
            # Get current status
            result = self.get_batch_status(operation_id)
            
            # Check if complete
            if result.is_complete:
                logger.info(
                    f"Batch operation complete: {result.status.value}, "
                    f"Success rate: {result.success_rate:.1f}%"
                )
                return result
            
            # Wait before next poll
            logger.debug(f"Batch still running, waiting {polling_interval}s...")
            time.sleep(polling_interval)
    
    def list_result_files(
        self,
        result_container: Optional[str] = None,
        result_prefix: str = "results"
    ) -> List[str]:
        """
        List all result files in the result container.
        
        Args:
            result_container: Result container name (defaults to config)
            result_prefix: Prefix to filter results
            
        Returns:
            List of blob names
        """
        if not result_container:
            result_container = self.settings.storage_container_results
        
        container_client = self.blob_service_client.get_container_client(result_container)
        
        blob_names = [
            blob.name 
            for blob in container_client.list_blobs(name_starts_with=result_prefix)
        ]
        
        logger.info(f"Found {len(blob_names)} result files with prefix '{result_prefix}'")
        return blob_names
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup resources."""
        try:
            self.client.close()
            self.blob_service_client.close()
            if hasattr(self.credential, 'close'):
                self.credential.close()
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
