"""
Azure Document Intelligence Batch Processor

A comprehensive solution for batch processing large datasets of documents
using Azure Document Intelligence.
"""

__version__ = "0.1.0"
__author__ = "DNV"

from .config import Settings
from .scanner import DocumentScanner
from .uploader import BlobUploader
from .analyzer import DocumentIntelligenceAnalyzer
from .processor import ResultsProcessor
from .translation import TranslationPipeline
from .pdf_recreator import AzureTranslator, create_pdf_from_analysis, translate_analysis_content

__all__ = [
    "Settings",
    "DocumentScanner",
    "BlobUploader",
    "DocumentIntelligenceAnalyzer",
    "ResultsProcessor",
    "TranslationPipeline",
    "AzureTranslator",
    "create_pdf_from_analysis",
    "translate_analysis_content",
]
