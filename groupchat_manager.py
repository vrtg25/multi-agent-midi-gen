import json
from typing import Callable, Dict, Optional
from dataclasses import asdict

from autogen import (
    Agent,
    GroupChat,
    GroupChatManager,
)

from agents import (
    leader_agent,
    orchestration_agent,
    rhythm_agent,
    melody_chords_agent,
    review_agent,
)
from parsers import (
    OrchestrationPlan,
    CompositionResult,
    ReviewResult,
    parse_orchestration_plan,
    parse_composition_result,
    parse_leader_analysis,
    parse_review_from_text,
        validate_abc_with_music21,
)


# ==============================================================================
# Pipeline State
# ==============================================================================

class PipelineState:
    """Pipeline state - collaborative discussion closed loop"""
    def __init__(self):
        self.user_prompt = ""
        self.max_iterations = 12
        self.current_iteration = 0

        self.stage = "LEADER_ANALYSIS"
        self.discussion_round = 0
        self.last_speaker = ""

        # Stage outputs
        self.leader_analysis: dict = None
        self.plan: Optional[OrchestrationPlan] = None
        self.plan_confirmed = False
        self.rhythm_draft: Optional[CompositionResult] = None
        self.rhythm_confirmed = False
        self.composition: Optional[CompositionResult] = None

        # Final review results (2-layer)
        self.tech_review: Optional[ReviewResult] = None
        self.final_review: Optional[ReviewResult] = None

        # Best version preservation
        self.best_composition: Optional[CompositionResult] = None
        self.best_score = 0

        self.is_complete = False

    def is_all_reviews_passed(self) -> bool:
        return (
            self.tech_review and self.tech_review.passed
            and self.final_review and self.final_review.passed
        )

    def can_continue(self) -> bool:
        return self.current_iteration < self.max_iterations


# ==============================================================================
# Review Handler
# ==============================================================================

def _handle_review_message(state: PipelineState, last_name: str, last_content: str,
                           on_progress: Callable[[str, str], None] = None) -> Optional[Agent]:
    """Process a single review message during the FINAL_REVIEW stage.

    2-layer review:
      1. Orchestrator → technical review (tech_review)
      2. Producer     → final quality score (final_review)
    """
    def _p(stage, summary):
        if on_progress:
            on_progress(stage, summary)

    # Layer 1: Orchestrator technical check
    if last_name == "Orchestration" and state.tech_review is None:
        try:
            passed, score, feedback = parse_review_from_text(last_content)
            state.tech_review = ReviewResult(reviewer="orchestration", passed=passed, score=score, feedback=feedback)
            _p("Tech Review", f"Orchestrator: {'PASS' if passed else 'REWORK'} ({score}/10)")
        except Exception as e:
            _p("Tech Review", f"Parse failed: {e}")
        return None

    # Layer 2: Producer final quality score
    if last_name == "Review" and state.tech_review and state.final_review is None:
        try:
            passed, score, feedback = parse_review_from_text(last_content)
            state.final_review = ReviewResult(reviewer="review_agent", passed=passed, score=score, feedback=feedback)
            state.current_iteration += 1
            _p("Final Review", f"Producer: {'PASS' if passed else 'REWORK'} ({score}/10)")

            # Always track the best score seen (even if not passed)
            if score > state.best_score:
                state.best_score = score
                state.best_composition = state.composition

            if passed and state.is_all_reviews_passed():
                state.is_complete = True
                _p("Complete", f"All reviews passed! Score: {score}/10")
                return None

            if state.current_iteration >= state.max_iterations:
                state.is_complete = True
                if state.best_composition:
                    state.composition = state.best_composition
                _p("Complete", f"Max iterations reached. Best score: {state.best_score}/10")
                return None
        except Exception as e:
            _p("Final Review", f"Parse failed: {e}")
        return None

    return None


# ==============================================================================
# Speaker Selection Logic
# ==============================================================================

def _make_speaker_selector(state: PipelineState, on_progress: Callable[[str, str], None] = None):
    """Factory: returns a speaker-selection closure bound to the given PipelineState.

    on_progress(stage, summary) is called at key pipeline events for UI feedback.
    """
    def _progress(stage: str, summary: str):
        if on_progress:
            on_progress(stage, summary)

    def _select_speaker(last_speaker: Agent, groupchat: GroupChat) -> Optional[Agent]:
        if state.is_complete:
            return None

        last_messages = groupchat.messages
        if last_messages:
            last_msg = last_messages[-1]
            last_content = last_msg.get("content", "")
            last_name = last_msg.get("name", "")
            state.last_speaker = last_name

            # ===== PARSE OUTPUTS =====

            if last_name == "Leader" and "---LEADER ANALYSIS---" in last_content and state.leader_analysis is None:
                try:
                    state.leader_analysis = parse_leader_analysis(last_content)
                    la = state.leader_analysis
                    _progress("Leader Analysis", f"BPM {la.get('bpm')} | {la.get('genre')} | {la.get('texture')}")
                except Exception as e:
                    _progress("Leader Analysis", f"Parse failed: {e}")

            if last_name == "Orchestration" and ("---ORCHESTRATION PLAN---" in last_content or "Key:" in last_content):
                if state.plan is None or not state.plan_confirmed:
                    try:
                        state.plan = parse_orchestration_plan(last_content, state.user_prompt)
                        _progress("Orchestration Plan", f"{state.plan.key} | {state.plan.tempo} | round {state.discussion_round}")
                    except Exception as e:
                        _progress("Orchestration Plan", f"Parse failed: {e}")

            if last_name == "Rhythm" and ("---RHYTHM ABC---" in last_content or "V:1" in last_content):
                if state.rhythm_draft is None or not state.rhythm_confirmed:
                    try:
                        state.rhythm_draft = parse_composition_result(last_content)
                        _progress("Rhythm Draft", f"3-voice rhythm skeletons submitted | round {state.discussion_round}")
                    except Exception as e:
                        _progress("Rhythm Draft", f"Parse failed: {e}")

            if last_name == "Melody_Chords" and "---ABC MUSIC---" in last_content:
                if state.composition is None:
                    try:
                        parsed = parse_composition_result(last_content)
                        is_valid, tech_errors = validate_abc_with_music21(parsed.abc_content)
                        if is_valid:
                            state.composition = parsed
                            _progress("Composition", f"Full composition submitted ({len(parsed.abc_content)} chars) | validation PASSED")
                        else:
                            _progress("Composition", f"Format issues detected, needs revision")
                    except Exception as e:
                        _progress("Composition", f"Parse failed: {e}")

            confirm_keywords = ["confirmed", "confirm", "looks good", "approved", "pass", "✅"]
            if last_name == "Leader":
                if any(kw in last_content.lower() for kw in confirm_keywords):
                    if state.stage == "PLAN_DISCUSSION" and state.plan and not state.plan_confirmed:
                        state.plan_confirmed = True
                        state.discussion_round = 0
                        _progress("Plan Confirmed", "Orchestration plan approved by Leader")
                    elif state.stage == "RHYTHM_DISCUSSION" and state.rhythm_draft and not state.rhythm_confirmed:
                        state.rhythm_confirmed = True
                        state.discussion_round = 0
                        _progress("Rhythm Confirmed", "Rhythm skeletons approved by Leader")

            # Final review
            if state.stage == "FINAL_REVIEW":
                result = _handle_review_message(state, last_name, last_content, on_progress)
                if result is not None:
                    return result

        # ===== STATE TRANSITIONS =====

        # Stage 1: Leader Analysis
        if state.stage == "LEADER_ANALYSIS":
            if state.leader_analysis is None:
                _progress("Leader Analysis", "Analyzing style, BPM, genre, texture...")
                return leader_agent
            if state.last_speaker == "Leader":
                _progress("Leader Analysis", "Orchestrator reviewing analysis...")
                return orchestration_agent
            state.stage = "PLAN_DISCUSSION"
            state.discussion_round = 0

        # Stage 2: Plan Discussion
        if state.stage == "PLAN_DISCUSSION":
            if not state.plan_confirmed:
                if state.plan is None:
                    _progress("Plan Discussion", "Orchestrator designing arrangement...")
                    return orchestration_agent
                if state.last_speaker == "Orchestration" and state.discussion_round < 2:
                    state.discussion_round += 1
                    _progress("Plan Discussion", f"Leader reviewing plan (round {state.discussion_round})...")
                    return leader_agent
                if state.last_speaker == "Leader" and state.discussion_round < 2:
                    _progress("Plan Discussion", f"Orchestrator revising plan...")
                    return orchestration_agent
                if state.discussion_round >= 2:
                    state.plan_confirmed = True
                    _progress("Plan Discussion", "Max rounds reached, plan confirmed")
            if state.plan_confirmed:
                state.stage = "RHYTHM_DISCUSSION"
                state.discussion_round = 0

        # Stage 3: Rhythm Discussion
        if state.stage == "RHYTHM_DISCUSSION":
            if not state.rhythm_confirmed:
                if state.rhythm_draft is None:
                    orchestration_msg = next((m for m in reversed(groupchat.messages) if m.get("name") == "Orchestration" and "---ORCHESTRATION PLAN---" in m.get("content", "")), None)
                    if orchestration_msg and state.leader_analysis:
                        context_msg = {
                            "role": "user",
                            "name": "System",
                            "content": f"""CONTEXT FOR RHYTHM AGENT:

====================
LEADER'S STYLE ANALYSIS:
   GENRE: {state.leader_analysis['genre']}
   RHYTHMIC CHARACTER: {state.leader_analysis.get('rhythmic_character', 'Standard rhythmic approach')}
   MOOD: {state.leader_analysis['mood']}
   BPM: {state.leader_analysis['bpm']}
====================

ORCHESTRATION PLAN:
{orchestration_msg.get("content", "")}

INSTRUCTIONS:
1. Design COMPLETE RHYTHMIC PATTERNS for ALL THREE VOICES - durations, rests, and articulations ONLY
2. CRITICAL: Apply the RHYTHMIC CHARACTER specified above (swing, syncopation, groove patterns)
3. Reflect BOTH the orchestration plan AND the Leader's style/rhythm analysis
4. Do NOT add pitch content (no note names!)
5. All three voices must rhythmically complement each other
6. Follow the BPM, key, and chord progression from the plan
"""
                        }
                        groupchat.messages.append(context_msg)
                        _progress("Rhythm Discussion", "Context injected to Rhythm Agent")
                    return rhythm_agent
                if state.last_speaker == "Rhythm" and state.discussion_round < 2:
                    state.discussion_round += 1
                    _progress("Rhythm Discussion", f"Orchestrator reviewing rhythms (round {state.discussion_round})...")
                    return orchestration_agent
                if state.last_speaker == "Orchestration" and state.discussion_round < 2:
                    _progress("Rhythm Discussion", "Leader reviewing rhythms...")
                    return leader_agent
                if state.last_speaker == "Leader" and state.discussion_round < 2:
                    _progress("Rhythm Discussion", "Rhythm Agent revising...")
                    return rhythm_agent
                if state.discussion_round >= 2:
                    state.rhythm_confirmed = True
                    _progress("Rhythm Discussion", "Max rounds reached, rhythm confirmed")
            if state.rhythm_confirmed:
                state.stage = "COMPOSITION_DISCUSSION"
                state.discussion_round = 0
                orchestration_msg = next((m for m in reversed(groupchat.messages) if m.get("name") == "Orchestration" and "---ORCHESTRATION PLAN---" in m.get("content", "")), None)
                rhythm_msg = next((m for m in reversed(groupchat.messages) if m.get("name") == "Rhythm" and "---RHYTHM ABC---" in m.get("content", "")), None)
                if orchestration_msg and rhythm_msg and state.leader_analysis:
                    context_msg = {
                        "role": "user", "name": "System",
                        "content": f"""CONTEXT FOR MELODY & CHORDS AGENT:
Build upon these foundations - DO NOT REWRITE RHYTHM PATTERNS!

STYLE ANALYSIS:
   GENRE: {state.leader_analysis['genre']}
   RHYTHMIC CHARACTER: {state.leader_analysis.get('rhythmic_character', 'Standard rhythmic approach')}
   MOOD: {state.leader_analysis['mood']}
   BPM: {state.leader_analysis['bpm']}

ORCHESTRATION PLAN (CONFIRMED):
{orchestration_msg.get("content", "")}

RHYTHM SKELETONS (CONFIRMED - DO NOT CHANGE):
{rhythm_msg.get("content", "")}

INSTRUCTIONS:
1. DO NOT CHANGE ANY RHYTHM PATTERNS. The rhythms are FINAL.
2. Apply pitch content ONLY
3. Follow the key, BPM, and chord progression from the Orchestration Plan
4. Match the genre/mood in articulation and dynamics
"""
                    }
                    groupchat.messages.append(context_msg)
                    _progress("Composition", "Context injected to Melody & Chords Agent")

        # Stage 4: Composition Discussion
        if state.stage == "COMPOSITION_DISCUSSION":
            if state.composition is None:
                return melody_chords_agent
            state.stage = "FINAL_REVIEW"
            state.discussion_round = 0

        # Stage 5: Final Review
        if state.stage == "FINAL_REVIEW":
            if state.tech_review is None:
                return orchestration_agent
            if state.final_review is None:
                return review_agent
            return None

        if state.can_continue():
            return melody_chords_agent

        return None

    return _select_speaker


# ==============================================================================
# Pipeline Entry Point
# ==============================================================================

def run_full_pipeline(user_prompt: str, max_iterations: int = 12,
                      on_progress: Callable[[str, str], None] = None) -> Dict:
    """Run the full multi-agent composition pipeline. Returns structured result dict."""
    state = PipelineState()
    state.user_prompt = user_prompt
    state.max_iterations = max_iterations

    speaker_selector = _make_speaker_selector(state, on_progress)

    print("=" * 70)
    print("Multi-Agent MIDI Composition Pipeline")
    print(f"User request: {user_prompt}")
    print(f"Max iterations: {max_iterations}")
    print("=" * 70)

    agents_list = [
        leader_agent,
        orchestration_agent,
        rhythm_agent,
        melody_chords_agent,
        review_agent,
    ]

    initial_message = f"""
Team members, we need to produce music for the following style: {user_prompt}

【WORKFLOW】
1. Orchestrator: Design a complete orchestration plan including: key, tempo, drum description, 3 voices with instruments and timbres, chord progression, production notes.
2. Rhythm Agent: Write rhythmic patterns for V:1 Bass, V:2 Chords, V:3 Melody.
3. Melody & Chords Agent: Apply pitch content to the rhythm skeletons.
4. Orchestrator: Review technical quality. Provide a score and feedback.
5. Producer: Final quality review and scoring, score >= 8/10 passes.

【ITERATION RULES】
- If any review fails, the Melody & Chords Agent revises based on feedback.
- Maximum {max_iterations} iterations.

Orchestrator please start!
"""

    groupchat = GroupChat(
        agents=agents_list,
        messages=[],
        max_round=max_iterations * 10 + 10,
        speaker_selection_method=speaker_selector,
        allow_repeat_speaker=True,
        send_introductions=False,
    )

    manager_llm_config = {
        "config_list": leader_agent.llm_config["config_list"],
        "temperature": 0,
    }

    manager = GroupChatManager(
        groupchat=groupchat,
        llm_config=manager_llm_config,
        is_termination_msg=lambda x: state.is_complete,
        system_message="You are the coordinator for this music production team.",
    )

    print("\nStarting multi-agent collaboration...")

    leader_agent.initiate_chat(manager, message=initial_message)

    # Finalize results
    if not state.composition and state.best_composition:
        state.composition = state.best_composition

    print("\n" + "=" * 70)
    print(f"Pipeline complete!")
    print(f"   Iterations: {state.current_iteration}")
    print(f"   Final score: {state.best_score}/10")
    if state.composition:
        print(f"   ABC length: {len(state.composition.abc_content)} chars")
    print("=" * 70)

    return {
        "abc": state.composition.abc_content if state.composition else "",
        "orchestration_json": json.dumps(asdict(state.plan), ensure_ascii=False, indent=2) if state.plan else "{}",
        "theory_analysis": state.composition.theory_analysis if state.composition else "",
        "iterations_used": state.current_iteration,
        "final_score": state.best_score,
        "drum_description": state.plan.drum_description if state.plan else "",
        "has_drums": state.plan.has_drums if state.plan else False,
    }


if __name__ == "__main__":
    result = run_full_pipeline("warm lo-fi hip hop beat", max_iterations=1)
    print("\nFinal result:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
