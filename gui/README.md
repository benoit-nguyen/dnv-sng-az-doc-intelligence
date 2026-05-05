# Document Intelligence GUI

Desktop application for Azure Document Intelligence Batch Processor with translation capabilities.

## Features

- 📁 **Folder & File Selection**: Choose entire folders or individual files
- 🔄 **Batch Processing**: Automatic scan, upload, analyze, and translate workflow
- 🌐 **Translation**: Translate documents to English using Azure AI Translator
- 📊 **Real-time Progress**: Monitor processing steps with live activity log
- 📂 **Results Management**: Easy access to processed documents and translations

## Prerequisites

For the packaged app installed from `release/`, colleagues do not need Node.js, Python, or the source repository.

For development and building the installer:

1. **Node.js** (v16 or higher)
2. **Python 3.11+** with the docprocessor package installed in `../.venv`
3. **Azure Account** with Document Intelligence and Translator services configured
4. **Environment Variables**: A `.env` file in the parent directory for development only

## Installation

1. Navigate to the gui folder:
   ```powershell
   cd gui
   ```

2. Install dependencies:
   ```powershell
   npm install
   ```

## Configuration

In development mode, ensure your `.env` file in the parent directory (`../`) contains:

```env
DOCUMENT_INTELLIGENCE_ENDPOINT=https://your-document-intelligence.cognitiveservices.azure.com/
DOCUMENT_INTELLIGENCE_KEY=your_document_intelligence_key
TRANSLATOR_ENDPOINT=https://your-translator-resource.cognitiveservices.azure.com/
TRANSLATOR_KEY=your_translator_key
TRANSLATOR_REGION=your_region
```

In the packaged app, `.env` is not used. Open **Azure Settings** in the app, paste the endpoints and keys, and click **Save Securely**. The values are stored under the current Windows user profile using Electron `safeStorage`, backed by the operating system where available.

## Usage

### Development Mode

Run the application in development mode with hot-reloading:

```powershell
npm run dev
```

This will:
- Start the Vite dev server for React
- Launch the Electron app
- Enable developer tools

### Production Build

Build the application for distribution:

```powershell
npm run build
```

This creates a bundled Python backend with PyInstaller, builds the renderer, and creates an installer in the `release/` directory. The installed app contains `resources/backend/docprocessor.exe`, so it can run on another Windows computer without a Python installation.

The installer does not include the development `.env` file. Each colleague should configure Azure Settings on first launch.

## Updates

The app includes a **Check for Updates** button in the header.

By default, the button reads the GitHub-hosted manifest at:

```text
https://raw.githubusercontent.com/dnv-internal/dnv-sng-az-doc-intelligence/main/update-manifest.json
```

If a different update source is needed, host a small JSON manifest at an accessible HTTPS URL and set `DOC_PROCESSOR_UPDATE_MANIFEST_URL` before building the installer. Do not embed private GitHub tokens in the app.

Example manifest:

```json
{
   "version": "1.0.2",
   "installerUrl": "https://your-internal-download-location/Document%20Intelligence%20Processor%20Setup%201.0.2.exe",
   "releaseNotesUrl": "https://your-internal-release-notes/page",
   "notes": "PDF translation now uses Document Intelligence layout analysis directly."
}
```

For each new release, increment `version` in `package.json`, run `npm run build`, upload the new installer, then update the manifest.

### Start Production App

Run the built application:

```powershell
npm start
```

## How to Use

1. **Select Documents**
   - Click "Select Folder" to process all supported files in a folder
   - Or click "Select Files" to choose specific files
   - Supported formats: PDF, DOCX, XLSX, PPTX, Images, HTML, TXT

2. **Configure Processing**
   - Set a result prefix to organize your batch
   - Enable/disable translation to English

3. **Process Documents**
   - Click "Start Processing" to begin
   - Monitor progress in the activity log
   - Cancel anytime if needed

4. **View Results**
   - Click "Open Results Folder" when complete
   - Find analyzed documents and translations in the output directory

## Architecture

- **Frontend**: React + Vite
- **Desktop Framework**: Electron
- **Backend**: Python docprocessor CLI
- **Communication**: IPC (Inter-Process Communication)

## Supported File Types

- Documents: PDF, DOCX, DOC, XLSX, XLS, PPTX, PPT
- Images: JPG, JPEG, PNG, TIFF, BMP
- Web: HTML, HTM
- Text: TXT

## Troubleshooting

### "Azure Not Configured" Error
- Check your `.env` file exists in the parent directory
- Verify all required environment variables are set
- Restart the application

### Processing Fails
- Check the activity log for specific errors
- Ensure Python virtual environment is activated
- Verify Azure credentials are valid

### Files Not Found
- Ensure files are in supported formats
- Check file permissions
- Try selecting files individually instead of folder

## License

Copyright © 2026 DNV. All rights reserved.
