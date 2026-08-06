"""chat_pipeline._split_thinking — pulls the optional [[THINKING]] reasoning block out
of a model reply so it renders as a separate 'Thinking' disclosure. Fallback-safe."""
from app.services.chat_pipeline import _split_thinking


def test_extracts_steps_and_strips_block():
    reply = ("[[THINKING]]\n- Found the LDL row in the lipid panel\n- Read the value column\n"
             "[[/THINKING]]\nLDL: 1.41 MMOL/L")
    steps, answer = _split_thinking(reply)
    assert steps == ["Found the LDL row in the lipid panel", "Read the value column"]
    assert answer == "LDL: 1.41 MMOL/L"


def test_no_block_returns_whole_text():
    steps, answer = _split_thinking("Just the answer, no reasoning.")
    assert steps == [] and answer == "Just the answer, no reasoning."


def test_empty_input():
    assert _split_thinking("") == ([], "")


def test_block_with_answer_before_and_after():
    reply = "prefix [[THINKING]]\n- a\n[[/THINKING]] suffix"
    steps, answer = _split_thinking(reply)
    assert steps == ["a"]
    assert "THINKING" not in answer and "prefix" in answer and "suffix" in answer


def test_caps_at_six_steps():
    body = "\n".join(f"- s{i}" for i in range(10))
    steps, _ = _split_thinking(f"[[THINKING]]\n{body}\n[[/THINKING]]\nans")
    assert len(steps) == 6
