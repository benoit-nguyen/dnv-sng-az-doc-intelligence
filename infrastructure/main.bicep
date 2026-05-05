// Main Bicep template for Document Intelligence Batch Processor
// Deploys: Document Intelligence, Storage Account, Key Vault, Managed Identity

targetScope = 'resourceGroup'

@description('Primary location for all resources')
param location string = resourceGroup().location

@description('Environment name (dev, test, prod)')
@allowed(['dev', 'test', 'prod'])
param environment string = 'dev'

@description('Unique suffix for resource naming')
param resourceSuffix string = uniqueString(resourceGroup().id)

@description('Tags to apply to all resources')
param tags object = {
  Project: 'DocumentIntelligence'
  ManagedBy: 'Bicep'
  Environment: environment
}

// Storage Configuration
@description('Storage account SKU')
@allowed(['Standard_LRS', 'Standard_GRS', 'Standard_ZRS'])
param storageSku string = 'Standard_LRS'

@description('Source container name for documents')
param sourceContainerName string = 'source-documents'

@description('Results container name for processed documents')
param resultsContainerName string = 'results'

// Document Intelligence Configuration
@description('Document Intelligence SKU')
@allowed(['F0', 'S0'])
param documentIntelligenceSku string = 'S0'

// Variables
var storageAccountName = 'stdocint${resourceSuffix}'
var documentIntelligenceName = 'di-${environment}-${resourceSuffix}'
var keyVaultName = 'kv-di-${resourceSuffix}'
var userAssignedIdentityName = 'id-docprocessor-${environment}'

// Role IDs (Azure built-in roles)
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

//////////////////////////////////////////////
// Resources
//////////////////////////////////////////////

// User Assigned Managed Identity
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: userAssignedIdentityName
  location: location
  tags: tags
}

// Storage Account
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: storageSku
  }
  tags: tags
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false  // Enforce managed identity authentication
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
    publicNetworkAccess: 'Enabled'
  }

  resource blobServices 'blobServices' = {
    name: 'default'
    
    resource sourceContainer 'containers' = {
      name: sourceContainerName
      properties: {
        publicAccess: 'None'
      }
    }
    
    resource resultsContainer 'containers' = {
      name: resultsContainerName
      properties: {
        publicAccess: 'None'
      }
    }
  }
}

// Role Assignment: Storage Blob Data Contributor to Managed Identity
resource storageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, managedIdentity.id, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Document Intelligence (Cognitive Services Account)
resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: documentIntelligenceName
  location: location
  kind: 'FormRecognizer'
  sku: {
    name: documentIntelligenceSku
  }
  tags: tags
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    customSubDomainName: documentIntelligenceName
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    disableLocalAuth: false  // Allow both key and managed identity auth
    apiProperties: {}
  }
}

// Role Assignment: Cognitive Services User to Managed Identity
resource cognitiveServicesRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(documentIntelligence.id, managedIdentity.id, cognitiveServicesUserRoleId)
  scope: documentIntelligence
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true  // Use RBAC instead of access policies
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// Role Assignment: Key Vault Secrets User to Managed Identity
resource keyVaultRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, managedIdentity.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Store Document Intelligence endpoint and key in Key Vault
resource documentIntelligenceEndpointSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'document-intelligence-endpoint'
  properties: {
    value: documentIntelligence.properties.endpoint
  }
}

resource documentIntelligenceKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'document-intelligence-key'
  properties: {
    value: documentIntelligence.listKeys().key1
  }
}

//////////////////////////////////////////////
// Outputs
//////////////////////////////////////////////

@description('Storage account name')
output storageAccountName string = storageAccount.name

@description('Storage account blob endpoint')
output storageAccountBlobEndpoint string = storageAccount.properties.primaryEndpoints.blob

@description('Source container name')
output sourceContainerName string = sourceContainerName

@description('Results container name')
output resultsContainerName string = resultsContainerName

@description('Document Intelligence resource name')
output documentIntelligenceName string = documentIntelligence.name

@description('Document Intelligence endpoint')
output documentIntelligenceEndpoint string = documentIntelligence.properties.endpoint

@description('Key Vault name')
output keyVaultName string = keyVault.name

@description('Key Vault URI')
output keyVaultUri string = keyVault.properties.vaultUri

@description('Managed Identity name')
output managedIdentityName string = managedIdentity.name

@description('Managed Identity client ID')
output managedIdentityClientId string = managedIdentity.properties.clientId

@description('Managed Identity principal ID')
output managedIdentityPrincipalId string = managedIdentity.properties.principalId

@description('Resource Group Name')
output resourceGroupName string = resourceGroup().name

@description('Subscription ID')
output subscriptionId string = subscription().subscriptionId
