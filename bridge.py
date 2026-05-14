import os
import re
import subprocess
import numpy as np
import music21
import pretty_midi
import soundfile as sf
from dotenv import load_dotenv

load_dotenv()

# Configuration - All paths are ABSOLUTE to avoid working directory conflicts
OUTPUT_DIR = os.path.abspath(os.getenv("OUTPUT_DIR", "./outputs"))
DURATION_SECONDS = int(os.getenv("DURATION_SECONDS", 30))
SF2_PATH = os.getenv("SF2_PATH", "/usr/share/sounds/sf2/FluidR3_GM.sf2")
SAMPLE_RATE = 44100  # FluidSynth standard

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ==============================================================================
# GM Instrument Mapping
# ==============================================================================

# Keyword → GM program number (order matters: first match wins)
_GM_MAPPINGS = [
    # Bass
    ("upright bass", 32), ("acoustic bass", 32), ("contrabass", 32),
    ("electric bass", 33), ("finger bass", 33), ("bass", 33),
    # Keys
    ("rhodes", 4), ("electric piano", 4), ("epiano", 4),
    ("harpsichord", 6), ("clavinet", 7),
    ("organ", 19), ("hammond", 19),
    ("pad", 89), ("synth pad", 89),
    ("synth", 81), ("synthesizer", 81),
    ("piano", 0), ("grand piano", 0), ("keys", 0),
    # Guitar
    ("nylon guitar", 24), ("classical guitar", 24),
    ("steel guitar", 25), ("acoustic guitar", 25),
    ("jazz guitar", 26), ("electric guitar", 27), ("guitar", 27),
    ("overdrive", 29), ("distortion", 30),
    # Strings
    ("violin", 40), ("viola", 41), ("cello", 42), ("contrabass", 43),
    ("strings", 48), ("string ensemble", 48), ("orchestra", 48),
    # Brass
    ("trumpet", 56), ("trombone", 57), ("tuba", 58),
    ("french horn", 60), ("horn", 60), ("brass", 61),
    # Woodwinds
    ("soprano sax", 64), ("alto sax", 65), ("tenor sax", 66),
    ("baritone sax", 67), ("sax", 65), ("saxophone", 65),
    ("oboe", 68), ("english horn", 69), ("bassoon", 70),
    ("clarinet", 71), ("piccolo", 72), ("flute", 73), ("recorder", 74),
]


def _resolve_gm_program(instrument_name: str) -> int:
    """Map an instrument name from the orchestrator to a GM program number.
    Returns 0 (Acoustic Grand Piano) if no match found.
    """
    name_lower = instrument_name.lower()
    for keyword, program in _GM_MAPPINGS:
        if keyword in name_lower:
            return program
    return 0  # Default: Acoustic Grand Piano


# ==============================================================================
# ABC → MIDI Conversion (preserved from original, enhanced with GM instruments)
# ==============================================================================

def clean_abc_for_music21(abc_content: str) -> str:
    """
    ABC Normalization: outputs sheet music strictly conforming to music21 parsing standards.
    Automatically fixes structural issues, completes missing headers, cleans invalid content.
    """
    lines = [line.strip() for line in abc_content.split('\n') if line.strip()]
    clean_lines = []

    header = {
        'X': '1',
        'T': 'Composition',
        'M': '4/4',
        'L': '1/8',
        'K': 'C'
    }

    current_voice = None
    voice_lines = []
    voice_num_to_idx = {}

    for line in lines:
        is_header = False
        for key in header.keys():
            if line.startswith(f'{key}:'):
                header[key] = line.split(':', 1)[1].strip()
                is_header = True
                break
        if is_header:
            continue

        if line.startswith('V:'):
            match = re.match(r'V:(\d+)(.*)', line)
            if match:
                voice_num = match.group(1)
                voice_rest = match.group(2).strip()
                if '%%MIDI channel 10' in voice_rest:
                    voice_name = voice_rest.split('%%MIDI')[0].strip() or {
                        '1': 'Drums', '2': 'Bass', '3': 'Rhodes', '4': 'Lead'
                    }.get(voice_num, f'Voice{voice_num}')
                    voice_line = f'V:{voice_num} name="{voice_name}" %%MIDI channel 10'
                else:
                    voice_name = voice_rest or {
                        '1': 'Drums', '2': 'Bass', '3': 'Rhodes', '4': 'Lead'
                    }.get(voice_num, f'Voice{voice_num}')
                    voice_line = f'V:{voice_num} name="{voice_name}"'
                current_voice = voice_num
                if voice_num not in voice_num_to_idx:
                    voice_lines.append({
                        'voice_line': voice_line,
                        'music_lines': []
                    })
                    voice_num_to_idx[voice_num] = len(voice_lines) - 1
            continue

        inline_voice_match = re.match(r'^\[V:(\d+)\]\s*(.*)', line)
        if inline_voice_match:
            voice_num = inline_voice_match.group(1)
            rest_of_line = inline_voice_match.group(2).strip()
            current_voice = voice_num
            if voice_num not in voice_num_to_idx:
                voice_lines.append({
                    'voice_line': f'V:{voice_num} name="Voice{voice_num}"',
                    'music_lines': []
                })
                voice_num_to_idx[voice_num] = len(voice_lines) - 1
            if rest_of_line:
                line = rest_of_line
            else:
                continue

        if line.startswith('%') and '|' not in line:
            continue

        if current_voice and any(c in line for c in 'ABCDEFGabcdefg|z[]'):
            line = re.sub(r'(^|\|)\s*[A-G][#b]?(maj|min|dim|aug|m|sus|M|mi)[0-9]*(/[A-G][#b]?)?\s+', r'\1 ', line)
            line = re.sub(r'"[A-Za-z0-9#()/+-]+"\s*', '', line)
            line = re.sub(r'[^ABCDEFGabcdefg0-9/\.|z\s,\'\[\]]', '', line)
            line = re.sub(r'\s+', ' ', line).strip()

            if not line.startswith(('|', ' ')):
                line = ' ' + line
            if not line.endswith('|'):
                line += '|'

            if current_voice in voice_num_to_idx:
                voice_idx = voice_num_to_idx[current_voice]
                voice_lines[voice_idx]['music_lines'].append(line)

    clean_lines.extend([
        f'X:{header["X"]}',
        f'T:{header["T"]}',
        f'M:{header["M"]}',
        f'L:{header["L"]}',
        f'K:{header["K"]}'
    ])

    voice_count = 0
    for voice_entry in voice_lines:
        if voice_count >= 4:
            break
        clean_lines.append(voice_entry['voice_line'])
        clean_lines.extend(voice_entry['music_lines'])
        voice_count += 1

    final_abc = '\n'.join([l for l in clean_lines if l.strip()])
    print(f"   ABC normalization: {voice_count} voices, key={header['K']}")
    return final_abc, header


def abc_to_midi(abc_content: str, output_path: str, instrument_config: dict = None) -> str:
    """Convert ABC notation to MIDI with correct GM instrument programs per voice.
    Returns ABSOLUTE path to the generated MIDI file.
    """
    midi_path = output_path.replace(".wav", ".mid")
    clean_abc, header = clean_abc_for_music21(abc_content)

    score = music21.converter.parseData(clean_abc, format="abc")

    bpm = int(header.get('Q', '120').split('=')[-1]) if '=' in header.get('Q', '120') else int(header.get('Q', '120'))
    score.insert(0, music21.tempo.MetronomeMark(number=bpm))

    time_sig = music21.meter.TimeSignature(header.get('M', '4/4'))
    bar_duration = time_sig.barDuration.quarterLength
    max_note_duration = bar_duration * 2

    # Voice order: V:1 = bass, V:2 = chords, V:3 = melody
    default_config = {
        'V:1': {'name': 'Electric Bass'},
        'V:2': {'name': 'Rhodes Piano'},
        'V:3': {'name': 'Electric Guitar'},
    }
    if instrument_config:
        default_config.update(instrument_config)

    voice_keys = ['V:1', 'V:2', 'V:3', 'V:4']

    processed_parts = []
    if hasattr(score, 'parts'):
        for part_idx, part in enumerate(score.parts[:4]):
            part_name = part.partName if part.partName else f'Voice{part_idx + 1}'
            new_part = music21.stream.Part()
            new_part.partName = part_name

            midi_channel = part_idx

            # Resolve GM program for this voice
            voice_key = voice_keys[part_idx] if part_idx < len(voice_keys) else None
            if voice_key and voice_key in default_config:
                instr_name = default_config[voice_key].get('name', '')
                gm_program = _resolve_gm_program(instr_name)
            else:
                gm_program = 0

            inst = music21.instrument.Piano()
            inst.midiChannel = midi_channel
            inst.midiProgram = gm_program
            new_part.insert(0, inst)

            for el in part.flatten():
                if isinstance(el, (music21.note.Note, music21.chord.Chord, music21.note.Rest)):
                    if el.duration.quarterLength > max_note_duration:
                        el.duration.quarterLength = max_note_duration
                    elif el.duration.quarterLength <= 0:
                        el.duration.quarterLength = 0.125

                    if isinstance(el, (music21.note.Note, music21.chord.Chord)):
                        el.midiChannel = midi_channel

                    new_part.append(el)

            part_time_sig = music21.meter.TimeSignature(header.get('M', '4/4'))
            new_part = new_part.makeMeasures(part_time_sig)
            processed_parts.append(new_part)

    new_score = music21.stream.Score()
    new_score.insert(0, music21.tempo.MetronomeMark(number=bpm))
    for part in processed_parts:
        new_score.append(part)

    if len(new_score.parts) == 0:
        raise RuntimeError("MIDI generation failed! No valid tracks found in ABC notation")

    new_score.write("midi", midi_path)

    if not os.path.exists(midi_path) or os.path.getsize(midi_path) == 0:
        raise RuntimeError(f"MIDI generation failed! File empty or does not exist: {midi_path}")

    # Validate and fix instrument programs with pretty_midi
    pm = pretty_midi.PrettyMIDI(midi_path)
    for track_idx, inst in enumerate(pm.instruments[:4]):
        voice_key = voice_keys[track_idx] if track_idx < len(voice_keys) else None
        if voice_key and voice_key in default_config:
            instr_name = default_config[voice_key].get('name', '')
            gm_program = _resolve_gm_program(instr_name)
            inst.program = gm_program
            print(f"   Track {track_idx}: {instr_name} → GM program {gm_program}")
    pm.write(midi_path)

    print(f"   MIDI generated: {midi_path} ({os.path.getsize(midi_path)} bytes, {len(new_score.parts)} tracks)")
    return midi_path


# ==============================================================================
# FluidSynth Rendering
# ==============================================================================

def render_midi_with_fluidsynth(
    midi_path: str,
    sf2_path: str = SF2_PATH,
    sr: int = SAMPLE_RATE,
    instrument_config: dict = None,
) -> np.ndarray:
    """Render a MIDI file to audio using FluidSynth + SoundFont.
    Faithfully plays back every note as written in the MIDI.
    Returns a normalized numpy array.
    """
    pm = pretty_midi.PrettyMIDI(midi_path)

    # Assign GM programs per track
    voice_keys = ['V:1', 'V:2', 'V:3', 'V:4']
    if instrument_config:
        for track_idx, inst in enumerate(pm.instruments[:4]):
            voice_key = voice_keys[track_idx] if track_idx < len(voice_keys) else None
            if voice_key and voice_key in instrument_config:
                instr_name = instrument_config[voice_key].get('name', '')
                gm_program = _resolve_gm_program(instr_name)
                inst.program = gm_program

    # Render with FluidSynth
    print(f"   Rendering with FluidSynth + {os.path.basename(sf2_path)}...")
    audio = pm.fluidsynth(fs=sr, synthesizer=sf2_path)

    # Trim or pad to target duration
    target_samples = int(DURATION_SECONDS * sr)
    if len(audio) > target_samples:
        audio = audio[:target_samples]
    elif len(audio) < target_samples:
        audio = np.pad(audio, (0, target_samples - len(audio)))

    # Normalize
    max_val = np.abs(audio).max()
    if max_val > 0:
        audio = audio / max_val * 0.8

    duration = len(audio) / sr
    print(f"   FluidSynth render complete: {duration:.1f}s")
    return audio


# ==============================================================================
# Full Pipeline
# ==============================================================================

def run_full_pipeline(
    abc_content: str,
    output_name: str = "output",
    instrument_config: dict = None,
) -> str:
    """ABC → MIDI → FluidSynth → WAV. Returns path to the final WAV file."""
    print("=" * 70)
    print("ABC → MIDI → FluidSynth + SoundFont → WAV")
    print("=" * 70)

    default_config = {
        'V:1': {'name': 'Electric Bass'},
        'V:2': {'name': 'Rhodes Piano'},
        'V:3': {'name': 'Electric Guitar'},
    }
    if instrument_config:
        default_config.update(instrument_config)

    # Step 1: ABC → MIDI
    midi_path = os.path.join(OUTPUT_DIR, f"{output_name}.mid")
    abc_to_midi(abc_content, midi_path, instrument_config=default_config)

    # Step 2: FluidSynth render
    print(f"\nFluidSynth rendering...")
    audio = render_midi_with_fluidsynth(midi_path, instrument_config=default_config)

    # Step 3: Save WAV
    wav_path = os.path.join(OUTPUT_DIR, f"{output_name}.wav")
    sf.write(wav_path, audio, SAMPLE_RATE)

    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE!")
    print(f"   MIDI: {midi_path}")
    print(f"   WAV:  {wav_path}")
    print("=" * 70)

    return wav_path


if __name__ == "__main__":
    test_abc = """X:1
T:Test
M:4/4
L:1/8
K:C
V:1 Melody
C E G A | G E C D | E G B c | B G E C |
V:2 Chords
[CEGc] [CEGc] [FAc] [FAc] [GBd] [GBd] [CEGc] [CEGc]|
V:3 Bass
C, C, C, C, | F, F, F, F, | G, G, G, G, | C, C, C, C, |"""
    run_full_pipeline(test_abc, "warm lo-fi beat", "pipeline_test")
