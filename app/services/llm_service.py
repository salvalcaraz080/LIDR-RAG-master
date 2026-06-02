from openai import AsyncOpenAI
from app.config import get_settings
from app.context.examples import ESTIMATION_EXAMPLES

settings = get_settings()
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

def format_examples(examples: list[dict]) -> str:
    """Format estimation examples into a string suitable for injection into a system prompt."""
    parts: list[str] = []
    for i, example in enumerate(examples, start=1):
        parts.append(
            f"--- EXAMPLE {i} ---\n"
            f"Meeting Summary:\n{example['meeting_summary']}\n\n"
            f"Estimation:\n{example['estimation']}\n"
        )
    return "\n".join(parts) + "\n--- END OF ESTIMATION EXAMPLES ---"

def build_system_prompt() -> str:
    examples_text = format_examples(ESTIMATION_EXAMPLES)
    return f"""You are a senior software consultant with 15 years of experience in project estimation.
Your job is to analyze meeting transcripts with clients and generate detailed software
development estimates.

Below are estimations from previous projects in the company.
Use them as reference to calibrate your estimates: the hourly rates,
the granularity of the task breakdown and the budget structure must
be consistent with these examples.

{examples_text}

Your estimation must include:
1. Project summary (2-3 sentences)
2. Task breakdown with estimated hours and cost
3. Recommended team
4. Total estimated duration
5. Key risks or assumptions

Use EUR as currency. Round hours to multiples of 5.

Generate a detailed estimation for the described project."""
    

async def generate_estimation(transcription: str) -> dict:
    system_prompt = build_system_prompt()
    
    response = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcription}
        ]
    )
    
    return {
        "estimation": response.choices[0].message.content,
        "model": settings.LLM_MODEL,
        "provider": settings.LLM_PROVIDER,
        "usage": {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }