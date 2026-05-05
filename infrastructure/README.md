# Azure Infrastructure Deployment Guide

This directory contains Bicep templates for deploying the Azure infrastructure required for the Document Intelligence Batch Processor.

## Resources Deployed

- **Azure Document Intelligence** (Cognitive Services) - For document analysis
- **Azure Storage Account** - For storing source documents and results
- **Azure Key Vault** - For secure credential management
- **User Assigned Managed Identity** - For secure authentication between services

## Prerequisites

- Azure CLI installed (version 2.50.0 or later)
- Azure subscription with appropriate permissions
- Bicep CLI (included with Azure CLI 2.20.0+)

## Deployment Steps

### 1. Login to Azure

```powershell
az login
az account set --subscription "<your-subscription-id>"
```

### 2. Create Resource Group

```powershell
$resourceGroupName = "rg-doc-intelligence"
$location = "eastus"

az group create `
  --name $resourceGroupName `
  --location $location
```

### 3. Deploy Infrastructure

#### Option A: Using Default Parameters

```powershell
az deployment group create `
  --resource-group $resourceGroupName `
  --template-file main.bicep `
  --parameters main.parameters.json
```

#### Option B: Override Parameters

```powershell
az deployment group create `
  --resource-group $resourceGroupName `
  --template-file main.bicep `
  --parameters main.parameters.json `
  --parameters environment=prod documentIntelligenceSku=S0
```

#### Option C: What-If Deployment (Dry Run)

```powershell
az deployment group what-if `
  --resource-group $resourceGroupName `
  --template-file main.bicep `
  --parameters main.parameters.json
```

### 4. Capture Deployment Outputs

```powershell
$outputs = az deployment group show `
  --resource-group $resourceGroupName `
  --name main `
  --query properties.outputs `
  --output json | ConvertFrom-Json

# Extract key values
$storageAccountName = $outputs.storageAccountName.value
$documentIntelligenceEndpoint = $outputs.documentIntelligenceEndpoint.value
$keyVaultUri = $outputs.keyVaultUri.value
$managedIdentityClientId = $outputs.managedIdentityClientId.value

# Display
Write-Host "Storage Account: $storageAccountName"
Write-Host "Document Intelligence Endpoint: $documentIntelligenceEndpoint"
Write-Host "Key Vault URI: $keyVaultUri"
Write-Host "Managed Identity Client ID: $managedIdentityClientId"
```

### 5. Update .env File

Create or update your `.env` file in the project root:

```powershell
@"
AZURE_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
AZURE_RESOURCE_GROUP=$resourceGroupName
AZURE_LOCATION=$location

DOCUMENT_INTELLIGENCE_ENDPOINT=$documentIntelligenceEndpoint
DOCUMENT_INTELLIGENCE_KEY=

STORAGE_ACCOUNT_NAME=$storageAccountName
STORAGE_CONTAINER_SOURCE=source-documents
STORAGE_CONTAINER_RESULTS=results
STORAGE_CONNECTION_STRING=

KEY_VAULT_NAME=$($outputs.keyVaultName.value)
KEY_VAULT_URI=$keyVaultUri
"@ | Out-File -FilePath ..\.env -Encoding utf8
```

## Resource Naming Convention

Resources are named using the following pattern:

- Storage Account: `stdocint{uniqueString}`
- Document Intelligence: `di-{environment}-{uniqueString}`
- Key Vault: `kv-di-{uniqueString}`
- Managed Identity: `id-docprocessor-{environment}`

Where `{uniqueString}` is generated from the resource group ID to ensure uniqueness.

## Parameters

| Parameter | Description | Default | Allowed Values |
|-----------|-------------|---------|----------------|
| `location` | Azure region for resources | Resource group location | Any Azure region |
| `environment` | Environment name | `dev` | `dev`, `test`, `prod` |
| `storageSku` | Storage account SKU | `Standard_LRS` | `Standard_LRS`, `Standard_GRS`, `Standard_ZRS` |
| `documentIntelligenceSku` | Document Intelligence SKU | `S0` | `F0` (free), `S0` (standard) |
| `sourceContainerName` | Source container name | `source-documents` | Any valid container name |
| `resultsContainerName` | Results container name | `results` | Any valid container name |

## Security Configuration

### Managed Identity

All resources are configured to use **User Assigned Managed Identity** for authentication:

- **Storage Blob Data Contributor** role assigned to managed identity on storage account
- **Cognitive Services User** role assigned to managed identity on Document Intelligence
- **Key Vault Secrets User** role assigned to managed identity on Key Vault

### Storage Account

- Public blob access: **Disabled**
- Shared key access: **Disabled** (enforces managed identity)
- Minimum TLS version: **1.2**
- HTTPS only: **Enabled**

### Key Vault

- RBAC authorization: **Enabled**
- Soft delete: **Enabled** (7-day retention)
- Public network access: **Enabled** (restrict via network rules if needed)

### Document Intelligence

- Custom subdomain: **Enabled** (required for managed identity)
- Public network access: **Enabled**
- Network ACLs: Allow Azure Services

## Outputs

After successful deployment, the following outputs are available:

| Output | Description |
|--------|-------------|
| `storageAccountName` | Storage account name |
| `storageAccountBlobEndpoint` | Blob storage endpoint URL |
| `sourceContainerName` | Source container name |
| `resultsContainerName` | Results container name |
| `documentIntelligenceName` | Document Intelligence resource name |
| `documentIntelligenceEndpoint` | Document Intelligence endpoint URL |
| `keyVaultName` | Key Vault name |
| `keyVaultUri` | Key Vault URI |
| `managedIdentityName` | Managed identity name |
| `managedIdentityClientId` | Client ID for authentication |
| `managedIdentityPrincipalId` | Principal ID for role assignments |

## Cost Estimation

### Development Environment (F0/S0 Tiers)

- Document Intelligence S0: ~$1.50 per 1000 pages
- Storage Account (LRS): ~$0.02 per GB/month + transactions
- Key Vault: ~$0.03 per 10,000 operations
- Managed Identity: Free

**Estimated monthly cost**: $5-50 depending on usage

### Production Environment

Adjust SKUs in `main.parameters.json` for production:

```json
{
  "documentIntelligenceSku": {"value": "S0"},
  "storageSku": {"value": "Standard_GRS"}
}
```

## Troubleshooting

### Deployment Failures

```powershell
# View deployment status
az deployment group show `
  --resource-group $resourceGroupName `
  --name main

# View deployment operation details
az deployment operation group list `
  --resource-group $resourceGroupName `
  --name main
```

### Permission Issues

Ensure your account has the following permissions:
- **Owner** or **Contributor** role on the resource group
- **User Access Administrator** role for role assignments

### Managed Identity Not Working

```powershell
# Verify role assignments
az role assignment list `
  --assignee $managedIdentityPrincipalId `
  --output table
```

## Cleanup

To delete all deployed resources:

```powershell
az group delete --name $resourceGroupName --yes --no-wait
```

## Next Steps

After deployment:

1. Update `.env` file with output values
2. Test storage connectivity: `az storage blob list`
3. Test Document Intelligence: Use Azure Portal or SDK
4. Set up local development environment
5. Run first batch processing job

## Additional Resources

- [Azure Document Intelligence Documentation](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/)
- [Bicep Documentation](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/)
- [Managed Identity Best Practices](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/overview)
