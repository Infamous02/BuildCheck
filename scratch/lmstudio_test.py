import lmstudio as lms
from pydantic import BaseModel

class MemorySpec(BaseModel):
    memory_type: str
    dimm_slots: int
    max_capacity_gb: int
    evidence_quote: str

page = "Memory: 4 x DDR5 DIMM sockets supporting up to 256 GB of system memory..."

model = lms.llm()  # uses whichever model is loaded in LM Studio
result = model.respond(
    "Extract the memory spec from this manual text. "
    f"evidence_quote must be copied exactly from the text.\n\n{page}",
    response_format=MemorySpec,
    config={"temperature": 0},
)
print(MemorySpec.model_validate(result.parsed))