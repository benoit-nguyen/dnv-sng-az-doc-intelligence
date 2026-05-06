"""
Translation pipeline for Document Processor.

Implements multi-locale translation of analyzed documents, supporting
per-format strategies and blob storage output management.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import requests
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .config import get_settings
from .processor import DocumentResult, ResultsProcessor

logger = logging.getLogger(__name__)


@dataclass
class TranslationRecord:
    """Summary of a single document-language translation."""

    source_document: str
    locale: str
    blob_name: Optional[str]
    status: str
    translated_segments: int = 0
    translated_table_cells: int = 0
    error: Optional[str] = None


class TranslationPipeline:
    """Coordinate translation of analyzed documents into multiple locales."""

    _COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(self) -> None:
        self.settings = get_settings()

        if not self.settings.translator_endpoint:
            raise ValueError(
                "Translator endpoint is not configured. Set TRANSLATOR_ENDPOINT in the environment."
            )

        self._endpoint = self.settings.translator_endpoint.rstrip("/") + "/translate"
        self._session = requests.Session()
        self._session.verify = self.settings.requests_ca_bundle or True

        # Reuse a single managed identity credential when API key is not supplied.
        self._identity_credential: Optional[DefaultAzureCredential] = None
        if not self.settings.translator_key or not self.settings.storage_connection_string:
            self._identity_credential = DefaultAzureCredential()

        # Storage client for translation outputs.
        if self.settings.storage_connection_string:
            self._blob_service_client = BlobServiceClient.from_connection_string(
                self.settings.storage_connection_string
            )
        else:
            account_url = f"https://{self.settings.storage_account_name}.blob.core.windows.net"
            self._blob_service_client = BlobServiceClient(
                account_url=account_url,
                credential=self._identity_credential,
            )

        self._container_client = self._blob_service_client.get_container_client(
            self.settings.storage_container_translations
        )
        try:
            self._container_client.create_container()
        except ResourceExistsError:
            pass

        self._cached_token: Optional[str] = None
        self._cached_token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Public API

    def translate_batch(
        self,
        batch_id: str,
        target_locales: Optional[Sequence[str]] = None,
        overwrite: Optional[bool] = None,
    ) -> List[TranslationRecord]:
        """Translate the documents belonging to a batch into target locales."""

        if not batch_id:
            raise ValueError("batch_id is required")

        locales = list(target_locales) if target_locales else self.settings.get_translation_locales()
        if not locales:
            raise ValueError("No target locales provided or configured")

        overwrite_existing = (
            self.settings.translation_overwrite_existing if overwrite is None else overwrite
        )

        logger.info(
            "Starting translation batch: batch_id=%s, locales=%s, overwrite=%s",
            batch_id,
            ",".join(locales),
            overwrite_existing,
        )

        manifest_index: Dict[str, Dict[str, object]] = {
            locale: {
                "batchId": batch_id,
                "generatedAt": self._utcnow(),
                "documents": [],
            }
            for locale in locales
        }

        records: List[TranslationRecord] = []

        with ResultsProcessor() as processor:
            document_results = processor.batch_download_results(result_prefix=batch_id)

        if not document_results:
            logger.warning("No document results found for batch prefix '%s'", batch_id)
            return records

        for document in document_results:
            doc_relative_path = self._derive_relative_document_path(batch_id, document)
            source_identifier = (
                document.source_file
                if document.source_file and document.source_file != "unknown"
                else document.source_stem
            )

            for locale in locales:
                manifest_entry: Dict[str, object] = {
                    "sourceDocument": source_identifier,
                    "relativePath": str(doc_relative_path).replace("\\", "/"),
                    "locale": locale,
                }

                try:
                    markdown_text, paragraph_count, table_cells = self._build_translated_markdown(
                        document, locale
                    )
                    blob_name = self._build_blob_name(locale, batch_id, doc_relative_path)

                    self._upload_text(
                        blob_name,
                        markdown_text,
                        overwrite_existing,
                        content_type="text/markdown",
                    )

                    manifest_entry.update(
                        {
                            "status": "succeeded",
                            "blobName": blob_name,
                            "paragraphsTranslated": paragraph_count,
                            "tableCellsTranslated": table_cells,
                        }
                    )

                    records.append(
                        TranslationRecord(
                            source_document=source_identifier,
                            locale=locale,
                            blob_name=blob_name,
                            status="succeeded",
                            translated_segments=paragraph_count,
                            translated_table_cells=table_cells,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - propagate status per document
                    logger.exception(
                        "Translation failed for document '%s' locale '%s': %s",
                        source_identifier,
                        locale,
                        exc,
                    )
                    manifest_entry.update(
                        {
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
                    records.append(
                        TranslationRecord(
                            source_document=source_identifier,
                            locale=locale,
                            blob_name=None,
                            status="failed",
                            error=str(exc),
                        )
                    )

                manifest_index[locale]["documents"].append(manifest_entry)

        # Persist manifests for each locale
        for locale, locale_manifest in manifest_index.items():
            manifest_blob = self._build_manifest_blob_name(locale, batch_id)
            manifest_payload = json.dumps(locale_manifest, ensure_ascii=False, indent=2)
            self._upload_text(
                manifest_blob,
                manifest_payload,
                overwrite=True,
                content_type="application/json",
            )

        return records

    def close(self) -> None:
        """Release network and credential resources."""
        try:
            self._session.close()
        finally:
            try:
                self._blob_service_client.close()
            finally:
                if self._identity_credential and hasattr(self._identity_credential, "close"):
                    self._identity_credential.close()

    def __enter__(self) -> "TranslationPipeline":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Helpers

    def _build_translated_markdown(
        self, document: DocumentResult, locale: str
    ) -> Tuple[str, int, int]:
        """Create translated Markdown content for a document."""

        paragraphs = [p for p in document.paragraphs if p and p.strip()]
        if not paragraphs and document.content:
            paragraphs = [line for line in document.content.splitlines() if line.strip()]

        translated_paragraphs: List[str] = []
        if paragraphs:
            translated_paragraphs = self._translate_segments(paragraphs, locale)

        lines: List[str] = []
        lines.append(f"# Translation ({locale})")
        lines.append("")
        lines.append(f"_Generated: {self._utcnow()}_")
        lines.append("")

        paragraph_count = len(translated_paragraphs)
        if translated_paragraphs:
            lines.append("## Paragraphs")
            lines.append("")
            for paragraph in translated_paragraphs:
                lines.append(paragraph)
                lines.append("")

        table_lines, table_cells = self._translate_tables(document.tables, locale)
        if table_lines:
            lines.append("## Tables")
            lines.append("")
            lines.extend(table_lines)

        markdown_text = "\n".join(lines).strip() + "\n"
        return markdown_text, paragraph_count, table_cells

    def _translate_tables(
        self, tables: List[Dict[str, object]], locale: str
    ) -> Tuple[List[str], int]:
        """Translate table content and render as Markdown."""

        if not tables:
            return [], 0

        rendered_lines: List[str] = []
        translated_cell_total = 0

        for index, table in enumerate(tables, start=1):
            row_count = int(table.get("row_count", 0) or 0)
            column_count = int(table.get("column_count", 0) or 0)
            cells = table.get("cells", []) or []

            if not row_count or not column_count or not cells:
                continue

            cell_texts = [str(cell.get("content", "")) for cell in cells]
            translated_cells = self._translate_segments(cell_texts, locale)
            translated_cell_total += len([text for text in translated_cells if text.strip()])

            grid: Dict[int, Dict[int, str]] = {}
            for cell, translated_text in zip(cells, translated_cells, strict=False):
                row_index = int(cell.get("row_index", 0) or 0)
                column_index = int(cell.get("column_index", 0) or 0)
                grid.setdefault(row_index, {})[column_index] = translated_text

            rendered_lines.append(f"### Table {index}")
            rendered_lines.append("")

            header_written = False
            for row_index in range(row_count):
                row_values = [grid.get(row_index, {}).get(col_index, "") for col_index in range(column_count)]
                rendered_lines.append("| " + " | ".join(row_values) + " |")
                if not header_written and row_values:
                    rendered_lines.append("| " + " | ".join(["---"] * column_count) + " |")
                    header_written = True

            rendered_lines.append("")

        return rendered_lines, translated_cell_total

    def _translate_segments(self, segments: Sequence[str], locale: str) -> List[str]:
        """Translate a list of text segments into the target locale."""

        if not segments:
            return []

        results: List[str] = ["" for _ in segments]
        buffer: List[str] = []
        buffer_indices: List[int] = []
        buffer_chars = 0

        max_batch = max(1, self.settings.translation_request_batch_size)
        max_chars = max(100, self.settings.translation_max_chars_per_request)

        for index, text in enumerate(segments):
            if not text:
                results[index] = ""
                continue

            buffer.append(text)
            buffer_indices.append(index)
            buffer_chars += len(text)

            if len(buffer) >= max_batch or buffer_chars >= max_chars:
                translated = self._dispatch_translation(buffer, locale)
                for target_index, translated_text in zip(buffer_indices, translated, strict=False):
                    results[target_index] = translated_text
                buffer = []
                buffer_indices = []
                buffer_chars = 0

        if buffer:
            translated = self._dispatch_translation(buffer, locale)
            for target_index, translated_text in zip(buffer_indices, translated, strict=False):
                results[target_index] = translated_text

        return results

    def _dispatch_translation(self, texts: Sequence[str], locale: str) -> List[str]:
        """Invoke the Translator Text API for a batch of strings."""

        payload = [{"text": text} for text in texts]
        params = {"api-version": "3.0", "to": locale}

        headers = self._build_headers()
        response = self._session.post(
            self._endpoint,
            params=params,
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        translations: List[str] = []
        for item in data:
            translation_candidates = item.get("translations", [])
            translated_text = translation_candidates[0]["text"] if translation_candidates else ""
            translations.append(translated_text)

        if len(translations) != len(texts):
            logger.warning(
                "Translation count mismatch (expected %s, got %s). Using best-effort alignment.",
                len(texts),
                len(translations),
            )

        return translations

    def _build_headers(self) -> Dict[str, str]:
        """Construct authentication headers for Translator requests."""

        headers: Dict[str, str] = {"Content-Type": "application/json"}

        if self.settings.translator_key:
            headers["Ocp-Apim-Subscription-Key"] = self.settings.translator_key
            if self.settings.translator_region:
                headers["Ocp-Apim-Subscription-Region"] = self.settings.translator_region
        else:
            if not self._identity_credential:
                raise RuntimeError(
                    "Managed identity credential unavailable for translator authentication"
                )
            now = datetime.utcnow().timestamp()
            if not self._cached_token or now >= self._cached_token_expiry - 60:
                token = self._identity_credential.get_token(self._COGNITIVE_SCOPE)
                self._cached_token = token.token
                self._cached_token_expiry = float(token.expires_on)
            headers["Authorization"] = f"Bearer {self._cached_token}"

        return headers

    def _upload_text(
        self,
        blob_name: str,
        data: str,
        overwrite: bool,
        *,
        content_type: str,
    ) -> None:
        """Upload textual content to the translations container."""

        blob_client = self._container_client.get_blob_client(blob_name)
        blob_client.upload_blob(
            data.encode("utf-8"),
            overwrite=overwrite,
            content_settings=ContentSettings(content_type=content_type, charset="utf-8"),
        )
        logger.info("Uploaded translation blob: %s", blob_name)

    def _derive_relative_document_path(self, batch_id: str, document: DocumentResult) -> Path:
        """Infer a relative path for translation outputs based on result blob name."""

        blob_path = Path(document.relative_blob_path)
        parts = list(blob_path.parts)

        relative_parts: List[str]
        if batch_id and batch_id in parts:
            batch_index = parts.index(batch_id)
            relative_parts = parts[batch_index + 1 :]
        elif parts:
            relative_parts = parts[1:]
        else:
            relative_parts = [blob_path.name]

        if not relative_parts:
            relative_parts = [blob_path.name]

        relative_path = Path(*relative_parts)
        stem = relative_path.stem
        if stem.endswith("_analysis"):
            stem = stem[:-9]
        return relative_path.with_name(stem)

    def _build_blob_name(self, locale: str, batch_id: str, relative_path: Path) -> str:
        """Create a blob name for a translated Markdown file."""

        safe_parts = [part for part in relative_path.parts if part not in {"", "."}]
        if not safe_parts:
            safe_parts = [relative_path.stem or "document"]

        filename = safe_parts[-1]
        folder_parts = safe_parts[:-1]
        blob_parts = [locale, batch_id, *folder_parts, f"{filename}.md"]
        return "/".join(blob_parts)

    def _build_manifest_blob_name(self, locale: str, batch_id: str) -> str:
        """Return the blob name for a locale manifest."""

        return "/".join([locale, batch_id, self.settings.translation_manifest_filename])

    @staticmethod
    def _utcnow() -> str:
        """Return the current UTC time in ISO-8601 format."""

        return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"