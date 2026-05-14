import re
from typing import List, Optional
from dataclasses import dataclass

import music21


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass
class OrchestrationPlan:
    """Structured data for orchestration plan"""
    key: str
    tempo: str
    total_bars: int
    has_drums: bool
    drum_description: str
    bass_instrument: str
    bass_timbre: str
    chords_instrument: str
    chords_timbre: str
    melody_instrument: str
    melody_timbre: str
    chord_progression: List[str]
    production_notes: str
    style_description: str


@dataclass
class CompositionResult:
    """Structured data for composition result"""
    abc_content: str
    theory_analysis: str


@dataclass
class ReviewResult:
    """Structured data for review result"""
    reviewer: str  # orchestration, leader, review_agent
    passed: bool
    score: int
    feedback: str


# ==============================================================================
# Parsing Functions
# ==============================================================================

def parse_orchestration_plan(text: str, user_prompt: str) -> OrchestrationPlan:
    """Extract structured orchestration plan from text.
    Raises ValueError on any missing required field.
    """
    key_match = re.search(r'Key:\s*([^\n]+)', text)
    if not key_match:
        raise ValueError("Orchestration plan missing required field: Key")
    key = key_match.group(1).strip()

    tempo_match = re.search(r'Tempo:\s*([^\n]+)', text)
    if not tempo_match:
        raise ValueError("Orchestration plan missing required field: Tempo")
    tempo = tempo_match.group(1).strip()

    bars_match = re.search(r'Total Bars:\s*(\d+)', text)
    if not bars_match:
        raise ValueError("Orchestration plan missing required field: Total Bars")
    total_bars = int(bars_match.group(1))

    has_drums = "no drums" not in text.lower()

    drum_section = re.search(r'Drums:\s*(.*?)(?=Instruments:|Overall Production Notes:|---|$)', text, re.DOTALL)
    drum_description = drum_section.group(1).strip() if drum_section else ""
    if "no drums" in drum_description.lower():
        has_drums = False
        drum_description = ""

    bass_match = re.search(r'`V:1:\s*([^`-]+)\s*-\s*([^`]+)`', text)
    if not bass_match:
        raise ValueError("Orchestration plan missing V:1 Bass instrument definition")
    bass_instrument = bass_match.group(1).strip()
    bass_timbre = bass_match.group(2).strip()

    chords_match = re.search(r'`V:2:\s*([^`-]+)\s*-\s*([^`]+)`', text)
    if not chords_match:
        raise ValueError("Orchestration plan missing V:2 Chords instrument definition")
    chords_instrument = chords_match.group(1).strip()
    chords_timbre = chords_match.group(2).strip()

    melody_match = re.search(r'`V:3:\s*([^`-]+)\s*-\s*([^`]+)`', text)
    if not melody_match:
        raise ValueError("Orchestration plan missing V:3 Melody instrument definition")
    melody_instrument = melody_match.group(1).strip()
    melody_timbre = melody_match.group(2).strip()

    chord_prog_match = re.search(r'Chord Progression.*?:\s*(.*?)(?=Overall Production Notes:|---|$)', text, re.DOTALL)
    if not chord_prog_match:
        raise ValueError("Orchestration plan missing required field: Chord Progression")
    chords_text = chord_prog_match.group(1).strip()
    chord_progression = re.findall(r'[A-G][#b]?(?:maj|min|dim|aug|m|sus)?[0-9]*(?:/[A-G][#b]?)?', chords_text)
    if not chord_progression:
        raise ValueError("Orchestration plan chord progression is empty or unparseable")

    prod_notes_match = re.search(r'Overall Production Notes:\s*(.*?)(?=---|$)', text, re.DOTALL)
    production_notes = prod_notes_match.group(1).strip() if prod_notes_match else ""

    return OrchestrationPlan(
        key=key,
        tempo=tempo,
        total_bars=total_bars,
        has_drums=has_drums,
        drum_description=drum_description,
        bass_instrument=bass_instrument,
        bass_timbre=bass_timbre,
        chords_instrument=chords_instrument,
        chords_timbre=chords_timbre,
        melody_instrument=melody_instrument,
        melody_timbre=melody_timbre,
        chord_progression=chord_progression,
        production_notes=production_notes,
        style_description=user_prompt
    )


def auto_fix_abc_format(abc_content: str) -> str:
    """Auto-fix common ABC notation format issues"""
    lines = abc_content.split('\n')
    fixed_lines = []
    has_k_header = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith('K:'):
            has_k_header = True

        # Fix commas in chords: [C,E,G] -> [CEG]
        if '[' in line and ']' in line and ',' in line:
            def remove_comma_from_chords(match):
                return match.group(0).replace(',', '')
            line = re.sub(r'\[[^\]]+\]', remove_comma_from_chords, line)

        fixed_lines.append(line)

    if not has_k_header:
        insert_pos = 0
        for i, line in enumerate(fixed_lines):
            if line.startswith('L:') or line.startswith('Q:'):
                insert_pos = i + 1
        fixed_lines.insert(insert_pos, 'K:C')

    return '\n'.join(fixed_lines)


def parse_composition_result(text: str) -> CompositionResult:
    """Extract structured composition result from agent output with auto-fix"""
    abc_match = re.search(r'---(?:ABC MUSIC|RHYTHM ABC)---\s*(.*?)\s*---END (?:ABC|RHYTHM)---', text, re.DOTALL)
    if not abc_match:
        abc_match = re.search(r'(X:\d+.*?(?:V:\d+.*?\|.*)+)', text, re.DOTALL)
    abc_content = abc_match.group(1).strip() if abc_match else ""

    lines = abc_content.split('\n')
    clean_lines = []
    for line in lines:
        if line.startswith(('X:', 'T:', 'M:', 'L:', 'Q:', 'K:', 'V:')) or '|' in line:
            clean_lines.append(line)
    abc_content = '\n'.join(clean_lines)

    abc_content = auto_fix_abc_format(abc_content)

    theory_match = re.search(r'---THEORY ANALYSIS---\s*(.*?)\s*---ABC MUSIC---', text, re.DOTALL)
    theory_analysis = theory_match.group(1).strip() if theory_match else ""

    return CompositionResult(
        abc_content=abc_content,
        theory_analysis=theory_analysis
    )


def validate_abc_with_music21(abc_content: str) -> tuple[bool, List[str]]:
    """Validate ABC notation using music21 (3 voices required)."""
    errors = []
    lines = [line.rstrip('\n') for line in abc_content.split('\n')]
    clean_lines = [line.strip() for line in lines if line.strip()]

    voice_count = sum(1 for line in clean_lines if line.startswith('V:'))
    if voice_count < 2:
        errors.append(f"Expected at least 2 voices, found {voice_count}")

    try:
        music21.converter.parse(abc_content, format='abc')
    except Exception as e:
        errors.append(f"ABC format error: {str(e)[:80]}")

    has_3_voices = voice_count >= 3
    has_bars = any('|' in line for line in clean_lines if not line.startswith('V:'))

    return has_3_voices and has_bars, errors


def parse_leader_analysis(text: str) -> dict:
    """Extract structured analysis from Leader agent output.
    Raises ValueError on missing required fields.
    """
    result = {}

    bpm_match = re.search(r'BPM:\s*(\d+)', text)
    if not bpm_match:
        raise ValueError("Leader analysis missing required field: BPM")
    result['bpm'] = bpm_match.group(1)

    genre_match = re.search(r'GENRE:\s*([^\n]+)', text)
    if not genre_match:
        raise ValueError("Leader analysis missing required field: GENRE")
    result['genre'] = genre_match.group(1).strip()

    # Support both RHYTHMIC CHARACTER and MOOD/TEXTURE
    rhythm_match = re.search(r'RHYTHMIC CHARACTER:\s*([^\n]+)', text)
    if rhythm_match:
        result['rhythmic_character'] = rhythm_match.group(1).strip()

    mood_match = re.search(r'(?:MOOD|TEXTURE):\s*([^\n]+)', text)
    if not mood_match:
        raise ValueError("Leader analysis missing required field: MOOD or TEXTURE")
    result['mood'] = mood_match.group(1).strip()

    return result




def parse_review_from_text(text: str) -> tuple[bool, int, str]:
    """Extract review result from text (passed, score, feedback)"""
    score_match = re.search(r'(?:Quality|Score|评分).*?(\d+)(?:/10)?', text, re.IGNORECASE)
    score = int(score_match.group(1)) if score_match else 5
    if score > 10:
        score = 10

    passed_keywords = ["pass", "passed", "通过", "accept", "approved"]
    reject_keywords = ["reject", "rejected", "不通过", "未通过", "rework", "需要修改"]

    passed = score >= 8
    for kw in passed_keywords:
        if kw.lower() in text.lower():
            passed = True
            break
    for kw in reject_keywords:
        if kw.lower() in text.lower():
            passed = False
            break

    feedback = text.strip()
    feedback = re.sub(r'^.*?(?:Quality|Score|评分).*?(?=\n|$)', '', feedback, flags=re.IGNORECASE)
    feedback = feedback.strip()

    return passed, score, feedback


def validate_abc(abc_content: str) -> tuple[bool, str]:
    """ABC validation with formatted error output."""
    is_valid, errors = validate_abc_with_music21(abc_content)
    return is_valid, "\n".join([f"- {e}" for e in errors]) if not is_valid else ""
