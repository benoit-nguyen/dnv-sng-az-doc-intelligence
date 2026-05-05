# Azure Document Intelligence Batch Processor

A comprehensive solution for batch processing large datasets of documents using Azure Document Intelligence, with support for translation, visualization, and summarization.

## Features

- **Batch Document Analysis**: Process up to 10,000 documents in a single batch
- **Multi-Format Support**: PDF, DOCX, XLSX, PPTX, images, HTML, TXT
- **Format Preservation**: Maintain layout, tables, and formatting during processing
- **Translation**: Multi-language translation with Azure AI Translator via `docprocessor translate`
- **Visualization**: Interactive dashboard for monitoring and results
- **Intelligent Extraction**: Extract text, tables, structure, entities

## Project Status

✅ **Phase 1 (MVP) - Complete**

- [x] Specification created
- [x] Infrastructure setup
- [x] Core processing engine
- [x] CLI interface
- [ ] Testing suite

## Architecture

```
Local Folder → Azure Blob Storage → Document Intelligence Batch API → Results Processing
```

### Technology Stack

- **Language**: Python 3.11+
- **Azure Services**:
  - Azure Document Intelligence (Layout Model)
  - Azure Blob Storage
  - Azure Key Vault
  - Azure Functions (orchestration)
- **CLI**: Typer/Click
- **Authentication**: Managed Identity

## Prerequisites

- Python 3.11 or higher
- Azure subscription
- Azure CLI installed and configured
- Git

## Quick Start

### 1. Clone the Repository

```powershell
git clone https://github.com/benoit-nguyen/dnv-sng-az-doc-intelligence.git
cd dnv-sng-az-doc-intelligence
```

### 2. Set Up Azure Resources

```powershell
# Login to Azure
az login

# Set your subscription
az account set --subscription "your-subscription-id"

# Deploy infrastructure
cd infrastructure
az deployment group create `
  --resource-group rg-doc-intelligence `
  --template-file main.bicep `
  --parameters main.parameters.json
```

### 3. Configure Environment

```powershell
# Copy template
cp .env.template .env

# Edit .env with your Azure resource details
notepad .env
```

### 4. Install Dependencies

```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 5. Run the Processor

```powershell
# Scan a folder
python -m docprocessor scan --folder "C:\Documents" --output scan-results.json

# Upload to blob storage
python -m docprocessor upload --scan-file scan-results.json

# Start batch analysis
python -m docprocessor analyze --batch-id <batch-id>

# Check status
python -m docprocessor status --batch-id <batch-id>

# Download results
python -m docprocessor download --prefix <batch-id> --output results/

# Translate results to configured locales (Phase 2)
python -m docprocessor translate --batch-id <batch-id>
```

## Project Structure

```
dnv-sng-az-doc-intelligence/
├── az-intel-specs/              # Specifications
│   └── 001-batch-document-processor.md
├── docprocessor/                # Main application package
│   ├── __init__.py
│   ├── cli.py                   # CLI interface
│   ├── scanner.py               # Folder scanner
│   ├── uploader.py              # Blob storage uploader
│   ├── analyzer.py              # Document Intelligence client
│   ├── processor.py             # Results processor
│   └── config.py                # Configuration management
├── infrastructure/              # Azure infrastructure (Bicep)
│   ├── main.bicep
│   ├── main.parameters.json
│   └── modules/
│       ├── document-intelligence.bicep
│       ├── storage.bicep
│       └── key-vault.bicep
├── tests/                       # Test suite
│   ├── test_scanner.py
│   ├── test_uploader.py
│   └── test_analyzer.py
├── .env.template                # Environment variables template
├── requirements.txt             # Python dependencies
├── README.md
└── speckit.constitution.md      # Project constitution
```

## Configuration

### Environment Variables

Create a `.env` file based on `.env.template`:

```ini
# Azure Configuration
AZURE_SUBSCRIPTION_ID=your-subscription-id
AZURE_RESOURCE_GROUP=rg-doc-intelligence
AZURE_LOCATION=eastus

# Document Intelligence
DOCUMENT_INTELLIGENCE_ENDPOINT=https://your-endpoint.cognitiveservices.azure.com/
DOCUMENT_INTELLIGENCE_KEY=your-key-or-use-managed-identity

# Storage Account
STORAGE_ACCOUNT_NAME=stdocintel
STORAGE_CONTAINER_SOURCE=source-documents
STORAGE_CONTAINER_RESULTS=results

# Key Vault
KEY_VAULT_NAME=kv-doc-intel
KEY_VAULT_URI=https://kv-doc-intel.vault.azure.net/

# Processing Options
BATCH_SIZE_LIMIT=10000
SUPPORTED_FORMATS=.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.tiff,.bmp,.html,.txt
```

## Development

### Running Tests

```powershell
# Run all tests
pytest

# Run with coverage
pytest --cov=docprocessor --cov-report=html

# Run specific test file
pytest tests/test_scanner.py
```

### Code Quality

```powershell
# Format code
black docprocessor/

# Lint
flake8 docprocessor/

# Type checking
mypy docprocessor/
```

## Documentation

- [Specification](az-intel-specs/001-batch-document-processor.md)
- [Constitution](speckit.constitution.md)
- [Azure Document Intelligence Documentation](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/)
- [Batch Analysis API](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/batch-analysis)

## Roadmap

### Phase 1: MVP (Complete)
- ✅ Specification
- ✅ Infrastructure setup
- ✅ Folder scanning and upload
- ✅ Batch analysis integration
- ✅ Results retrieval
- ✅ CLI interface

### Phase 2: Translation & Visualization
- Document translation
- Web dashboard
- Side-by-side comparison
- Table export (CSV/Excel)

### Phase 3: Intelligence Layer
- Azure OpenAI summarization
- Entity extraction
- Folder monitoring
- Advanced analytics

### Phase 4: Enterprise Features
- Custom model training
- API endpoints
- RBAC
- Multi-tenancy

## Contributing

1. Follow the [speckit.constitution.md](speckit.constitution.md)
2. Write tests first (TDD approach)
3. Create feature branch from `main`
4. Submit pull request with specification reference

## License

Copyright © 2025 DNV. All rights reserved.

## Support

For issues and questions:
- Create an issue in this repository
- Contact: [Your Team Email]

## Acknowledgments

- Azure Document Intelligence team for the batch API
- Azure SDK for Python team

---

**Last Updated**: 2025-11-12  
**Version**: 0.1.0 (Phase 1 - In Development)
