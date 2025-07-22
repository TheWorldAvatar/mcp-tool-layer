from pydantic import BaseModel
from typing import List

class Tool(BaseModel):
    name: str
    is_hypothetical_tool: bool
    is_llm_generation: bool


class AddTaskInput(BaseModel):
    task_id: str
    name: str
    description: str
    tools_required: List[Tool]
    dependencies: List[str]

 