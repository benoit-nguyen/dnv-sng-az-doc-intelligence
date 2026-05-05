"""
Document Scanner Module

Recursively scans folders for supported document formats and generates
a manifest of files ready for batch processing.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Set
from datetime import datetime

from .config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ScannedDocument:
    """Represents a scanned document file."""
    
    file_path: str
    file_name: str
    file_extension: str
    file_size_bytes: int
    relative_path: str
    folder_depth: int
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class ScanResult:
    """Results of a folder scan operation."""
    
    root_folder: str
    scan_timestamp: str
    total_files: int
    total_size_bytes: int
    supported_files: int
    unsupported_files: int
    skipped_files: int
    documents: List[ScannedDocument]
    errors: List[str]
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'root_folder': self.root_folder,
            'scan_timestamp': self.scan_timestamp,
            'total_files': self.total_files,
            'total_size_bytes': self.total_size_bytes,
            'supported_files': self.supported_files,
            'unsupported_files': self.unsupported_files,
            'skipped_files': self.skipped_files,
            'documents': [doc.to_dict() for doc in self.documents],
            'errors': self.errors
        }
    
    def save_to_json(self, output_path: str) -> None:
        """Save scan results to JSON file."""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"Scan results saved to: {output_path}")

    def save_to_file(self, output_path: str) -> None:
        """Save scan results to JSON file."""
        self.save_to_json(output_path)


class DocumentScanner:
    """Scans folders for documents and prepares them for batch processing."""
    
    def __init__(
        self,
        supported_extensions: Optional[List[str]] = None,
        max_file_size_bytes: Optional[int] = None,
        exclude_folders: Optional[Set[str]] = None
    ):
        """
        Initialize document scanner.
        
        Args:
            supported_extensions: List of supported file extensions (e.g., ['.pdf', '.docx'])
            max_file_size_bytes: Maximum file size in bytes
            exclude_folders: Set of folder names to exclude from scan
        """
        settings = get_settings()
        
        self.supported_extensions = supported_extensions or settings.get_supported_extensions()
        self.max_file_size_bytes = max_file_size_bytes or settings.max_file_size_bytes
        self.exclude_folders = exclude_folders or {
            '__pycache__', '.git', '.venv', 'node_modules', '.azure'
        }
        
        logger.info(f"Scanner initialized with extensions: {self.supported_extensions}")
        logger.info(f"Max file size: {self.max_file_size_bytes / (1024*1024):.2f} MB")
    
    def scan_folder(
        self,
        folder_path: str,
        recursive: bool = True,
        max_depth: Optional[int] = None
    ) -> ScanResult:
        """
        Scan a folder for supported documents.
        
        Args:
            folder_path: Path to folder to scan
            recursive: Whether to scan subdirectories
            max_depth: Maximum folder depth to scan (None for unlimited)
        
        Returns:
            ScanResult containing information about scanned documents
        """
        folder = Path(folder_path).resolve()
        
        if not folder.exists():
            raise FileNotFoundError(f"Folder not found: {folder}")
        
        if not folder.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {folder}")
        
        logger.info(f"Starting scan of: {folder}")
        logger.info(f"Recursive: {recursive}, Max depth: {max_depth}")
        
        documents: List[ScannedDocument] = []
        errors: List[str] = []
        total_files = 0
        total_size = 0
        unsupported_count = 0
        skipped_count = 0
        
        try:
            for root, dirs, files in os.walk(folder):
                root_path = Path(root)
                
                # Calculate folder depth
                try:
                    relative = root_path.relative_to(folder)
                    depth = len(relative.parts)
                except ValueError:
                    depth = 0
                
                # Check max depth
                if max_depth is not None and depth > max_depth:
                    dirs.clear()  # Don't descend further
                    continue
                
                # Exclude certain folders
                dirs[:] = [d for d in dirs if d not in self.exclude_folders]
                
                # Stop recursion if not recursive
                if not recursive and depth > 0:
                    break
                
                # Process files in this directory
                for file_name in files:
                    total_files += 1
                    file_path = root_path / file_name
                    
                    try:
                        # Get file info
                        stat = file_path.stat()
                        file_size = stat.st_size
                        file_ext = file_path.suffix.lower()
                        
                        # Calculate relative path
                        try:
                            rel_path = str(file_path.relative_to(folder))
                        except ValueError:
                            rel_path = str(file_path)
                        
                        total_size += file_size
                        
                        # Check if supported
                        if file_ext not in self.supported_extensions:
                            unsupported_count += 1
                            logger.debug(f"Unsupported format: {file_name} ({file_ext})")
                            continue
                        
                        # Check file size
                        if file_size > self.max_file_size_bytes:
                            skipped_count += 1
                            error_msg = (
                                f"File too large: {file_name} "
                                f"({file_size / (1024*1024):.2f} MB exceeds "
                                f"{self.max_file_size_bytes / (1024*1024):.2f} MB limit)"
                            )
                            logger.warning(error_msg)
                            errors.append(error_msg)
                            continue
                        
                        # Check if file is accessible
                        if not os.access(file_path, os.R_OK):
                            skipped_count += 1
                            error_msg = f"File not readable: {file_name}"
                            logger.warning(error_msg)
                            errors.append(error_msg)
                            continue
                        
                        # Add to documents list
                        doc = ScannedDocument(
                            file_path=str(file_path),
                            file_name=file_name,
                            file_extension=file_ext,
                            file_size_bytes=file_size,
                            relative_path=rel_path,
                            folder_depth=depth
                        )
                        documents.append(doc)
                        
                    except Exception as e:
                        error_msg = f"Error processing {file_name}: {str(e)}"
                        logger.error(error_msg)
                        errors.append(error_msg)
        
        except Exception as e:
            error_msg = f"Error scanning folder: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)
        
        # Create scan result
        result = ScanResult(
            root_folder=str(folder),
            scan_timestamp=datetime.utcnow().isoformat(),
            total_files=total_files,
            total_size_bytes=total_size,
            supported_files=len(documents),
            unsupported_files=unsupported_count,
            skipped_files=skipped_count,
            documents=documents,
            errors=errors
        )
        
        # Log summary
        logger.info(f"Scan complete:")
        logger.info(f"  Total files found: {total_files}")
        logger.info(f"  Supported files: {len(documents)}")
        logger.info(f"  Unsupported files: {unsupported_count}")
        logger.info(f"  Skipped files: {skipped_count}")
        logger.info(f"  Total size: {total_size / (1024*1024*1024):.2f} GB")
        logger.info(f"  Errors: {len(errors)}")
        
        return result
    
    @staticmethod
    def load_scan_result(json_path: str) -> ScanResult:
        """
        Load scan results from JSON file.
        
        Args:
            json_path: Path to JSON file
        
        Returns:
            ScanResult object
        """
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        documents = [ScannedDocument(**doc) for doc in data['documents']]
        
        return ScanResult(
            root_folder=data['root_folder'],
            scan_timestamp=data['scan_timestamp'],
            total_files=data['total_files'],
            total_size_bytes=data['total_size_bytes'],
            supported_files=data['supported_files'],
            unsupported_files=data['unsupported_files'],
            skipped_files=data['skipped_files'],
            documents=documents,
            errors=data['errors']
        )

    def load_from_file(self, json_path: str) -> ScanResult:
        """Load scan results from JSON file."""
        return self.load_scan_result(json_path)
