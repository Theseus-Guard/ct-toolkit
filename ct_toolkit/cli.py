"""
ct_toolkit.cli
--------------
Main entry point for the Theseus Guard / CT Toolkit CLI.
"""
from __future__ import annotations

import hashlib
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from importlib.resources import files as package_files
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ct_toolkit import TheseusWrapper, WrapperConfig, __version__
from ct_toolkit.divergence.l3_icm import ICMRunner
from ct_toolkit.server import start_dashboard_server, start_server

app = typer.Typer(
    help="Computational Theseus Toolkit — Identity Continuity Guardrails for Agentic Systems.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()

_PROFILE_CHECKSUM_RESOURCE = "profile_checksums.sha256"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _load_trusted_profile_checksums() -> dict[str, str]:
    """Load the out-of-band profile hashes shipped in the installed wheel."""
    manifest = package_files("ct_toolkit").joinpath(_PROFILE_CHECKSUM_RESOURCE)
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text(encoding="ascii").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not _SHA256.fullmatch(parts[0]):
            raise RuntimeError(f"Invalid profile checksum manifest at line {line_number}")
        filename = parts[1].strip()
        if Path(filename).name != filename:
            raise RuntimeError(f"Invalid profile filename in checksum manifest: {filename}")
        checksums[filename] = parts[0]
    if not checksums:
        raise RuntimeError("Trusted profile checksum manifest is empty")
    return checksums


class _NoDowngradeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only to the same HTTPS origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        original = urllib.parse.urlparse(req.full_url)
        redirected = urllib.parse.urlparse(newurl)
        if (
            redirected.scheme != "https"
            or redirected.hostname != original.hostname
            or redirected.port != original.port
        ):
            raise urllib.error.URLError(f"Refusing unsafe redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_profile_file(url: str) -> bytes:
    """Download one profile file over validated HTTPS without unsafe redirects."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise urllib.error.URLError(f"Refusing non-HTTPS URL: {url}")
    opener = urllib.request.build_opener(
        _NoDowngradeRedirectHandler(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    request = urllib.request.Request(url, headers={"User-Agent": "ct-toolkit-setup/1.0"})
    with opener.open(request) as response:
        return response.read()

BANNER = r"""
  _______ _    _ ______  _____ ______ _    _  _____    _____ _    _          _____  _____  
 |__   __| |  | |  ____|/ ____|  ____| |  | |/ ____|  / ____| |  | |   /\   |  __ \|  __ \ 
    | |  | |__| | |__  | (___ | |__  | |  | | (___   | |  __| |  | |  /  \  | |__) | |  | |
    | |  |  __  |  __|  \___ \|  __| | |  | |\___ \  | | |_ | |  | | / /\ \ |  _  /| |  | |
    | |  | |  | | |____ ____) | |____| |__| |____) | | |__| | |__| |/ ____ \| | \ \| |__| |
    |_|  |_|  |_|______|_____/|______|_____/|_____/   \_____|\____//_/    \_\_|  \_\_____/ 
"""

def show_banner():
    console.print(f"[bold cyan]{BANNER}[/bold cyan]")
    console.print(f"[bold white]  Computational Theseus Toolkit (CT Toolkit) v{__version__}[/bold white]")
    console.print("[dim]  Identity Continuity Guardrails for Agentic Systems[/dim]\n")

def version_callback(value: bool):
    if value:
        console.print(f"CT Toolkit v{__version__}")
        raise typer.Exit()

@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-v", help="Show version and exit.", callback=version_callback
    ),
):
    """Theseus Guard CLI — Preserve Agent Identity Continuity."""
    if not version:
        show_banner()

@app.command()
def audit(
    url: str = typer.Option(..., "--url", help="Target LLM API base URL."),
    api_key: str = typer.Option("no-key", "--api-key", help="API Key for the provider."),
    provider: str = typer.Option("openai", "--provider", help="LLM provider (openai, anthropic, ollama)."),
    kernel: str = typer.Option("general", "--kernel", help="Name of the Constitutional Kernel to use."),
    template: str = typer.Option("general", "--template", help="Name of the Identity Template to use."),
    policy_environment: str = typer.Option("prod", "--policy-environment", help="Policy environment override (dev, test, prod)."),
    model: Optional[str] = typer.Option(None, "--model", help="Specific model ID to test."),
    max_probes: Optional[int] = typer.Option(None, "--max-probes", help="Max number of probes to run."),
):
    """Run an Independent Identity Audit (L3 ICM) against an LLM endpoint."""
    with console.status("[bold green]Initializing Auditor...[/bold green]"):
        try:
            # Load kernel
            config = WrapperConfig(
                kernel_name=kernel,
                template=template,
                project_root=Path.cwd(),
                policy_environment=policy_environment,
            )
            
            # Using TheseusWrapper to manage kernel loading
            wrapper = TheseusWrapper(provider=provider, config=config)
            
            import openai
            client = openai.OpenAI(base_url=url, api_key=api_key)

            runner = ICMRunner(
                client=client,
                provider=provider,
                kernel=wrapper.kernel,
                template=template,
                model=model,
                max_probes=max_probes,
                project_root=Path.cwd()
            )
        except Exception as e:
            console.print(f"[bold red]Initialization failed:[/bold red] {e}")
            raise typer.Exit(code=1)

    console.print(f"🚀 Starting audit against [bold cyan]{url}[/bold cyan] using kernel [bold yellow]{kernel}[/bold yellow]...")
    
    with console.status("[bold blue]Running probes...[/bold blue]"):
        try:
            report = runner.run()
            if report.total_probes == 0:
                console.print(f"[bold red]Error:[/bold red] No probes were loaded from {runner.PROBES_DIR}. Check your configuration and probe files.")
                raise typer.Exit(code=1)
        except Exception as e:
            console.print(f"[bold red]Audit failed during execution:[/bold red] {e}")
            raise typer.Exit(code=1)

    # Results Table
    table = Table(title=f"ICM Audit Report: {kernel}", show_header=True, header_style="bold magenta")
    table.add_column("Probe ID", style="dim", width=20)
    table.add_column("Category")
    table.add_column("Severity")
    table.add_column("Result", justify="center")

    for res in report.results:
        status = "[green]PASS[/green]" if res.passed else "[red]FAIL[/red]"
        table.add_row(res.probe_id, res.category, res.severity, status)

    console.print(table)

    # Summary Panel
    color = "green" if report.is_healthy else "red"
    if report.risk_level == "MEDIUM":
        color = "yellow"
        
    summary_text = (
        f"Health Score : [bold]{report.health_score:.1%}[/bold]\n"
        f"Risk Level   : [bold {color}]{report.risk_level}[/bold {color}]\n"
        f"Passed/Total : {report.passed}/{report.total_probes}"
    )
    
    console.print(Panel(summary_text, title="Summary", border_style=color, expand=False))

    if not report.is_healthy:
        raise typer.Exit(code=1)

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind the server to."),
    port: int = typer.Option(8001, "--port", help="Port to bind the server to."),
    kernel: str = typer.Option("general", "--kernel", help="Name of the Constitutional Kernel to use."),
    template: str = typer.Option("general", "--template", help="Name of the Identity Template to use."),
    vault_path: str = typer.Option("./ct_provenance.db", "--vault", help="Path to the provenance log database."),
    policy_environment: str = typer.Option("prod", "--policy-environment", help="Policy environment override (dev, test, prod)."),
    judge_provider: str = typer.Option("openai", "--judge-provider", help="LLM provider for L2/L3 judge calls."),
    judge_model: Optional[str] = typer.Option(None, "--judge-model", help="Model ID for L2/L3 judge calls."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Optional Bearer token / API key for server endpoints."),
):
    """Start the CT-Toolkit Guardrail & Web Audit Server."""
    console.print("Starting Guardrail & Audit Server...")
    console.print(f"  - Kernel: [bold yellow]{kernel}[/bold yellow]")
    console.print(f"  - Template: [bold green]{template}[/bold green]")
    console.print(f"  - Bind: [bold cyan]{host}:{port}[/bold cyan]")
    console.print(f"  - Web Audit Dashboard: [bold underline cyan]http://{host}:{port}/dashboard[/bold underline cyan]")
    
    try:
        config = WrapperConfig(
            kernel_name=kernel,
            template=template,
            vault_path=vault_path,
            policy_environment=policy_environment,
            judge_provider=judge_provider,
            judge_model=judge_model,
            project_root=Path.cwd()
        )
        wrapper = TheseusWrapper(provider=judge_provider, config=config)
        
        # Start the uvicorn server
        start_server(wrapper=wrapper, host=host, port=port, api_key=api_key)
    except Exception as e:
        console.print(f"[bold red]Failed to start server:[/bold red] {e}")
        raise typer.Exit(code=1)

@app.command()
def setup(
    profile: str = typer.Argument("personal_kernel", help="Name of the profile to download (e.g., personal_kernel)"),
    repo_branch: str = typer.Option("main", "--branch", help="GitHub repo branch"),
    dest_dir: str = typer.Option("./config", "--dest", help="Destination folder (default: ./config)"),
    verify_checksums: bool = typer.Option(True, "--verify-checksums/--no-verify-checksums", help="Download and verify SHA256 checksums"),
):
    """Download a CT-Toolkit profile (kernel, identity, probes) from the official GitHub repository.

    SECURITY: The previous implementation only self-computed SHA-256 of the
    just-downloaded payload, which is a no-op against an active MITM or a
    compromised upstream. We now require the out-of-band known-good checksum
    manifest shipped in the installed wheel and refuse the download if the
    live bytes do not match. We additionally pin the download to HTTPS, refuse
    cross-origin redirects, and require certificate validation against the
    system trust store.
    """
    base_url = f"https://raw.githubusercontent.com/Theseus-Guard/ct-toolkit/{repo_branch}/examples/agent_dna_config"

    if not verify_checksums:
        console.print(
            "[bold red]Refusing to download without checksum verification.[/bold red]\n"
            "The profile must match the trusted checksum manifest shipped with CT Toolkit."
        )
        raise typer.Exit(code=2)

    try:
        trusted_checksums = _load_trusted_profile_checksums()
    except Exception as exc:
        console.print(f"[bold red]Trusted checksum manifest unavailable:[/bold red] {exc}")
        raise typer.Exit(code=1)

    base_name = profile.replace("_kernel", "") if profile.endswith("_kernel") else profile
    if base_name == profile:
        kernel_file = f"{profile}_kernel.yaml"
    else:
        kernel_file = f"{profile}.yaml"

    identity_file = f"{base_name}_identity.yaml"
    probes_file = f"{base_name}_probes.json"

    files_to_download = [kernel_file, identity_file, probes_file]

    missing_hashes = [filename for filename in files_to_download if filename not in trusted_checksums]
    if missing_hashes:
        console.print(
            "[bold red]No trusted checksum is pinned for:[/bold red] "
            + ", ".join(missing_hashes)
        )
        raise typer.Exit(code=2)

    target_dir = Path(dest_dir)

    with console.status(f"[bold green]Downloading '{profile}' profile from GitHub...[/bold green]"):
        has_errors = False
        downloaded: dict[str, bytes] = {}
        checksums: dict[str, str] = {}

        for filename in files_to_download:
            url = f"{base_url}/{filename}"
            try:
                content = _download_profile_file(url)
                actual = hashlib.sha256(content).hexdigest()
                expected = trusted_checksums[filename]
                if actual != expected:
                    raise ValueError(
                        f"checksum mismatch (expected {expected}, got {actual})"
                    )
                downloaded[filename] = content
                console.print(f"[green]✓ Verified {filename}[/green]")
            except Exception as e:
                console.print(f"[red]✗ Failed to download {filename} from {url}:[/red] {e}")
                has_errors = True

        # Do not write any profile until every downloaded file has passed the
        # trusted hash check.
        if not has_errors:
            target_dir.mkdir(parents=True, exist_ok=True)
            for filename, content in downloaded.items():
                (target_dir / filename).write_bytes(content)
                checksums[filename] = hashlib.sha256(content).hexdigest()

        # Write the verified hashes for transparency/audit.
        if checksums and not has_errors:
            checksum_path = target_dir / f"{profile}_checksums.sha256"
            try:
                with open(checksum_path, "w") as f:
                    for fname, digest in checksums.items():
                        f.write(f"{digest}  {fname}\n")
                console.print(f"[dim]Checksums saved to {checksum_path}[/dim]")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not save checksums file: {e}[/yellow]")

    if not has_errors:
        console.print(f"\n[bold blue]Profile '{profile}' setup complete![/bold blue]")
        console.print(
            f"You can now use this kernel by setting "
            f"[bold]kernel_name='{kernel_file.replace('.yaml','')}'[/bold] in TheseusWrapper"
        )
    else:
        console.print(f"\n[bold yellow]Profile '{profile}' setup finished with some errors.[/bold yellow]")
        raise typer.Exit(code=1)

@app.command()
def list_kernels():
    """List available Constitutional Kernels."""
    kernels_dir = Path(__file__).parent / "kernels"
    files = list(kernels_dir.glob("*.yaml"))
    
    table = Table(title="Available Kernels", show_header=True, header_style="bold blue")
    table.add_column("Name", style="cyan")
    table.add_column("Path", style="dim")
    
    for f in files:
        table.add_row(f.stem, str(f))
        
    console.print(table)

@app.command()
def list_templates():
    """List available Identity Templates."""
    templates_dir = Path(__file__).parent / "identity" / "templates"
    files = list(templates_dir.glob("*.yaml"))
    
    table = Table(title="Available Templates", show_header=True, header_style="bold green")
    table.add_column("Name", style="cyan")
    table.add_column("Path", style="dim")
    
    for f in files:
        table.add_row(f.stem, str(f))
        
    console.print(table)


def _render_status_dashboard(
    vault_path: str = "./ct_provenance.db",
    vault_type: str = "local",
    limit: int = 200,
) -> None:
    """Renders the Provenance Chain, L1/L2/L3 Divergence Trends, and Staged Rules Dashboard."""
    import time as _time

    from ct_toolkit.core.exceptions import ChainIntegrityError
    from ct_toolkit.provenance.log import ProvenanceLog

    try:
        log = ProvenanceLog(vault_type=vault_type, vault_path=vault_path)
    except Exception as exc:
        console.print(f"[bold red]Failed to open Provenance Log ({vault_path}):[/bold red] {exc}")
        raise typer.Exit(code=1)

    chain_ok = True
    tampered_entry_id: str | None = None
    verified_count = 0
    last_hash = ProvenanceLog.GENESIS_HASH

    try:
        checkpoint = log.get_chain_checkpoint(batch_size=500)
        verified_count = int(checkpoint["verified_count"])
        last_hash = str(checkpoint["last_content_hash"])
    except ChainIntegrityError as exc:
        chain_ok = False
        tampered_entry_id = getattr(exc, "entry_id", str(exc))

    entries = log.get_entries(limit=limit, include_rolled_back=False)
    scores = [e.divergence_score for e in entries if e.divergence_score is not None]
    avg_score = (sum(scores) / len(scores)) if scores else 0.0
    max_score = max(scores) if scores else 0.0

    tier_counts: dict[str, int] = {
        "ok": 0,
        "l1_warning": 0,
        "l2_judge": 0,
        "l3_icm": 0,
    }
    promoted_ids: set[str] = set()
    staged_records: list[dict[str, object]] = []

    for entry in entries:
        meta = entry.metadata or {}
        event_type = meta.get("event_type")
        if event_type == "staged_promotion":
            eid = meta.get("endorsement_id")
            if eid:
                promoted_ids.add(str(eid))
            continue
        if event_type == "reflective_endorsement" and meta.get("decision") == "staged":
            staged_records.append(meta)
            continue

        raw_tier = str(meta.get("tier") or meta.get("divergence_tier") or "").lower()
        if raw_tier in tier_counts:
            tier_counts[raw_tier] += 1
        elif entry.divergence_score is not None:
            if entry.divergence_score >= 0.50:
                tier_counts["l3_icm"] += 1
            elif entry.divergence_score >= 0.30:
                tier_counts["l2_judge"] += 1
            elif entry.divergence_score >= 0.15:
                tier_counts["l1_warning"] += 1
            else:
                tier_counts["ok"] += 1

    chain_badge = (
        "[bold green]VALID (HMAC Verified)[/bold green]"
        if chain_ok
        else f"[bold red]COMPROMISED (Entry {tampered_entry_id})[/bold red]"
    )
    summary_text = (
        f"[bold]Vault:[/bold] {vault_path} ({vault_type})\n"
        f"[bold]Chain Integrity:[/bold] {chain_badge}\n"
        f"[bold]Verified Active Entries:[/bold] {verified_count} (showing last {len(entries)})\n"
        f"[bold]Divergence Score (Avg / Max):[/bold] {avg_score:.4f} / {max_score:.4f}\n"
        f"[bold]Head Hash:[/bold] [dim]{last_hash[:32]}...[/dim]"
    )
    console.print(Panel(summary_text, title="CT Toolkit Audit & Telemetry Status", border_style="cyan"))

    tier_table = Table(title="Divergence Tier Breakdown (L1 / L2 / L3)", show_header=True, header_style="bold magenta")
    tier_table.add_column("Tier", style="cyan")
    tier_table.add_column("Count", justify="right")
    tier_table.add_column("Share", justify="right")

    total_tiered = sum(tier_counts.values())
    for tier_name, count in tier_counts.items():
        pct = (count / total_tiered * 100.0) if total_tiered > 0 else 0.0
        tier_table.add_row(tier_name.upper(), str(count), f"{pct:.1f}%")
    console.print(tier_table)

    staged_table = Table(title="Staged Kernel Rules (Sandbox Cooldown)", show_header=True, header_style="bold yellow")
    staged_table.add_column("Endorsement ID", style="cyan")
    staged_table.add_column("Commitment", style="white")
    staged_table.add_column("Rule Text", style="white")
    staged_table.add_column("Status", style="bold")

    now = _time.time()
    if not staged_records:
        staged_table.add_row("-", "-", "No staged rules recorded", "[dim]NONE[/dim]")
    else:
        for s_meta in staged_records:
            eid = str(s_meta.get("endorsement_id", "unknown"))
            commitment = str(s_meta.get("commitment_id", "unknown"))
            rule_text = str(s_meta.get("rule_text", ""))
            cooldown_until = s_meta.get("cooldown_until")
            if eid in promoted_ids:
                status_str = "[green]PROMOTED[/green]"
            elif isinstance(cooldown_until, (int, float)) and now < cooldown_until:
                rem = int(cooldown_until - now)
                status_str = f"[yellow]ACTIVE COOLDOWN ({rem}s left)[/yellow]"
            else:
                status_str = "[blue]READY FOR PROMOTION[/blue]"
            staged_table.add_row(eid[:12], commitment, rule_text, status_str)

    console.print(staged_table)

    if not chain_ok:
        raise typer.Exit(code=1)


@app.command(name="status")
def status_cmd(
    vault_path: str = typer.Option("./ct_provenance.db", "--vault", help="Path or SQLAlchemy URL to the provenance log database."),
    vault_type: str = typer.Option("local", "--vault-type", help="Vault backend type: local, postgres, sqlalchemy."),
    limit: int = typer.Option(200, "--limit", help="Maximum recent entries to analyze."),
):
    """Display Provenance Chain integrity, L1/L2/L3 divergence trends, and active staged rules."""
    _render_status_dashboard(vault_path=vault_path, vault_type=vault_type, limit=limit)


@app.command(name="dashboard")
def dashboard_cmd(
    vault_path: str = typer.Option("./ct_provenance.db", "--vault", help="Path or SQLAlchemy URL to the provenance log database."),
    vault_type: str = typer.Option("local", "--vault-type", help="Vault backend type: local, postgres, sqlalchemy."),
    limit: int = typer.Option(200, "--limit", help="Maximum recent entries to analyze."),
    web: bool = typer.Option(False, "--web", "-w", help="Launch the interactive Web Audit & Telemetry Dashboard in browser mode."),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind the Web Dashboard server to (when --web is used)."),
    port: int = typer.Option(8001, "--port", help="Port to bind the Web Dashboard server to (when --web is used)."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Optional Bearer token / API key for dashboard API authentication."),
):
    """Display the terminal audit dashboard or launch the interactive Web Audit Dashboard (--web)."""
    if web:
        console.print("🌐 Launching [bold cyan]CT Toolkit Web Audit & Telemetry Dashboard[/bold cyan]...")
        console.print(f"  - Vault: [bold yellow]{vault_path}[/bold yellow] ({vault_type})")
        console.print(f"  - Dashboard URL: [bold underline green]http://{host}:{port}/dashboard[/bold underline green]")
        start_dashboard_server(
            vault_path=vault_path,
            vault_type=vault_type,
            host=host,
            port=port,
            api_key=api_key,
        )
        return
    _render_status_dashboard(vault_path=vault_path, vault_type=vault_type, limit=limit)


if __name__ == "__main__":
    app()
