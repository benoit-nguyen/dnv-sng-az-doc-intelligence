"""
Translation module for Document Intelligence results.

Translates extracted text content from JSON analysis results using Azure Translator,
then recreates PDFs with translated content using precise layout reconstruction.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import certifi
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from tenacity import retry, stop_after_attempt, wait_exponential
import urllib3

from .config import get_settings
from .pdf_recreator import (
    build_translator_from_settings,
    create_pdf_from_analysis,
    translate_analysis_content,
)

# Disable SSL warnings for development (Windows SSL cert issues)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

DOCUMENT_TRANSLATION_EXTENSIONS = {
    ".docx",
    ".xlsx",
    ".pptx",
    ".pdf",
    ".txt",
    ".html",
    ".htm",
}


@dataclass
class DocumentTranslationResult:
    """Result of translating one source document."""

    source_path: Path
    output_path: Optional[Path]
    success: bool
    error: Optional[str] = None


class AzureTranslator:
    """Azure Translator service wrapper with Managed Identity support."""
    
    def __init__(self):
        """Initialize Azure Translator client."""
        self.settings = get_settings()
        self.endpoint = self.settings.translator_endpoint
        
        if not self.endpoint:
            raise ValueError(
                "Azure Translator not configured. "
                "Set TRANSLATOR_ENDPOINT in .env"
            )
        
        # Initialize credential (managed identity preferred, fallback to key)
        if self.settings.translator_key:
            # Use API key if provided (development/testing)
            logger.warning(
                "Using API key authentication for Translator. "
                "Consider using Managed Identity for production."
            )
            self.auth_mode = 'key'
            self.key = self.settings.translator_key
            self.region = self.settings.translator_region
        else:
            # Use managed identity (preferred for production)
            logger.info("Using Managed Identity authentication for Translator")
            self.auth_mode = 'managed_identity'
            self.credential = DefaultAzureCredential()
            # Get access token for Cognitive Services
            self._token = None
            self._token_expiry = None
        
        self.translate_url = f"{self.endpoint.rstrip('/')}/translate"
        logger.info(f"Azure Translator initialized: {self.endpoint} (auth: {self.auth_mode})")
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """
        Get authentication headers for API requests.
        
        Returns:
            Dictionary with authentication headers
        """
        headers = {
            'Content-type': 'application/json',
            'X-ClientTraceId': str(uuid.uuid4())
        }
        
        if self.auth_mode == 'key':
            # Key-based authentication
            headers['Ocp-Apim-Subscription-Key'] = self.key
            if self.region:
                headers['Ocp-Apim-Subscription-Region'] = self.region
        else:
            # Managed Identity authentication
            # Get bearer token from Azure AD
            token = self._get_bearer_token()
            headers['Authorization'] = f'Bearer {token}'
        
        return headers
    
    def _get_bearer_token(self) -> str:
        """
        Get bearer token for Managed Identity authentication.
        
        Returns:
            Bearer token string
        """
        from datetime import datetime, timedelta
        
        # Check if we have a valid cached token
        if self._token and self._token_expiry:
            if datetime.utcnow() < self._token_expiry:
                return self._token
        
        # Get new token
        # Scope for Cognitive Services: https://cognitiveservices.azure.com/.default
        token_obj = self.credential.get_token('https://cognitiveservices.azure.com/.default')
        self._token = token_obj.token
        # Token typically expires in 1 hour, refresh 5 minutes early
        self._token_expiry = datetime.utcnow() + timedelta(seconds=token_obj.expires_on - 300)
        
        logger.debug("Obtained new bearer token for Translator")
        return self._token
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def translate_text(
        self, 
        text: str, 
        target_language: str = 'en',
        source_language: Optional[str] = None
    ) -> str:
        """
        Translate text to target language.
        
        Args:
            text: Text to translate
            target_language: Target language code (e.g., 'en', 'ko', 'ja')
            source_language: Source language code (auto-detect if None)
            
        Returns:
            Translated text
        """
        if not text or not text.strip():
            return text
        
        params = {
            'api-version': '3.0',
            'to': target_language
        }
        
        if source_language:
            params['from'] = source_language
        
        body = [{'text': text}]
        
        try:
            # Get fresh auth headers for each request
            headers = self._get_auth_headers()
            
            # Note: SSL verification disabled for development on Windows
            # For production, ensure Python has proper SSL certificates installed
            response = requests.post(
                self.translate_url,
                params=params,
                headers=headers,
                json=body,
                timeout=30,
                verify=False  # Disable SSL verification (dev only)
            )
            response.raise_for_status()
            
            result = response.json()
            translated = result[0]['translations'][0]['text']
            
            logger.debug(f"Translated: '{text[:50]}...' -> '{translated[:50]}...'")
            return translated
            
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return text  # Return original on error
    
    def translate_batch(
        self,
        texts: List[str],
        target_language: str = 'en',
        source_language: Optional[str] = None
    ) -> List[str]:
        """
        Translate multiple texts in a single batch.
        
        Args:
            texts: List of texts to translate
            target_language: Target language code
            source_language: Source language code (auto-detect if None)
            
        Returns:
            List of translated texts
        """
        if not texts:
            return []
        
        # Azure Translator API limits to 100 texts per request
        batch_size = 100
        all_translations = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            params = {
                'api-version': '3.0',
                'to': target_language
            }
            
            if source_language:
                params['from'] = source_language
            
            body = [{'text': text} for text in batch]
            
            try:
                # Get fresh auth headers for each batch
                headers = self._get_auth_headers()
                
                # Note: SSL verification disabled for development on Windows
                # For production, ensure Python has proper SSL certificates installed
                response = requests.post(
                    self.translate_url,
                    params=params,
                    headers=headers,
                    json=body,
                    timeout=60,
                    verify=False  # Disable SSL verification (dev only)
                )
                response.raise_for_status()
                
                results = response.json()
                translations = [r['translations'][0]['text'] for r in results]
                all_translations.extend(translations)
                
                logger.info(f"Translated batch {i//batch_size + 1}: {len(translations)} texts")
                
            except Exception as e:
                logger.error(f"Batch translation failed: {e}")
                all_translations.extend(batch)  # Return originals on error
        
        return all_translations


class DocumentTranslator:
    """Translates Document Intelligence JSON results."""
    
    def __init__(self, target_language: str = 'en'):
        """
        Initialize document translator.
        
        Args:
            target_language: Target language code (default: 'en' for English)
        """
        self.translator = AzureTranslator()
        self.target_language = target_language
        logger.info(f"Document translator initialized (target: {target_language})")
    
    def translate_json_result(self, json_path: Path) -> Dict[str, Any]:
        """
        Translate a Document Intelligence JSON result file.
        
        Args:
            json_path: Path to JSON result file
            
        Returns:
            Translated JSON structure
        """
        logger.info(f"Translating JSON: {json_path}")
        
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Translate content
        if 'content' in data:
            data['content'] = self.translator.translate_text(
                data['content'], 
                self.target_language
            )
        
        # Translate paragraphs
        if 'paragraphs' in data and isinstance(data['paragraphs'], list):
            paragraphs = []
            for para in data['paragraphs']:
                if isinstance(para, dict) and 'content' in para:
                    para['content'] = self.translator.translate_text(
                        para['content'],
                        self.target_language
                    )
                    paragraphs.append(para)
                elif isinstance(para, str):
                    paragraphs.append(
                        self.translator.translate_text(para, self.target_language)
                    )
            data['paragraphs'] = paragraphs
        
        # Translate tables
        if 'tables' in data and isinstance(data['tables'], list):
            for table in data['tables']:
                if 'cells' in table:
                    for cell in table['cells']:
                        if 'content' in cell:
                            cell['content'] = self.translator.translate_text(
                                cell['content'],
                                self.target_language
                            )
        
        # Translate key-value pairs
        if 'keyValuePairs' in data and isinstance(data['keyValuePairs'], list):
            for kvp in data['keyValuePairs']:
                if 'key' in kvp and 'content' in kvp['key']:
                    kvp['key']['content'] = self.translator.translate_text(
                        kvp['key']['content'],
                        self.target_language
                    )
                if 'value' in kvp and 'content' in kvp['value']:
                    kvp['value']['content'] = self.translator.translate_text(
                        kvp['value']['content'],
                        self.target_language
                    )
        
        logger.info(f"Translation complete: {json_path.name}")
        return data
    
    def save_translated_json(self, translated_data: Dict[str, Any], output_path: Path):
        """
        Save translated JSON to file.
        
        Args:
            translated_data: Translated JSON data
            output_path: Output file path
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(translated_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved translated JSON: {output_path}")
    
    def translate_batch_results(
        self,
        input_dir: Path,
        output_dir: Path,
        pattern: str = "*.json"
    ) -> List[Path]:
        """
        Translate all JSON files in a directory.
        
        Args:
            input_dir: Input directory containing JSON files
            output_dir: Output directory for translated JSON files
            pattern: File pattern to match (default: "*.json")
            
        Returns:
            List of translated file paths
        """
        input_dir = Path(input_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        json_files = list(input_dir.glob(pattern))
        translated_files = []
        
        logger.info(f"Translating {len(json_files)} JSON files...")
        
        for json_file in json_files:
            try:
                translated_data = self.translate_json_result(json_file)
                
                # Create output path with language suffix
                output_name = f"{json_file.stem}_{self.target_language}.json"
                output_path = output_dir / output_name
                
                self.save_translated_json(translated_data, output_path)
                translated_files.append(output_path)
                
            except Exception as e:
                logger.error(f"Failed to translate {json_file}: {e}")
        
        logger.info(f"Translated {len(translated_files)}/{len(json_files)} files")
        return translated_files

class AzureDocumentFileTranslator:
    """Uses Azure's Document Translation API to preserve original layout."""
    
    def __init__(self):
        self.settings = get_settings()
        if not self.settings.translator_endpoint:
            raise ValueError("Azure Translator is not configured. Set TRANSLATOR_ENDPOINT in .env")
        if not self.settings.translator_key:
            raise ValueError("Azure Translator key is not configured. Set TRANSLATOR_KEY in .env")

        self.endpoint = self.settings.translator_endpoint.rstrip("/")
        self.key = self.settings.translator_key
        self.region = self.settings.translator_region
        self.doc_endpoint = self._build_document_translation_endpoint(self.endpoint)

    @staticmethod
    def _build_document_translation_endpoint(endpoint: str) -> str:
        """Build the synchronous Document Translation endpoint from a custom domain."""
        normalized = endpoint.rstrip("/")
        if "api.cognitive.microsofttranslator.com" in normalized:
            raise ValueError(
                "Azure Document Translation requires the custom domain endpoint from the "
                "Translator resource overview, for example "
                "https://<resource-name>.cognitiveservices.azure.com. "
                "The generic endpoint https://api.cognitive.microsofttranslator.com only "
                "works for text translation. Update TRANSLATOR_ENDPOINT in .env."
            )
        if "api.cognitive.microsoft.com" in normalized:
            raise ValueError(
                "Azure Document Translation requires the custom domain endpoint from the "
                "Translator resource overview, for example "
                "https://<resource-name>.cognitiveservices.azure.com. "
                "Regional api.cognitive.microsoft.com endpoints are not valid for this "
                "synchronous document translation call. Update TRANSLATOR_ENDPOINT in .env."
            )
        return f"{normalized}/translator/document:translate"

    def translate_document(
        self,
        file_path: Path,
        target_language: str = "en",
        output_path: Optional[Path] = None,
        overwrite: bool = False,
    ) -> Path:
        """Translate a document and save it beside the original."""
        file_path = Path(file_path)
        logger.info(f"Translating {file_path.name} to {target_language} while preserving formatting...")

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = file_path.suffix.lower()
        if ext not in DOCUMENT_TRANSLATION_EXTENSIONS:
            supported = ", ".join(sorted(DOCUMENT_TRANSLATION_EXTENSIONS))
            raise ValueError(f"Unsupported file type '{ext}'. Supported types: {supported}")
        
        url = f"{self.doc_endpoint}?targetLanguage={target_language}&api-version=2024-05-01"

        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
        }
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region

        mime_types = {
            ".pdf":  "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".txt":  "text/plain",
            ".html": "text/html",
            ".htm":  "text/html",
        }
        content_type = mime_types.get(ext, "application/octet-stream")

        if output_path is None:
            output_path = file_path.parent / f"{file_path.stem}_{target_language}{file_path.suffix}"
        else:
            output_path = Path(output_path)

        if output_path.exists() and not overwrite:
            raise FileExistsError(
                f"Output already exists: {output_path}. Use overwrite=True to replace it."
            )

        if ext == ".pdf":
            logger.info(
                "Using Document Intelligence layout analysis for PDF translation: %s",
                file_path,
            )
            return self._translate_pdf_with_document_intelligence(
                file_path,
                target_language,
                output_path,
            )
        
        # Open file as binary
        with open(file_path, "rb") as document_file:
            files = {
                "document": (file_path.name, document_file, content_type)
            }
            
            response = requests.post(url, headers=headers, files=files, timeout=300, verify=False)
            
        if response.status_code != 200:
            raise Exception(f"Translation failed: {response.status_code} - {response.text}")
            
        # Save the translated result next to the original
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(response.content)
            
        logger.info(f"Successfully saved translated document to: {output_path}")
        return output_path

    def _translate_pdf_with_document_intelligence(
        self,
        file_path: Path,
        target_language: str,
        output_path: Path,
    ) -> Path:
        settings = self.settings
        if not settings.document_intelligence_endpoint:
            raise ValueError(
                "PDF translation requires DOCUMENT_INTELLIGENCE_ENDPOINT in .env"
            )

        if settings.document_intelligence_key:
            credential = AzureKeyCredential(settings.document_intelligence_key)
        else:
            credential = DefaultAzureCredential()

        client = DocumentIntelligenceClient(
            endpoint=settings.document_intelligence_endpoint,
            credential=credential,
        )

        with open(file_path, "rb") as document_file:
            poller = client.begin_analyze_document(
                "prebuilt-layout",
                document_file,
                content_type="application/pdf",
            )
            analysis_result = poller.result()

        if hasattr(analysis_result, "as_dict"):
            analysis_data = analysis_result.as_dict()
        else:
            analysis_data = dict(analysis_result)

        with build_translator_from_settings() as translator:
            translate_analysis_content(analysis_data, translator, target_language)

        create_pdf_from_analysis("", str(output_path), analysis_data=analysis_data)
        logger.info("Successfully saved recreated translated PDF to: %s", output_path)
        return output_path

    def translate_documents(
        self,
        file_paths: List[Path],
        target_language: str = "en",
        overwrite: bool = False,
    ) -> List[DocumentTranslationResult]:
        """Translate multiple documents and continue after per-file failures."""
        results: List[DocumentTranslationResult] = []
        for file_path in file_paths:
            try:
                output_path = self.translate_document(
                    file_path,
                    target_language=target_language,
                    overwrite=overwrite,
                )
                results.append(DocumentTranslationResult(Path(file_path), output_path, True))
            except Exception as exc:
                logger.error("Failed to translate %s: %s", file_path, exc)
                results.append(DocumentTranslationResult(Path(file_path), None, False, str(exc)))
        return results

