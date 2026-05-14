#!/usr/bin/env python3
"""
Multi-Agent MIDI Composition + FluidSynth Rendering
"""

import json
import time
import typer
import os
from rich.console import Console
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.text import Text
from dotenv import load_dotenv

from groupchat_manager import run_full_pipeline
from parsers import validate_abc
from bridge import run_full_pipeline as run_audio_pipeline

load_dotenv()

app = typer.Typer(help="Multi-Agent MIDI Composition System")
console = Console()


def _make_progress_display():
    """Create a Rich Live-based progress tracker. Returns (callback, render_fn)."""
    events = []
    start_time = time.time()
    max_events = 12

    def render():
        elapsed = int(time.time() - start_time)
        mins, secs = divmod(elapsed, 60)

        table = Table.grid(padding=(0, 2))
        table.add_column(style="cyan", width=18)
        table.add_column()

        # Header
        table.add_row(
            Text("Pipeline Progress", style="bold"),
            Text(f"  {mins}m {secs:02d}s elapsed", style="dim"),
        )
        table.add_row("", "")

        # Events
        for stage, summary in events[-max_events:]:
            table.add_row(
                Text(f"  {stage}", style="bold yellow"),
                Text(summary),
            )

        # Current stage indicator
        if events:
            table.add_row("", "")
            last_stage = events[-1][0]
            table.add_row(Text("  Current", style="bold green"), Text(f"{last_stage}..."))

        return Panel(table, border_style="blue", padding=(1, 2))

    def on_progress(stage: str, summary: str):
        events.append((stage, summary))

    return on_progress, render


@app.command()
def generate(
    prompt: str = typer.Argument(..., help="Music style description"),
    output_name: str = typer.Option("output", help="Base filename for outputs"),
    skip_audio: bool = typer.Option(False, help="Skip audio generation"),
    iterations: int = typer.Option(1, help="Maximum number of composition iterations"),
):
    """
    FULL MIDI COMPOSITION PIPELINE:

    1. Leader Agent: analyzes style, BPM, genre, and rhythmic character
    2. Orchestration Agent: determines instruments and arrangement
    3. Rhythm Agent: establishes the rhythmic groove
    4. Melody & Chords Agent: completes the full composition
    5. Review Gate: Orchestrator + Producer quality assurance
    6. FluidSynth: MIDI to WAV rendering with SoundFont instruments
    """
    console.print(Panel.fit(
        "[bold blue]Multi-Agent MIDI Composition[/bold blue]\n\n"
        f"[cyan]Style:[/cyan] {prompt}\n"
        f"[cyan]Output:[/cyan] {output_name}",
        title="Starting MIDI Composition"
    ))

    # Step 1: Run the multi-agent composition pipeline with live progress
    on_progress, render_fn = _make_progress_display()

    with Live(render_fn(), console=console, refresh_per_second=1, transient=False) as live:
        # Update the display after each agent event
        original_callback = on_progress
        def tracked_callback(stage, summary):
            original_callback(stage, summary)
            live.update(render_fn())

        result = run_full_pipeline(prompt, max_iterations=iterations, on_progress=tracked_callback)

    final_abc = result["abc"]
    orchestration_json = result["orchestration_json"]

    # Save ABC score
    abc_path = os.path.join("outputs", f"{output_name}.abc")
    with open(abc_path, "w") as f:
        f.write(final_abc)
    console.print(f"\nABC score saved to: {abc_path}")

    # Final ABC validation
    is_valid, error_msg = validate_abc(final_abc)
    if not is_valid:
        console.print(f"[red]ABC validation failed: {error_msg}[/red]")
    else:
        console.print("ABC validation PASSED!")

    # Build instrument config from orchestration plan
    orchestration_data = json.loads(orchestration_json)
    instrument_config = {
        'V:1': {'name': orchestration_data.get('bass_instrument', 'Electric Bass')},
        'V:2': {'name': orchestration_data.get('chords_instrument', 'Rhodes Piano')},
        'V:3': {'name': orchestration_data.get('melody_instrument', 'Electric Guitar')},
    }

    # Display results
    instr_preview = "\n".join([f"  {k}: {v['name']}" for k, v in instrument_config.items()])
    console.print(Panel(instr_preview, title="[magenta]Instrument Configuration[/magenta]"))

    if skip_audio:
        console.print("\n[yellow]Skipping audio generation.[/yellow]")
        return

    # Step 2: FluidSynth rendering
    console.print("\n[magenta]FluidSynth MIDI rendering...[/magenta]")
    try:
        wav_path = run_audio_pipeline(
            final_abc, output_name, instrument_config=instrument_config,
        )
        console.print(f"\n[bold green]COMPOSITION COMPLETE![/bold green]")
        console.print(f"   Audio: {wav_path}")
    except Exception as e:
        console.print(f"[red]Error during audio generation: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def chat_only(
    prompt: str = typer.Argument(..., help="Music style description"),
    output_name: str = typer.Option("output", help="Base filename for ABC output"),
    iterations: int = typer.Option(1, help="Maximum number of composition iterations"),
):
    """Run only the agent composition pipeline (skip audio rendering)."""
    generate(prompt, output_name=output_name, skip_audio=True, iterations=iterations)


@app.command()
def test_bridge():
    """Test the FluidSynth bridge pipeline with sample data."""
    console.print("[yellow]Testing FluidSynth bridge...[/yellow]")

    test_abc = """X:1
T:Test Loop
M:4/4
L:1/8
K:Am
V:1 Melody
C E G A | G E C D | E G B c | B G E C |
C E G A | G E C D | A c e g | c A E C |
V:2 Bass
A, C, E, A, | F, A, C, F, | G, B, D, G, | C, E, G, C, |
A, C, E, A, | F, A, C, F, | G, B, D, G, | C, E, C, A, |"""

    run_audio_pipeline(test_abc, output_name="bridge_test")


@app.callback()
def main_callback():
    """
    Multi-Agent Music Generation + FluidSynth Rendering
    """
    if not os.path.exists("outputs"):
        os.makedirs("outputs")


if __name__ == "__main__":
    app()
