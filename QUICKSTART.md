# Quick Start Guide - Phase 1 MVP

Get up and running with the Document Intelligence Batch Processor in minutes.

## Prerequisites

- **Python 3.11+** installed
- **Azure CLI** installed ([Download](https://aka.ms/azure-cli))
- **Azure Subscription** with appropriate permissions
- **PowerShell 7+** (for deployment script)

## Step 1: Clone the Repository

```powershell
git clone https://github.com/benoit-nguyen/dnv-sng-az-doc-intelligence.git
cd dnv-sng-az-doc-intelligence
```

## Step 2: Deploy Azure Infrastructure

```powershell
# Navigate to infrastructure directory
cd infrastructure

# Run deployment (will create resources in Azure)
.\deploy.ps1 -ResourceGroupName "rg-doc-intelligence" -Location "eastus" -Environment "dev"

# Or run a dry-run first
.\deploy.ps1 -WhatIf
```

The deployment script will:
- ✅ Create Azure Resource Group
- ✅ Deploy Document Intelligence service
- ✅ Deploy Storage Account with containers
- ✅ Deploy Key Vault
- ✅ Create Managed Identity with appropriate permissions
- ✅ Generate `.env` file with configuration

**Estimated time**: 3-5 minutes

## Step 3: Set Up Python Environment

```powershell
# Return to project root
cd ..

# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

## Step 4: Verify Installation

```powershell
# Test configuration loading
python -c "from docprocessor.config import get_settings; s = get_settings(); print(f'Config loaded: {s.storage_account_name}')"
```

## Step 5: Scan Your First Folder

```powershell
# Scan a folder for documents
python -c "from docprocessor.scanner import DocumentScanner; scanner = DocumentScanner(); result = scanner.scan_folder('C:\\Documents'); print(f'Found {result.supported_files} documents')"
```

## What's Included in Phase 1 (MVP)

✅ **Azure Infrastructure**
- Document Intelligence (S0 tier)
- Storage Account with source/results containers
- Key Vault for secrets
- Managed Identity with RBAC roles

✅ **Python Application Structure**
- Configuration management
- Document scanner module
- Project organization

✅ **Documentation**
- README and specifications
- Infrastructure deployment guide
- Constitution and guidelines

## Coming Next (Phase 1 Completion)

The following modules are in progress:

⏳ **Blob Storage Uploader** - Upload scanned files to Azure
⏳ **Batch Analyzer** - Call Document Intelligence batch API
⏳ **Results Processor** - Download and parse results
⏳ **CLI Interface** - Command-line tools for all operations
⏳ **Testing Suite** - Integration tests

## Quick Reference

### Environment Variables

Key variables in your `.env` file:

```ini
DOCUMENT_INTELLIGENCE_ENDPOINT=https://...
STORAGE_ACCOUNT_NAME=stdocint...
STORAGE_CONTAINER_SOURCE=source-documents
STORAGE_CONTAINER_RESULTS=results
KEY_VAULT_URI=https://kv-di-...
SUPPORTED_FORMATS=.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.tiff,.bmp,.html,.txt
```

### Resource Naming

- Storage: `stdocint{uniqueString}`
- Document Intelligence: `di-{env}-{uniqueString}`
- Key Vault: `kv-di-{uniqueString}`
- Managed Identity: `id-docprocessor-{env}`

### Useful Commands

```powershell
# Check Azure resources
az resource list --resource-group rg-doc-intelligence --output table

# View Key Vault secrets
az keyvault secret list --vault-name <your-kv-name> --output table

# Test storage access
az storage blob list --account-name <your-storage-account> --container-name source-documents --auth-mode login
```

## Troubleshooting

### Deployment Fails

```powershell
# Check deployment status
az deployment group show --resource-group rg-doc-intelligence --name <deployment-name>

# View detailed errors
az deployment operation group list --resource-group rg-doc-intelligence --name <deployment-name>
```

### Permission Issues

Ensure your account has:
- **Owner** or **Contributor** on the resource group
- **User Access Administrator** for role assignments

### Python Import Errors

```powershell
# Ensure virtual environment is activated
.\.venv\Scripts\Activate.ps1

# Reinstall dependencies
pip install --force-reinstall -r requirements.txt
```

## Cost Management

**Phase 1 MVP costs** (estimate):
- Document Intelligence S0: ~$1.50 per 1000 pages
- Storage: ~$0.02/GB/month
- Key Vault: ~$0.03 per 10K operations
- **Total**: $5-20/month for development

### Cost Optimization Tips

1. Use F0 (Free) tier for Document Intelligence during testing:
   ```powershell
   # In main.parameters.json
   "documentIntelligenceSku": {"value": "F0"}
   ```

2. Delete resources when not in use:
   ```powershell
   az group delete --name rg-doc-intelligence --yes
   ```

## Next Steps

1. ✅ Complete Phase 1 implementation (uploader, analyzer, processor)
2. 📝 Review specification: `az-intel-specs/001-batch-document-processor.md`
3. 🧪 Run integration tests
4. 🚀 Process your first batch!

## Support

- **Issues**: Create an issue in this repository
- **Documentation**: See `/docs` folder
- **Specification**: `az-intel-specs/001-batch-document-processor.md`

---

**Last Updated**: 2025-11-12  
**Phase**: 1 (MVP) - In Progress
