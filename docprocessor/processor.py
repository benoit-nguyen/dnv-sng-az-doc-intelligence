"""
Results processor module.

This module handles downloading, parsing, and exporting Document Intelligence results:
- Downloads result files from blob storage
- Parses JSON analysis results
- Exports to Markdown format
- Extracts tables to structured format
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class DocumentResult:
    """Parsed document analysis result."""
    
    source_file: str
    source_stem: str
    result_blob_name: str
    relative_blob_path: str
    page_count: int
    content: str
    tables: List[Dict[str, Any]]
    key_value_pairs: List[Dict[str, str]]
    paragraphs: List[str]
    confidence: float = 0.0
    raw_json: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "source_file": self.source_file,
            "source_stem": self.source_stem,
            "result_blob_name": self.result_blob_name,
            "relative_blob_path": self.relative_blob_path,
            "page_count": self.page_count,
            "content": self.content,
            "tables": self.tables,
            "key_value_pairs": self.key_value_pairs,
            "paragraphs": self.paragraphs,
            "confidence": self.confidence,
            "raw_json": self.raw_json,
        }


class ResultsProcessor:
    """
    Handles processing and exporting Document Intelligence results.
    
    Downloads result files from blob storage, parses JSON results,
    and exports to various formats (Markdown, CSV, JSON).
    """
    
    def __init__(self):
        """Initialize the results processor."""
        self.settings = get_settings()
        
        # Create blob service client - use connection string if available, otherwise managed identity
        if self.settings.storage_connection_string:
            self.blob_service_client = BlobServiceClient.from_connection_string(
                conn_str=self.settings.storage_connection_string
            )
        else:
            self.credential = DefaultAzureCredential()
            account_url = f"https://{self.settings.storage_account_name}.blob.core.windows.net"
            self.blob_service_client = BlobServiceClient(
                account_url=account_url,
                credential=self.credential
            )
        
        logger.info("ResultsProcessor initialized")
    
    @retry(
        retry=retry_if_exception_type((Exception,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def download_result(
        self,
        blob_name: str,
        result_container: Optional[str] = None,
        output_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Download and parse a result file from blob storage.
        
        Args:
            blob_name: Name of the result blob
            result_container: Result container name (defaults to config)
            output_path: Optional path to save raw JSON file
            
        Returns:
            Parsed JSON result as dictionary
        """
        if not result_container:
            result_container = self.settings.storage_container_results
        
        logger.info(f"Downloading result: {blob_name}")
        
        # Get blob client
        blob_client = self.blob_service_client.get_blob_client(
            container=result_container,
            blob=blob_name
        )
        
        # Download blob content
        blob_data = blob_client.download_blob().readall()
        
        # Parse JSON
        result_json = json.loads(blob_data)
        
        # Optionally save raw JSON
        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(blob_data)
            logger.info(f"Saved raw result to: {output_path}")
        
        return result_json
    
    def parse_result(self, result_json: Dict[str, Any], blob_name: str) -> DocumentResult:
        """
        Parse a Document Intelligence result into structured format.
        
        Args:
            result_json: Raw JSON result from Document Intelligence
            blob_name: Name of the blob containing this result
            
        Returns:
            DocumentResult object
        """
        # Extract basic info
        analyze_result = result_json.get("analyzeResult", {})
        
        # Get content
        content = analyze_result.get("content", "")
        
        # Get page count
        pages = analyze_result.get("pages", [])
        page_count = len(pages)
        
        # Extract tables
        tables = []
        for table in analyze_result.get("tables", []):
            table_data = {
                "row_count": table.get("rowCount", 0),
                "column_count": table.get("columnCount", 0),
                "cells": []
            }
            for cell in table.get("cells", []):
                table_data["cells"].append({
                    "row_index": cell.get("rowIndex", 0),
                    "column_index": cell.get("columnIndex", 0),
                    "content": cell.get("content", ""),
                    "kind": cell.get("kind", "content"),
                })
            tables.append(table_data)
        
        # Extract key-value pairs
        key_value_pairs = []
        for kv_pair in analyze_result.get("keyValuePairs", []):
            key_text = ""
            value_text = ""
            
            if kv_pair.get("key"):
                key_text = kv_pair["key"].get("content", "")
            if kv_pair.get("value"):
                value_text = kv_pair["value"].get("content", "")
            
            key_value_pairs.append({
                "key": key_text,
                "value": value_text
            })
        
        # Extract paragraphs
        paragraphs = [
            para.get("content", "")
            for para in analyze_result.get("paragraphs", [])
        ]
        
        # Calculate average confidence
        confidence_values = []
        for page in pages:
            for word in page.get("words", []):
                if "confidence" in word:
                    confidence_values.append(word["confidence"])
        
        avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
        
        # Get source file from result
        metadata = result_json.get("metadata", {})
        source_file = metadata.get("source", "unknown")

        # Derive path information from blob name when source missing
        relative_blob_path = blob_name
        source_stem = Path(blob_name).stem
        if source_file and source_file != "unknown":
            source_stem = Path(source_file).stem
        elif source_stem.endswith("_analysis"):
            source_stem = source_stem[:-9]
        
        return DocumentResult(
            source_file=source_file,
            source_stem=source_stem,
            result_blob_name=blob_name,
            relative_blob_path=relative_blob_path,
            page_count=page_count,
            content=content,
            tables=tables,
            key_value_pairs=key_value_pairs,
            paragraphs=paragraphs,
            confidence=avg_confidence,
            raw_json=result_json,
        )
    
    def export_to_markdown(
        self,
        document_result: DocumentResult,
        output_path: Path,
        include_tables: bool = True,
        include_metadata: bool = True
    ) -> None:
        """
        Export document result to Markdown format.
        
        Args:
            document_result: Parsed document result
            output_path: Path to save Markdown file
            include_tables: Whether to include tables
            include_metadata: Whether to include metadata header
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        lines = []
        
        # Add metadata header
        if include_metadata:
            lines.append(f"# Document Analysis Result")
            lines.append(f"")
            lines.append(f"**Source File:** `{document_result.source_file}`")
            lines.append(f"**Pages:** {document_result.page_count}")
            lines.append(f"**Average Confidence:** {document_result.confidence:.2%}")
            lines.append(f"")
            lines.append(f"---")
            lines.append(f"")
        
        # Add content
        lines.append(f"## Document Content")
        lines.append(f"")
        lines.append(document_result.content)
        lines.append(f"")
        
        # Add tables
        if include_tables and document_result.tables:
            lines.append(f"## Tables")
            lines.append(f"")
            
            for i, table in enumerate(document_result.tables, 1):
                lines.append(f"### Table {i}")
                lines.append(f"")
                lines.append(f"**Rows:** {table['row_count']}, **Columns:** {table['column_count']}")
                lines.append(f"")
                
                # Build table in Markdown format
                if table['cells']:
                    # Organize cells into grid
                    grid = {}
                    for cell in table['cells']:
                        row = cell['row_index']
                        col = cell['column_index']
                        if row not in grid:
                            grid[row] = {}
                        grid[row][col] = cell['content']
                    
                    # Create markdown table
                    for row_idx in sorted(grid.keys()):
                        row_data = grid[row_idx]
                        row_cells = [row_data.get(col, "") for col in range(table['column_count'])]
                        lines.append(f"| {' | '.join(row_cells)} |")
                        
                        # Add header separator after first row
                        if row_idx == 0:
                            lines.append(f"| {' | '.join(['---'] * table['column_count'])} |")
                    
                    lines.append(f"")
        
        # Add key-value pairs
        if document_result.key_value_pairs:
            lines.append(f"## Key-Value Pairs")
            lines.append(f"")
            for kv in document_result.key_value_pairs:
                lines.append(f"- **{kv['key']}**: {kv['value']}")
            lines.append(f"")
        
        # Write to file
        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Exported Markdown to: {output_path}")
    
    def export_tables_to_csv(
        self,
        document_result: DocumentResult,
        output_dir: Path
    ) -> List[Path]:
        """
        Export tables to CSV files.
        
        Args:
            document_result: Parsed document result
            output_dir: Directory to save CSV files
            
        Returns:
            List of paths to created CSV files
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        csv_files = []
        
        for i, table in enumerate(document_result.tables, 1):
            # Organize cells into grid
            grid = {}
            for cell in table['cells']:
                row = cell['row_index']
                col = cell['column_index']
                if row not in grid:
                    grid[row] = {}
                grid[row][col] = cell['content']
            
            # Create CSV content
            csv_lines = []
            for row_idx in sorted(grid.keys()):
                row_data = grid[row_idx]
                row_cells = [
                    f'"{row_data.get(col, "").replace(chr(34), chr(34)+chr(34))}"' 
                    for col in range(table['column_count'])
                ]
                csv_lines.append(",".join(row_cells))
            
            # Write CSV file
            source_name = Path(document_result.source_file).stem
            csv_file = output_dir / f"{source_name}_table_{i}.csv"
            csv_file.write_text("\n".join(csv_lines), encoding="utf-8")
            csv_files.append(csv_file)
            
            logger.info(f"Exported table {i} to: {csv_file}")
        
        return csv_files
    
    def batch_download_results(
        self,
        result_prefix: str = "results",
        result_container: Optional[str] = None,
        output_dir: Optional[Path] = None
    ) -> List[DocumentResult]:
        """
        Download and parse all results with given prefix.
        
        Args:
            result_prefix: Prefix to filter results
            result_container: Result container name (defaults to config)
            output_dir: Optional directory to save raw JSON files
            
        Returns:
            List of parsed DocumentResult objects
        """
        if not result_container:
            result_container = self.settings.storage_container_results
        
        # List all result blobs
        container_client = self.blob_service_client.get_container_client(result_container)
        blob_names = [
            blob.name 
            for blob in container_client.list_blobs(name_starts_with=result_prefix)
            if blob.name.endswith('.json')
        ]
        
        logger.info(f"Found {len(blob_names)} result files to process")
        
        results = []
        for blob_name in blob_names:
            try:
                # Download and parse
                output_path = None
                if output_dir:
                    output_path = Path(output_dir) / blob_name
                
                result_json = self.download_result(blob_name, result_container, output_path)
                document_result = self.parse_result(result_json, blob_name)
                results.append(document_result)
                
            except Exception as e:
                logger.error(f"Failed to process {blob_name}: {e}")
                continue
        
        logger.info(f"Successfully processed {len(results)}/{len(blob_names)} results")
        return results
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup resources."""
        try:
            self.blob_service_client.close()
            if hasattr(self, 'credential'):
                self.credential.close()
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
