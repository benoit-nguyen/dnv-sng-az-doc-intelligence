# Deploy Azure Infrastructure for Document Intelligence Batch Processor
# This script deploys all required Azure resources using Bicep

param(
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroupName = "rg-doc-intelligence",
    
    [Parameter(Mandatory=$false)]
    [string]$Location = "southeastasia",
    
    [Parameter(Mandatory=$false)]
    [ValidateSet("dev", "test", "prod")]
    [string]$Environment = "dev",
    
    [Parameter(Mandatory=$false)]
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Azure Document Intelligence Deployment" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Check if Azure CLI is installed
try {
    $azVersion = az version --output json | ConvertFrom-Json
    Write-Host "✓ Azure CLI version: $($azVersion.'azure-cli')" -ForegroundColor Green
} catch {
    Write-Host "✗ Azure CLI not found. Please install from: https://aka.ms/azure-cli" -ForegroundColor Red
    exit 1
}

# Check if logged in
Write-Host ""
Write-Host "Checking Azure login status..." -ForegroundColor Yellow
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "Not logged in to Azure. Starting login..." -ForegroundColor Yellow
    az login
    $account = az account show | ConvertFrom-Json
}

Write-Host "✓ Logged in as: $($account.user.name)" -ForegroundColor Green
Write-Host "✓ Subscription: $($account.name) ($($account.id))" -ForegroundColor Green

# Create resource group if it doesn't exist
Write-Host ""
Write-Host "Creating resource group if needed..." -ForegroundColor Yellow
$rgExists = az group exists --name $ResourceGroupName
if ($rgExists -eq "false") {
    Write-Host "Creating resource group: $ResourceGroupName" -ForegroundColor Yellow
    az group create --name $ResourceGroupName --location $Location | Out-Null
    Write-Host "✓ Resource group created" -ForegroundColor Green
} else {
    Write-Host "✓ Resource group already exists" -ForegroundColor Green
}

# Deploy Bicep template
Write-Host ""
Write-Host "Deploying Azure resources..." -ForegroundColor Yellow
Write-Host "  Location: $Location" -ForegroundColor Gray
Write-Host "  Environment: $Environment" -ForegroundColor Gray
Write-Host ""

$deploymentName = "docprocessor-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$bicepFile = Join-Path $PSScriptRoot "main.bicep"
$parametersFile = Join-Path $PSScriptRoot "main.parameters.json"

if ($WhatIf) {
    Write-Host "Running What-If deployment..." -ForegroundColor Yellow
    az deployment group what-if `
        --resource-group $ResourceGroupName `
        --template-file $bicepFile `
        --parameters $parametersFile `
        --parameters location=$Location environment=$Environment
} else {
    $deployment = az deployment group create `
        --resource-group $ResourceGroupName `
        --name $deploymentName `
        --template-file $bicepFile `
        --parameters $parametersFile `
        --parameters location=$Location environment=$Environment `
        --output json | ConvertFrom-Json
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Deployment successful!" -ForegroundColor Green
        
        # Extract outputs
        Write-Host ""
        Write-Host "==========================================" -ForegroundColor Cyan
        Write-Host " Deployment Outputs" -ForegroundColor Cyan
        Write-Host "==========================================" -ForegroundColor Cyan
        Write-Host ""
        
        $outputs = $deployment.properties.outputs
        
        Write-Host "Storage Account:" -ForegroundColor Yellow
        Write-Host "  Name: $($outputs.storageAccountName.value)" -ForegroundColor White
        Write-Host "  Blob Endpoint: $($outputs.storageAccountBlobEndpoint.value)" -ForegroundColor White
        Write-Host "  Source Container: $($outputs.sourceContainerName.value)" -ForegroundColor White
        Write-Host "  Results Container: $($outputs.resultsContainerName.value)" -ForegroundColor White
        Write-Host ""
        
        Write-Host "Document Intelligence:" -ForegroundColor Yellow
        Write-Host "  Name: $($outputs.documentIntelligenceName.value)" -ForegroundColor White
        Write-Host "  Endpoint: $($outputs.documentIntelligenceEndpoint.value)" -ForegroundColor White
        Write-Host ""
        
        Write-Host "Key Vault:" -ForegroundColor Yellow
        Write-Host "  Name: $($outputs.keyVaultName.value)" -ForegroundColor White
        Write-Host "  URI: $($outputs.keyVaultUri.value)" -ForegroundColor White
        Write-Host ""
        
        Write-Host "Managed Identity:" -ForegroundColor Yellow
        Write-Host "  Name: $($outputs.managedIdentityName.value)" -ForegroundColor White
        Write-Host "  Client ID: $($outputs.managedIdentityClientId.value)" -ForegroundColor White
        Write-Host ""
        
        # Generate .env file
        Write-Host "Generating .env file..." -ForegroundColor Yellow
        $envPath = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
        
        $envContent = @"
# Azure Configuration
AZURE_SUBSCRIPTION_ID=$($outputs.subscriptionId.value)
AZURE_RESOURCE_GROUP=$($outputs.resourceGroupName.value)
AZURE_LOCATION=$Location

# Document Intelligence Service
DOCUMENT_INTELLIGENCE_ENDPOINT=$($outputs.documentIntelligenceEndpoint.value)
DOCUMENT_INTELLIGENCE_KEY=

# Storage Account
STORAGE_ACCOUNT_NAME=$($outputs.storageAccountName.value)
STORAGE_CONTAINER_SOURCE=$($outputs.sourceContainerName.value)
STORAGE_CONTAINER_RESULTS=$($outputs.resultsContainerName.value)
STORAGE_CONNECTION_STRING=

# Key Vault
KEY_VAULT_NAME=$($outputs.keyVaultName.value)
KEY_VAULT_URI=$($outputs.keyVaultUri.value)

# Processing Configuration
BATCH_SIZE_LIMIT=10000
SUPPORTED_FORMATS=.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.tiff,.bmp,.html,.txt
MAX_FILE_SIZE_MB=100
PARALLEL_UPLOAD_WORKERS=4

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/docprocessor.log

# Retry Configuration
RETRY_MAX_ATTEMPTS=3
RETRY_BACKOFF_FACTOR=2
RETRY_INITIAL_WAIT_SECONDS=1
"@
        
        $envContent | Out-File -FilePath $envPath -Encoding utf8 -Force
        Write-Host "✓ .env file created at: $envPath" -ForegroundColor Green
        
        # Summary
        Write-Host ""
        Write-Host "==========================================" -ForegroundColor Cyan
        Write-Host " Next Steps" -ForegroundColor Cyan
        Write-Host "==========================================" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "1. Review the .env file and update if needed" -ForegroundColor White
        Write-Host "2. Install Python dependencies:" -ForegroundColor White
        Write-Host "   pip install -r requirements.txt" -ForegroundColor Gray
        Write-Host ""
        Write-Host "3. Test the scanner:" -ForegroundColor White
        Write-Host "   python -m docprocessor.scanner <folder-path>" -ForegroundColor Gray
        Write-Host ""
        Write-Host "4. Start processing documents!" -ForegroundColor White
        Write-Host ""
        Write-Host "✓ Deployment complete!" -ForegroundColor Green
    } else {
        Write-Host "✗ Deployment failed. Check error messages above." -ForegroundColor Red
        exit 1
    }
}
