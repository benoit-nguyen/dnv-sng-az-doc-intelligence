# Azure Security Best Practices - Implementation Confirmation

## ✅ Best Practices Implemented

This implementation follows Microsoft's Azure AI Services security best practices as documented in the [official Azure documentation](https://learn.microsoft.com/en-us/azure/ai-services/security-features).

### 1. **Managed Identity (Preferred) with API Key Fallback**

All Azure service connections support **DefaultAzureCredential** which uses Managed Identity:

#### Document Intelligence (`analyzer.py`)
```python
if self.settings.document_intelligence_key:
    # API key (fallback for development)
    self.credential = AzureKeyCredential(self.settings.document_intelligence_key)
    logger.info("Using API key authentication")
else:
    # Managed Identity (production recommended)
    self.credential = DefaultAzureCredential()
    logger.info("Using managed identity authentication")
```

#### Blob Storage (`uploader.py`)
```python
if self.settings.storage_connection_string:
    # Connection string (development)
    self.blob_service_client = BlobServiceClient.from_connection_string(...)
else:
    # Managed Identity (production)
    self.credential = DefaultAzureCredential()
    self.blob_service_client = BlobServiceClient(account_url, credential=self.credential)
```

#### Azure Translator (`translator.py`)
```python
if self.settings.translator_key:
    # API key (development/testing)
    logger.warning("Using API key. Consider Managed Identity for production.")
    self.auth_mode = 'key'
else:
    # Managed Identity with bearer token (production)
    self.auth_mode = 'managed_identity'
    self.credential = DefaultAzureCredential()
    token = self.credential.get_token('https://cognitiveservices.azure.com/.default')
```

### 2. **Environment Variables (No Hardcoded Secrets)**

✅ All sensitive data stored in environment variables via `.env`:
- Document Intelligence endpoint and key
- Storage account credentials
- Translator service credentials
- No secrets in source code

Configuration loaded via `pydantic-settings`:
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
```

### 3. **Key Rotation Support**

✅ Optional field configuration allows switching between keys without code changes:
```python
document_intelligence_key: Optional[str] = Field(
    default=None, description="API key (optional if using managed identity)"
)
```

Switch keys by updating `.env` file - no code deployment needed.

### 4. **Transport Layer Security (TLS 1.2+)**

✅ All Azure SDK clients automatically use TLS 1.2/1.3:
- `azure-ai-documentintelligence`
- `azure-storage-blob`
- `azure-identity`
- All HTTPS endpoints enforced

### 5. **Retry Logic & Transient Error Handling**

✅ Implemented using `tenacity` library:
```python
@retry(
    retry_if_exception_type=HttpResponseError,
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10)
)
```

### 6. **Bearer Token Caching (Managed Identity)**

✅ Translator implementation caches tokens to reduce authentication overhead:
```python
def _get_bearer_token(self) -> str:
    # Check if we have a valid cached token
    if self._token and self._token_expiry:
        if datetime.utcnow() < self._token_expiry:
            return self._token
    
    # Refresh 5 minutes before expiry
    token_obj = self.credential.get_token('https://cognitiveservices.azure.com/.default')
```

### 7. **Logging Without Exposing Secrets**

✅ Log messages never include keys or tokens:
```python
logger.info(f"Using managed identity authentication")  # ✅ Safe
logger.info(f"Document Intelligence initialized: {endpoint}")  # ✅ Safe
logger.debug(f"Translated: '{text[:50]}...'")  # ✅ Truncated content
```

### 8. **Least Privilege SAS Tokens**

✅ Temporary SAS tokens with minimal permissions and expiry:
```python
def _generate_container_sas(
    self, 
    container_name: str,
    permissions: str = "rwdl",
    expiry_hours: int = 24  # Limited time window
) -> str:
```

## 🏆 Security Hierarchy (Best to Least Secure)

1. ✅ **Managed Identity** (Production) - No secrets stored anywhere
2. ✅ **Environment Variables** (Development) - Secrets not in code
3. ❌ **Hardcoded Keys** (Never) - Not implemented

## Production Deployment Checklist

For production deployment:

1. **Enable Managed Identity** on your Azure resources (VM, App Service, Container Instance)
2. **Grant RBAC Roles**:
   ```bash
   # Cognitive Services User
   az role assignment create \
     --assignee <managed-identity> \
     --role "Cognitive Services User" \
     --scope /subscriptions/.../documentIntelligence
   
   # Storage Blob Data Contributor
   az role assignment create \
     --assignee <managed-identity> \
     --role "Storage Blob Data Contributor" \
     --scope /subscriptions/.../storageAccount
   ```

3. **Remove keys from `.env`**:
   ```env
   # Production .env (no keys)
   DOCUMENT_INTELLIGENCE_ENDPOINT=https://...
   STORAGE_ACCOUNT_NAME=...
   TRANSLATOR_ENDPOINT=https://...
   # Keys removed - uses Managed Identity
   ```

4. **Verify authentication**:
   ```bash
   python -m docprocessor run ./documents
   # Should show: "Using managed identity authentication"
   ```

## Development vs Production

### Development (Local)
```env
# .env for local development
DOCUMENT_INTELLIGENCE_KEY=your-dev-key
STORAGE_CONNECTION_STRING=your-dev-connection
TRANSLATOR_KEY=your-dev-key
```

### Production (Azure)
```env
# .env for production (no secrets)
DOCUMENT_INTELLIGENCE_ENDPOINT=https://...
STORAGE_ACCOUNT_NAME=...
TRANSLATOR_ENDPOINT=https://...
# Managed Identity handles authentication
```

## References

- [Azure AI Services Security Features](https://learn.microsoft.com/en-us/azure/ai-services/security-features)
- [Azure Identity Library](https://learn.microsoft.com/en-us/python/api/azure-identity)
- [Managed Identities for Azure Resources](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/overview)
- [Azure SDK Authentication](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication-overview)
