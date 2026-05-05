# Document Processor - Usage Guide

Complete guide for using the Azure Document Intelligence Batch Processor.

## Table of Contents

1. [Installation](#installation)
2. [Configuration](#configuration)
3. [CLI Commands](#cli-commands)
4. [Python API](#python-api)
5. [Workflows](#workflows)
6. [Troubleshooting](#troubleshooting)

## Installation

### Prerequisites

- Python 3.11 or later
- Azure subscription with deployed infrastructure (see QUICKSTART.md)
- Azure CLI or managed identity for authentication

### Install Package

```powershell
# Clone repository
git clone https://github.com/benoit-nguyen/dnv-sng-az-doc-intelligence.git
cd dnv-sng-az-doc-intelligence

# Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install package in development mode
pip install -e .

# Or install from requirements
pip install -r requirements.txt
```

### Verify Installation

```powershell
docprocessor --help
```

## Configuration

### Environment Variables

Copy `.env.template` to `.env` and configure:

```ini
# Azure Configuration
AZURE_SUBSCRIPTION_ID=your-subscription-id
AZURE_RESOURCE_GROUP=rg-doc-intelligence
AZURE_LOCATION=southeastasia

# Document Intelligence
DOCUMENT_INTELLIGENCE_ENDPOINT=https://di-prod-xxxx.cognitiveservices.azure.com/
# DOCUMENT_INTELLIGENCE_KEY=optional-if-using-managed-identity

# Storage Account
STORAGE_ACCOUNT_NAME=stdocintxxxxxx
STORAGE_CONTAINER_SOURCE=source-documents
STORAGE_CONTAINER_RESULTS=results
# STORAGE_CONNECTION_STRING=optional-if-using-managed-identity

# Key Vault
KEY_VAULT_URI=https://kv-di-xxxx.vault.azure.net/

# Processing Options
BATCH_SIZE_LIMIT=10000
SUPPORTED_FORMATS=.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.tiff,.bmp,.html,.txt
MAX_FILE_SIZE_MB=100
PARALLEL_UPLOAD_WORKERS=4

# Blob Upload Configuration
BLOB_MAX_BLOCK_SIZE=4194304
BLOB_MAX_SINGLE_PUT_SIZE=8388608
BLOB_UPLOAD_CONCURRENCY=2

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/docprocessor.log
```

### Authentication

The package uses Azure Managed Identity by default. To authenticate locally:

```powershell
# Login with Azure CLI
az login

# Set default subscription
az account set --subscription "your-subscription-name"
```

## CLI Commands

### scan - Scan Local Folder

Recursively scan a folder for supported documents.

```powershell
# Basic scan
docprocessor scan C:\Documents

# Save results to JSON
docprocessor scan C:\Documents --output scan-results.json

# Non-recursive scan
docprocessor scan C:\Documents --no-recursive
```

**Output:**
- Summary table with file counts and sizes
- List of found documents
- Optional JSON file with detailed results

### upload - Upload Documents

Upload scanned documents to Azure Blob Storage.

```powershell
# Upload from folder (scans automatically)
docprocessor upload --folder C:\Documents

# Upload from previous scan results
docprocessor upload --scan-file scan-results.json

# Overwrite existing files
docprocessor upload --folder C:\Documents --overwrite
```

**Output:**
- Upload progress for each file
- Summary with success/failure counts
- Total bytes uploaded

### analyze - Start Batch Analysis

Submit documents for batch analysis using Document Intelligence.

```powershell
# Start analysis with default model (prebuilt-layout)
docprocessor analyze

# Use specific model
docprocessor analyze --model prebuilt-invoice

# Output format (markdown or text)
docprocessor analyze --format markdown

# Wait for completion
docprocessor analyze --wait

# Custom result prefix
docprocessor analyze --prefix my-results
```

**Output:**
- Operation ID for status tracking
- If `--wait` is used, final status summary

**Supported Models:**
- `prebuilt-layout` - Extract text, tables, structure
- `prebuilt-invoice` - Invoice-specific fields
- `prebuilt-receipt` - Receipt data extraction
- `prebuilt-idDocument` - ID card/passport data
- Custom models (if trained)

### status - Check Analysis Status

Check the status of a running batch operation.

```powershell
docprocessor status <operation-id>
```

**Example:**
```powershell
docprocessor status "12345678-1234-1234-1234-123456789012"
```

**Output:**
- Current status (notStarted, running, succeeded, failed)
- Progress (succeeded/failed/total counts)
- Success rate percentage
- Timestamps

### download - Download Results

Download and export analysis results.

```powershell
# Download all results
docprocessor download --output ./results

# Custom result prefix
docprocessor download --prefix my-results --output ./my-results

# Skip Markdown export
docprocessor download --no-markdown

# Skip CSV export
docprocessor download --no-csv
```

**Output Directory Structure:**
```
results/
├── json/              # Raw JSON results
│   ├── document1.json
│   └── document2.json
├── markdown/          # Markdown exports
│   ├── document1.md
│   └── document2.md
└── tables/            # Extracted tables as CSV
    ├── document1_table_1.csv
    └── document2_table_1.csv
```

### translate - Generate Translations

Translate analyzed documents to one or more locales and store the outputs in blob storage.

```powershell
# Translate the latest analysis batch using defaults from configuration
docprocessor translate

# Translate a specific result prefix into French and Korean
docprocessor translate --prefix results --locales fr-FR,ko-KR

# Force regeneration of translation manifests and markdown
docprocessor translate --overwrite
```

**Output:**
- Progress for each locale and document
- Markdown translations uploaded to the translations container
- Manifest files per locale for downstream processing

### run - Complete Pipeline

Execute the entire processing pipeline in one command.

```powershell
# Run complete pipeline
docprocessor run C:\Documents

# With custom options
docprocessor run C:\Documents \
  --model prebuilt-layout \
  --output ./results \
  --wait
```

**Steps Executed:**
1. Scan folder for documents
2. Upload to blob storage
3. Start batch analysis
4. Poll for completion (if `--wait`)
5. Download and export results

## Python API

### Using Individual Modules

#### Scanner

```python
from docprocessor.scanner import DocumentScanner

# Create scanner
scanner = DocumentScanner()

# Scan folder
result = scanner.scan_folder(r"C:\Documents")

# Access results
print(f"Found {result.valid_documents} documents")
print(f"Total size: {result.total_size_mb:.2f} MB")

# Save to file
result.save_to_file("scan-results.json")

# Load from file
loaded_result = scanner.load_from_file("scan-results.json")
```

#### Uploader

```python
from docprocessor.uploader import BlobUploader
from docprocessor.scanner import DocumentScanner

# Scan documents
scanner = DocumentScanner()
scan_result = scanner.scan_folder(r"C:\Documents")

# Upload with progress tracking
def progress(filename, current, total):
    print(f"[{current}/{total}] {filename}")

with BlobUploader(progress_callback=progress) as uploader:
    upload_result = uploader.upload_documents(
        scan_result.documents,
        overwrite=False
    )
    
    print(f"Uploaded: {upload_result.successful}/{upload_result.total_files}")
```

#### Analyzer

```python
from docprocessor.analyzer import DocumentIntelligenceAnalyzer

with DocumentIntelligenceAnalyzer() as analyzer:
    # Start batch analysis
    operation_id = analyzer.start_batch_analysis(
        model_id="prebuilt-layout",
        output_format="markdown"
    )
    
    print(f"Operation ID: {operation_id}")
    
    # Poll for completion
    result = analyzer.poll_batch_completion(
        operation_id,
        polling_interval=30,
        timeout_minutes=60
    )
    
    print(f"Status: {result.status.value}")
    print(f"Success rate: {result.success_rate:.1f}%")
```

#### Processor

```python
from docprocessor.processor import ResultsProcessor
from pathlib import Path

with ResultsProcessor() as processor:
    # Download all results
    results = processor.batch_download_results(
        result_prefix="results",
        output_dir=Path("./raw-results")
    )
    
    # Export each result
    for doc_result in results:
        # To Markdown
        md_file = Path(f"./markdown/{doc_result.source_file}.md")
        processor.export_to_markdown(doc_result, md_file)
        if doc_result.tables:
            csv_files = processor.export_tables_to_csv(
                doc_result,
                Path("./tables")
            )
```

#### Translation Pipeline

```python
from docprocessor.translation import TranslationPipeline

pipeline = TranslationPipeline()
results = processor.load_cached_results(prefix="results")

# Translate to German and Japanese without overwriting existing blobs
## Workflows

### Workflow 1: One-Time Batch Processing

Process a folder of documents once:

```powershell
# Complete pipeline
docprocessor run C:\MyDocuments --wait --output C:\Results
```

### Workflow 2: Staged Processing

Process in stages with checkpoints:

```powershell
# Stage 1: Scan and save
docprocessor scan C:\MyDocuments --output scan.json

# Stage 2: Review scan results, then upload
docprocessor upload --scan-file scan.json

# Stage 3: Start analysis
docprocessor analyze --wait

# Stage 4: Download when ready
docprocessor download --output C:\Results
```

### Workflow 3: Continuous Monitoring

For long-running jobs:

```powershell
# Start analysis
$op_id = docprocessor analyze | Select-String "Operation ID: (.*)" | % {$_.Matches.Groups[1].Value}

# Check periodically
while ($true) {
    docprocessor status $op_id
    Start-Sleep -Seconds 60
}

# Download when complete
docprocessor download --output C:\Results
```

### Workflow 4: Python Script Integration

```python
from pathlib import Path
from docprocessor.scanner import DocumentScanner
from docprocessor.uploader import BlobUploader
from docprocessor.analyzer import DocumentIntelligenceAnalyzer
from docprocessor.processor import ResultsProcessor

def process_documents(input_folder: Path, output_folder: Path):
    """Complete document processing pipeline."""
    
    # 1. Scan
    scanner = DocumentScanner()
    scan_result = scanner.scan_folder(input_folder)
    print(f"Found {scan_result.valid_documents} documents")
    
    # 2. Upload
    with BlobUploader() as uploader:
        upload_result = uploader.upload_documents(scan_result.documents)
        print(f"Uploaded {upload_result.successful} files")
    
    # 3. Analyze
    with DocumentIntelligenceAnalyzer() as analyzer:
        operation_id = analyzer.start_batch_analysis()
        result = analyzer.poll_batch_completion(operation_id)
        print(f"Analysis complete: {result.success_rate:.1f}% success")
    
    # 4. Download and export
    with ResultsProcessor() as processor:
        results = processor.batch_download_results()
        
        for doc_result in results:
            md_file = output_folder / "markdown" / f"{doc_result.source_file}.md"
            processor.export_to_markdown(doc_result, md_file)
        
        print(f"Exported {len(results)} results to {output_folder}")

# Run
process_documents(Path("C:\\Documents"), Path("C:\\Results"))
```

### Workflow 5: Translation Publication

Translate completed analysis results and push localized markdown back to storage.

```powershell
# Ensure analysis has finished and results are downloaded
docprocessor download --output C:\Results

# Generate translations for configured locales
docprocessor translate --prefix results --locales en-US,zh-CN,ko-KR
```

**Tips:**
- Configure `TRANSLATION_CONTAINER` and default locales in `.env` beforehand
- Use `--overwrite` when re-running after manual edits to manifests

## Troubleshooting

### Authentication Errors

**Error:** `DefaultAzureCredential failed to retrieve a token`

**Solutions:**
1. Login with Azure CLI: `az login`
2. Set correct subscription: `az account set --subscription <name>`
3. Verify managed identity has proper RBAC roles

### Upload Failures

**Error:** `Blob already exists`

**Solution:** Use `--overwrite` flag or delete existing blobs

**Error:** `File too large`

**Solution:** Adjust `MAX_FILE_SIZE_MB` in .env

### Analysis Errors

**Error:** `Batch operation failed`

**Check:**
1. Verify Document Intelligence endpoint is correct
2. Check blob container permissions
3. Review failed document list in status output

### Download Issues

**Error:** `No results found`

**Check:**
1. Verify batch analysis has completed
2. Check result container name matches configuration
3. Ensure result prefix is correct

### Performance Optimization

**Slow uploads:**
- Increase `PARALLEL_UPLOAD_WORKERS` (default: 4, try 8-10)
- Adjust `BLOB_UPLOAD_CONCURRENCY` (default: 2)

**Timeout during polling:**
- Increase timeout in `poll_batch_completion()` 
- Default: 60 minutes, increase for large batches

## Best Practices

1. **Scan Before Upload**: Always scan first to validate documents
2. **Save Scan Results**: Use `--output` to save scan results for later
3. **Monitor Large Batches**: For 1000+ documents, monitor status separately
4. **Use Managed Identity**: Avoid API keys in production
5. **Test with Subset**: Test with small batch first
6. **Clean Up**: Remove old results from blob storage periodically

## Cost Optimization

- **Free Tier**: Use F0 SKU for development (500 pages/month)
- **Batching**: Process in batches of 1000-5000 documents
- **Storage**: Use Cool tier for long-term result storage
- **Monitoring**: Enable cost alerts in Azure portal

## Support

For issues or questions:
- Check documentation: [Azure Document Intelligence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/)
- Review specification: `az-intel-specs/001-batch-document-processor.md`
- Open GitHub issue (if applicable)
