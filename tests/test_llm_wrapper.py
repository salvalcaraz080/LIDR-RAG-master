"""llm_wrapper helper tests — no API cost (fake stream chunks)."""

from types import SimpleNamespace

from app.services.llm_wrapper import _model_from_chunks, _usage_from_chunks


def _chunk(model=None, usage=None):
    return SimpleNamespace(model=model, usage=usage)


def test_usage_from_chunks_reads_final_usage_chunk():
    usage = SimpleNamespace(prompt_tokens=83, completion_tokens=45, total_tokens=128)
    chunks = [_chunk(model="gpt-4o-mini"), _chunk(model="gpt-4o-mini", usage=usage)]
    assert _usage_from_chunks(chunks) == {
        "input_tokens": 83,
        "output_tokens": 45,
        "total_tokens": 128,
    }


def test_usage_from_chunks_zero_when_no_usage():
    chunks = [_chunk(model="gpt-4o-mini"), _chunk(model="gpt-4o-mini")]
    assert _usage_from_chunks(chunks) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def test_usage_from_chunks_handles_empty():
    assert _usage_from_chunks(None)["total_tokens"] == 0


def test_model_from_chunks_reflects_actual_model():
    chunks = [_chunk(model="claude-haiku-4-5-20251001")]
    assert _model_from_chunks(chunks, "openai/gpt-4o-mini") == "claude-haiku-4-5-20251001"


def test_model_from_chunks_falls_back_to_default():
    assert _model_from_chunks([], "openai/gpt-4o-mini") == "openai/gpt-4o-mini"
