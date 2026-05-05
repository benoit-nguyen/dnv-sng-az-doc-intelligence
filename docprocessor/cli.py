"""
Command-line interface for Document Processor.

Provides commands for:
- Scanning local folders
- Uploading documents to blob storage
- Starting batch analysis
- Checking analysis status
- Downloading and exporting results
"""

import json
import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .analyzer import DocumentIntelligenceAnalyzer
from .processor import ResultsProcessor
from .scanner import DocumentScanner
from .uploader import BlobUploader
from .translation import TranslationPipeline
from .translator import AzureDocumentFileTranslator
from .translator import DOCUMENT_TRANSLATION_EXTENSIONS
from .pdf_recreator import (
    AzureTranslator,
    build_translator_from_settings,
    create_pdf_from_analysis,
    translate_analysis_content,
)

# Initialize Typer app and Rich console
app = typer.Typer(
    name="docprocessor",
    help="Azure Document Intelligence Batch Processor CLI",
    add_completion=False,
)
console = Console()


@app.command()
def scan(
    folder: Path = typer.Argument(..., help="Folder to scan for documents"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save scan results to JSON file"
    ),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", "-r", help="Scan recursively"),
):
    """
    Scan a local folder for supported documents.
    
    Recursively scans the specified folder and identifies all supported
    document formats (PDF, DOCX, XLSX, PPTX, images, HTML, TXT).
    """
    console.print(f"\n[bold cyan]Scanning folder:[/bold cyan] {folder}")
    
    if not folder.exists():
        console.print(f"[bold red]Error:[/bold red] Folder not found: {folder}")
        raise typer.Exit(1)
    
    # Scan folder
    scanner = DocumentScanner()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning...", total=None)
        scan_result = scanner.scan_folder(folder, recursive=recursive)
        progress.update(task, completed=True)
    
    # Display results
    console.print(f"\n[bold green]Scan Complete![/bold green]")
    
    table = Table(title="Scan Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")
    
    table.add_row("Total Files", str(scan_result.total_files))
    table.add_row("Supported Documents", str(scan_result.supported_files))
    table.add_row("Unsupported Files", str(scan_result.unsupported_files))
    table.add_row("Skipped Files", str(scan_result.skipped_files))
    table.add_row("Total Size", f"{scan_result.total_size_bytes / (1024 * 1024):.2f} MB")
    
    console.print(table)
    
    # Show sample documents
    if scan_result.documents:
        console.print(f"\n[bold]Sample Documents (showing first 10):[/bold]")
        for i, doc in enumerate(scan_result.documents[:10], 1):
            size_mb = doc.file_size_bytes / (1024 * 1024)
            console.print(f"  {i}. {doc.relative_path} ({size_mb:.2f} MB)")
        
        if len(scan_result.documents) > 10:
            console.print(f"  ... and {len(scan_result.documents) - 10} more")
    
    # Save to file if requested
    if output:
        scan_result.save_to_file(output)
        console.print(f"\n[green]Scan results saved to:[/green] {output}")
    
    console.print()


@app.command()
def upload(
    folder: Optional[Path] = typer.Option(
        None, "--folder", "-f", help="Folder with documents to upload"
    ),
    scan_file: Optional[Path] = typer.Option(
        None, "--scan-file", "-s", help="JSON file with scan results"
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing blobs"),
    blob_prefix: Optional[str] = typer.Option(
        None, "--blob-prefix", help="Prefix prepended to all blob names (used for run isolation)"
    ),
):
    """
    Upload documents to Azure Blob Storage.
    
    Either specify a folder to scan and upload, or provide a scan results
    JSON file from a previous scan operation.
    """
    if not folder and not scan_file:
        console.print("[bold red]Error:[/bold red] Must specify either --folder or --scan-file")
        raise typer.Exit(1)
    
    # Get documents to upload
    if folder:
        console.print(f"\n[bold cyan]Scanning and uploading from:[/bold cyan] {folder}")
        scanner = DocumentScanner()
        scan_result = scanner.scan_folder(folder)
        documents = scan_result.documents
    else:
        console.print(f"\n[bold cyan]Loading scan results from:[/bold cyan] {scan_file}")
        scanner = DocumentScanner()
        scan_result = scanner.load_from_file(scan_file)
        documents = scan_result.documents
    
    if not documents:
        console.print("[bold yellow]No documents to upload[/bold yellow]")
        raise typer.Exit(0)
    
    console.print(f"[bold]Uploading {len(documents)} documents...[/bold]\n")
    
    # Upload with progress tracking
    uploaded_count = [0]
    
    def progress_callback(filename: str, current: int, total: int):
        uploaded_count[0] = current
        console.print(f"[{current}/{total}] Uploaded: {filename}")
    
    with BlobUploader(progress_callback=progress_callback) as uploader:
        upload_result = uploader.upload_documents(documents, overwrite=overwrite, blob_prefix=blob_prefix)
    
    # Display results
    console.print(f"\n[bold green]Upload Complete![/bold green]")
    
    table = Table(title="Upload Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")
    
    table.add_row("Total Files", str(upload_result.total_files))
    table.add_row("Successful", str(upload_result.successful))
    table.add_row("Failed", str(upload_result.failed))
    table.add_row("Success Rate", f"{upload_result.success_rate:.1f}%")
    table.add_row("Total Uploaded", f"{upload_result.total_bytes / (1024 * 1024):.2f} MB")
    
    console.print(table)
    
    # Show failures if any
    if upload_result.failed > 0:
        console.print(f"\n[bold red]Failed Uploads:[/bold red]")
        for result in upload_result.results:
            if not result.success:
                console.print(f"  - {result.document.relative_path}: {result.error}")
    
    console.print()


@app.command()
def analyze(
    model: str = typer.Option(
        "prebuilt-layout", "--model", "-m", help="Document Intelligence model ID"
    ),
    output_format: str = typer.Option(
        "markdown", "--format", help="Output format (text or markdown)"
    ),
    result_prefix: str = typer.Option(
        "results", "--prefix", "-p", help="Prefix for result files"
    ),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for completion"),
    source_prefix: Optional[str] = typer.Option(
        None, "--source-prefix", help="Filter source blobs by prefix (for run isolation)"
    ),
):
    """
    Start batch document analysis.
    
    Submits all documents in the source container for batch analysis
    using the specified Document Intelligence model.
    """
    console.print(f"\n[bold cyan]Starting batch analysis...[/bold cyan]")
    console.print(f"Model: {model}")
    console.print(f"Output Format: {output_format}")
    
    with DocumentIntelligenceAnalyzer() as analyzer:
        # Start batch analysis
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Submitting batch request...", total=None)
            operation_id = analyzer.start_batch_analysis(
                model_id=model,
                result_prefix=result_prefix,
                output_format=output_format,
                source_prefix=source_prefix,
            )
            progress.update(task, completed=True)
        
        console.print(f"\n[bold green]Batch analysis started![/bold green]")
        console.print(f"Operation ID: [cyan]{operation_id}[/cyan]")
        console.print(f"\nUse this command to check status:")
        console.print(f"  [yellow]docprocessor status {operation_id}[/yellow]")
        
        # Wait for completion if requested
        if wait:
            console.print(f"\n[bold]Waiting for batch completion...[/bold]")
            result = analyzer.poll_batch_completion(operation_id)
            
            # Display final status
            table = Table(title="Analysis Complete")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", justify="right", style="green")
            
            table.add_row("Status", result.status.value)
            table.add_row("Total Documents", str(result.total_count))
            table.add_row("Succeeded", str(result.succeeded_count))
            table.add_row("Failed", str(result.failed_count))
            table.add_row("Success Rate", f"{result.success_rate:.1f}%")
            
            console.print(f"\n")
            console.print(table)
    
    console.print()


@app.command()
def status(
    operation_id: str = typer.Argument(..., help="Operation ID from analyze command"),
):
    """
    Check the status of a batch analysis operation.
    
    Retrieves the current status of the batch operation and displays
    progress information.
    """
    console.print(f"\n[bold cyan]Checking batch status...[/bold cyan]")
    console.print(f"Operation ID: {operation_id}")
    
    with DocumentIntelligenceAnalyzer() as analyzer:
        result = analyzer.get_batch_status(operation_id)
    
    # Display status
    table = Table(title="Batch Analysis Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    
    # Color code status
    status_color = "green" if result.is_complete else "yellow"
    table.add_row("Status", f"[{status_color}]{result.status.value}[/{status_color}]")
    table.add_row("Total Documents", str(result.total_count))
    table.add_row("Succeeded", f"[green]{result.succeeded_count}[/green]")
    table.add_row("Failed", f"[red]{result.failed_count}[/red]")
    table.add_row("Success Rate", f"{result.success_rate:.1f}%")
    table.add_row("Created", result.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    table.add_row("Last Updated", result.last_updated.strftime("%Y-%m-%d %H:%M:%S"))
    
    console.print(f"\n")
    console.print(table)
    
    if not result.is_complete:
        console.print(f"\n[yellow]Batch is still processing. Check again later.[/yellow]")
    else:
        console.print(f"\n[green]Batch processing complete![/green]")
        console.print(f"Use this command to download results:")
        console.print(f"  [yellow]docprocessor download --output ./results[/yellow]")
    
    console.print()


@app.command()
def translate_file(
    file_path: Path = typer.Argument(..., help="Path to the PDF file to translate directly"),
    target_language: str = typer.Option("en", "--to", "-t", help="Target language code"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite an existing translated file"),
):
    """
    Translates a document directly using Azure Document Translation.
    Preserves exact visual layout, images, and formatting (Sync API up to 40MB).
    """
    if not file_path.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {file_path}")
        raise typer.Exit(1)
        
    try:
        console.print(f"[bold cyan]Starting layout-preserving translation for: {file_path.name}[/bold cyan]")
        translator = AzureDocumentFileTranslator()
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            progress.add_task(description=f"Translating {file_path.name} to '{target_language}'...", total=None)
            
            output = translator.translate_document(file_path, target_language, overwrite=overwrite)
            
        console.print(f"\n[bold green]Translation complete![/bold green] \nPreserved formatting document saved at [cyan]{output}[/cyan]")
    except Exception as e:
        console.print(f"\n[bold red]Translation Error:[/bold red] {e}")
        raise typer.Exit(1)


def _collect_translatable_files(
    paths: List[Path],
    recursive: bool,
    target_language: str,
    skip_existing_translations: bool,
) -> tuple[List[Path], List[Path]]:
    files: List[Path] = []
    skipped: List[Path] = []

    for input_path in paths:
        path = Path(input_path).resolve()
        if not path.exists():
            skipped.append(path)
            continue

        candidates: List[Path]
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            candidates = [candidate for candidate in iterator if candidate.is_file()]
        else:
            candidates = [path]

        for candidate in candidates:
            if candidate.suffix.lower() not in DOCUMENT_TRANSLATION_EXTENSIONS:
                skipped.append(candidate)
                continue
            if skip_existing_translations and candidate.stem.lower().endswith(
                f"_{target_language.lower()}"
            ):
                skipped.append(candidate)
                continue
            files.append(candidate)

    return sorted(dict.fromkeys(files)), sorted(dict.fromkeys(skipped))


@app.command(name="translate-paths")
def translate_paths(
    paths: List[Path] = typer.Argument(
        ..., help="One or more files or folders to translate in place"
    ),
    target_language: str = typer.Option("en", "--to", "-t", help="Target language code"),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Scan folders recursively"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing translated files"),
    skip_existing_translations: bool = typer.Option(
        True,
        "--skip-existing-translations/--include-existing-translations",
        help="Skip files already named with the target language suffix",
    ),
):
    """
    Translate selected files or folders to a target language and save outputs beside originals.

    Output files use the pattern <original>_<language><extension>, for example
    report.pdf -> report_en.pdf.
    """
    console.print(f"\n[bold cyan]Collecting documents for translation...[/bold cyan]")

    files, skipped = _collect_translatable_files(
        paths,
        recursive=recursive,
        target_language=target_language,
        skip_existing_translations=skip_existing_translations,
    )

    if not files:
        console.print("[yellow]No supported documents found to translate.[/yellow]")
        if skipped:
            console.print(f"Skipped {len(skipped)} unsupported or unavailable item(s).")
        console.print()
        raise typer.Exit(0)

    console.print(f"Found {len(files)} supported document(s).")
    if skipped:
        console.print(f"Skipping {len(skipped)} unsupported, unavailable, or already translated item(s).")

    translator = AzureDocumentFileTranslator()
    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Translating documents...", total=None)
        for index, file_path in enumerate(files, start=1):
            progress.update(task, description=f"[{index}/{len(files)}] Translating {file_path.name}...")
            results.extend(
                translator.translate_documents(
                    [file_path],
                    target_language=target_language,
                    overwrite=overwrite,
                )
            )
        progress.update(task, completed=True)

    succeeded = [result for result in results if result.success]
    failed = [result for result in results if not result.success]

    table = Table(title="Bulk Translation Summary")
    table.add_column("Source", style="cyan")
    table.add_column("Status")
    table.add_column("Output / Error")

    for result in results:
        if result.success:
            table.add_row(str(result.source_path), "[green]succeeded[/green]", str(result.output_path))
        else:
            table.add_row(str(result.source_path), "[red]failed[/red]", result.error or "Unknown error")

    console.print()
    console.print(table)
    console.print(
        f"\n[bold green]{len(succeeded)} translated[/bold green], "
        f"[bold red]{len(failed)} failed[/bold red], {len(skipped)} skipped\n"
    )

    if failed:
        raise typer.Exit(1)


@app.command()
def download(
    output: Path = typer.Option("./results", "--output", "-o", help="Output directory"),
    result_prefix: str = typer.Option(
        "results", "--prefix", "-p", help="Result files prefix"
    ),
    export_markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Export to Markdown"),
    export_csv: bool = typer.Option(True, "--csv/--no-csv", help="Export tables to CSV"),
):
    """
    Download and export batch analysis results.
    
    Downloads all result files from blob storage and exports them
    to Markdown and CSV formats.
    """
    console.print(f"\n[bold cyan]Downloading results...[/bold cyan]")
    console.print(f"Output Directory: {output}")
    
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    
    with ResultsProcessor() as processor:
        # Download all results
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Downloading and parsing results...", total=None)
            
            results = processor.batch_download_results(
                result_prefix=result_prefix,
                output_dir=output / "json"
            )
            
            progress.update(task, completed=True)
        
        console.print(f"\n[green]Downloaded {len(results)} results[/green]")
        
        # Export to Markdown
        if export_markdown:
            console.print(f"\n[bold]Exporting to Markdown...[/bold]")
            markdown_dir = output / "markdown"
            markdown_dir.mkdir(exist_ok=True)
            
            for doc_result in results:
                source_name = Path(doc_result.source_file).stem
                md_file = markdown_dir / f"{source_name}.md"
                processor.export_to_markdown(doc_result, md_file)
                console.print(f"  ✓ {md_file.name}")
        
        # Export tables to CSV
        if export_csv:
            console.print(f"\n[bold]Exporting tables to CSV...[/bold]")
            csv_dir = output / "tables"
            csv_dir.mkdir(exist_ok=True)
            
            total_tables = 0
            for doc_result in results:
                if doc_result.tables:
                    csv_files = processor.export_tables_to_csv(doc_result, csv_dir)
                    total_tables += len(csv_files)
                    for csv_file in csv_files:
                        console.print(f"  ✓ {csv_file.name}")
            
            console.print(f"\n[green]Exported {total_tables} tables[/green]")
        
        # Summary
        console.print(f"\n[bold green]Export Complete![/bold green]")
        console.print(f"Results saved to: {output.absolute()}")
    
    console.print()


@app.command()
def translate(
    batch_id: str = typer.Option(..., "--batch-id", "-b", help="Batch or result prefix to translate"),
    locales: Optional[str] = typer.Option(
        None,
        "--locales",
        "-l",
        help="Comma-separated list of target locales (defaults to configuration)",
    ),
    overwrite: Optional[bool] = typer.Option(
        None,
        "--overwrite/--no-overwrite",
        help="Whether to overwrite existing translations (defaults to configuration)",
    ),
):
    """Translate analyzed documents into multiple locales."""

    console.print(f"\n[bold cyan]Starting translation for batch:[/bold cyan] {batch_id}")

    target_locales: Optional[List[str]] = None
    if locales:
        target_locales = [loc.strip() for loc in locales.split(",") if loc.strip()]
        console.print(f"Locales: {', '.join(target_locales)}")

    try:
        with TranslationPipeline() as pipeline:
            records = pipeline.translate_batch(
                batch_id=batch_id,
                target_locales=target_locales,
                overwrite=overwrite,
            )
    except Exception as exc:  # noqa: BLE001 - surface CLI error
        console.print(f"[bold red]Translation failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    if not records:
        console.print("[yellow]No documents were translated. Ensure the batch ID is correct.[/yellow]")
        console.print()
        return

    table = Table(title="Translation Summary")
    table.add_column("Document", style="cyan")
    table.add_column("Locale", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Segments", justify="right")
    table.add_column("Table Cells", justify="right")
    table.add_column("Blob")
    table.add_column("Error", style="red")

    for record in records:
        status_color = "green" if record.status == "succeeded" else "red"
        blob_display = record.blob_name or "-"
        error_display = record.error or ""
        table.add_row(
            record.source_document,
            record.locale,
            f"[{status_color}]{record.status}[/{status_color}]",
            str(record.translated_segments),
            str(record.translated_table_cells),
            blob_display,
            error_display,
        )

    console.print()
    console.print(table)
    console.print()


@app.command(name="recreate-pdf")
def recreate_pdf(
    json_file: Optional[Path] = typer.Argument(None, help="Path to a single analysis JSON file"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output PDF path for single-file mode"
    ),
    json_dir: Optional[Path] = typer.Option(
        None, "--json-dir", help="Directory of JSON files to process in batch"
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="Output directory for batch mode PDFs"
    ),
    translate: bool = typer.Option(
        False, "--translate/--no-translate", "-t", help="Translate content before rendering"
    ),
    locale: str = typer.Option("en", "--locale", "-l", help="Target locale when --translate is set"),
    timeout: float = typer.Option(15.0, "--timeout", help="Translation request timeout (seconds)"),
):
    """
    Recreate PDF(s) from Azure Document Intelligence analysis JSON.

    Single-file mode:  recreate-pdf path/to/file.json [--output out.pdf]
    Batch mode:        recreate-pdf --json-dir ./json/ --output-dir ./pdfs/

    Handles both scanned PDFs (polygon coordinates) and DOCX-origin files
    (text-flow layout). With --translate the content is translated before rendering.
    """
    import json as _json

    if json_file is None and json_dir is None:
        console.print("[bold red]Error:[/bold red] Provide a JSON file or --json-dir")
        raise typer.Exit(1)

    # ------------------------------------------------------------------ batch
    if json_dir is not None:
        if not json_dir.is_dir():
            console.print(f"[bold red]Error:[/bold red] Not a directory: {json_dir}")
            raise typer.Exit(1)
        dest = output_dir or (json_dir.parent / "pdfs")
        dest.mkdir(parents=True, exist_ok=True)
        json_files = sorted(json_dir.glob("*.json"))
        if not json_files:
            console.print(f"[yellow]No JSON files found in {json_dir}[/yellow]")
            return
        console.print(f"\n[bold cyan]Batch recreating {len(json_files)} PDF(s)...[/bold cyan]")
        ok = 0
        file_suffix = f"_{locale}" if translate else ""
        for jf in json_files:
            out = dest / f"{jf.stem}{file_suffix}.pdf"
            try:
                with open(jf, "r", encoding="utf-8") as fh:
                    data = _json.load(fh)
                if "analyzeResult" in data and "pages" not in data:
                    data = data["analyzeResult"]
                if translate:
                    with build_translator_from_settings() as translator:
                        translator._timeout = timeout
                        translate_analysis_content(data, translator, locale)
                create_pdf_from_analysis(str(jf), str(out), analysis_data=data)
                size_kb = out.stat().st_size / 1024
                console.print(f"  [green]OK[/green] {out.name} ({size_kb:.1f} KB)")
                ok += 1
            except Exception as exc:
                console.print(f"  [red]FAIL[/red] {jf.name}: {exc}")
        console.print(f"\n[bold green]{ok}/{len(json_files)} PDFs created → {dest.absolute()}[/bold green]\n")
        return

    # ------------------------------------------------------------------ single
    if not json_file.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {json_file}")
        raise typer.Exit(1)

    if output is None:
        file_suffix = f"_{locale}" if translate else ""
        output = json_file.parent / f"{json_file.stem}{file_suffix}.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold cyan]Recreating PDF from:[/bold cyan] {json_file.name}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Loading analysis JSON...", total=None)

        with open(json_file, "r", encoding="utf-8") as fh:
            analysis_data = _json.load(fh)

        if "analyzeResult" in analysis_data and "pages" not in analysis_data:
            analysis_data = analysis_data["analyzeResult"]

        if translate:
            progress.update(task, description=f"Translating to {locale}...")
            try:
                with build_translator_from_settings() as translator:
                    translator._timeout = timeout
                    unique, para_count, cell_count = translate_analysis_content(
                        analysis_data, translator, locale
                    )
                console.print(
                    f"[green]Translated {unique} unique segments "
                    f"({para_count} paragraphs, {cell_count} table cells) to {locale}[/green]"
                )
            except Exception as exc:
                console.print(f"[bold red]Translation failed:[/bold red] {exc}")
                raise typer.Exit(1) from exc

        progress.update(task, description="Rendering PDF...")
        try:
            create_pdf_from_analysis(str(json_file), str(output), analysis_data=analysis_data)
        except Exception as exc:
            console.print(f"[bold red]PDF creation failed:[/bold red] {exc}")
            raise typer.Exit(1) from exc

        progress.update(task, completed=True)

    file_size = output.stat().st_size / 1024
    console.print(f"\n[bold green]Created:[/bold green] {output.name} ({file_size:.1f} KB)")
    console.print(f"Location: {output.absolute()}\n")


@app.command()
def run(
    folder: Path = typer.Argument(..., help="Folder with documents to process"),
    model: str = typer.Option(
        "prebuilt-layout", "--model", "-m", help="Document Intelligence model ID"
    ),
    output: Path = typer.Option("./results", "--output", "-o", help="Output directory"),
    wait: bool = typer.Option(True, "--wait/--no-wait", "-w", help="Wait for completion"),
):
    """
    Run the complete document processing pipeline.
    
    This command performs all steps in sequence:
    1. Scan the local folder
    2. Upload documents to blob storage
    3. Start batch analysis
    4. Wait for completion (if --wait)
    5. Download and export results
    """
    console.print(f"\n[bold cyan]═══════════════════════════════════════════[/bold cyan]")
    console.print(f"[bold cyan]  Document Processing Pipeline[/bold cyan]")
    console.print(f"[bold cyan]═══════════════════════════════════════════[/bold cyan]\n")
    
    # Step 1: Scan
    console.print(f"[bold]Step 1/5: Scanning folder...[/bold]")
    scanner = DocumentScanner()
    scan_result = scanner.scan_folder(folder)
    total_size_mb = scan_result.total_size_bytes / (1024 * 1024)
    console.print(f"[green]✓[/green] Found {scan_result.supported_files} documents ({total_size_mb:.2f} MB)\n")
    
    if scan_result.supported_files == 0:
        console.print("[yellow]No documents found. Exiting.[/yellow]")
        raise typer.Exit(0)
    
    # Step 2: Upload
    console.print(f"[bold]Step 2/5: Uploading to Azure...[/bold]")
    with BlobUploader() as uploader:
        upload_result = uploader.upload_documents(scan_result.documents)
    console.print(f"[green]✓[/green] Uploaded {upload_result.successful}/{upload_result.total_files} files\n")
    
    # Step 3: Start analysis
    console.print(f"[bold]Step 3/5: Starting batch analysis...[/bold]")
    with DocumentIntelligenceAnalyzer() as analyzer:
        operation_id = analyzer.start_batch_analysis(model_id=model)
        console.print(f"[green]✓[/green] Batch started (ID: {operation_id})\n")
        
        # Step 4: Wait if requested
        if wait:
            console.print(f"[bold]Step 4/5: Waiting for completion...[/bold]")
            result = analyzer.poll_batch_completion(operation_id, polling_interval=30)
            console.print(f"[green]✓[/green] Analysis complete ({result.succeeded_count}/{result.total_count} succeeded)\n")
        else:
            console.print(f"[yellow]Skipping wait. Check status with: docprocessor status {operation_id}[/yellow]\n")
            raise typer.Exit(0)
    
    # Step 5: Download results
    console.print(f"[bold]Step 5/5: Downloading results...[/bold]")
    with ResultsProcessor() as processor:
        results = processor.batch_download_results()
        
        # Export
        output = Path(output)
        markdown_dir = output / "markdown"
        markdown_dir.mkdir(parents=True, exist_ok=True)
        
        for doc_result in results:
            source_name = Path(doc_result.source_file).stem
            md_file = markdown_dir / f"{source_name}.md"
            processor.export_to_markdown(doc_result, md_file)
        
        console.print(f"[green]✓[/green] Exported {len(results)} results to {output}\n")
    
    console.print(f"[bold green]═══════════════════════════════════════════[/bold green]")
    console.print(f"[bold green]  Pipeline Complete![/bold green]")
    console.print(f"[bold green]═══════════════════════════════════════════[/bold green]\n")


def main():
    """Main entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
