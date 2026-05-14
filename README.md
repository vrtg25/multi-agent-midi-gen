# Multi-Agent-Midi-Gen

A multi-agent collaborative MIDI music composition pipeline.

Input a text description, get a MIDI file rendered to WAV audio.

## Setup

```bash
1. System dependencies
sudo apt install -y fluidsynth fluid-soundfont

2. Python dependencies
uv sync

3. API setting
cp .env.example .env
```

## Usage

```bash
# Full pipeline: compose + render audio
uv run python main.py generate "YOUR DESCRIPTION"

# Compose only, skip audio rendering
uv run python main.py chat-only "YOUR DESCRIPTION"

# Test audio pipeline
uv run python main.py test-bridge
```

Output files in `outputs/`:

| Format | Description |
|--------|-------------|
| `.mid` | MIDI file, importable to any DAW |
| `.wav` | Preview audio (FluidSynth + GM SoundFont) |
| `.abc` | ABC notation source |

## License

MIT
