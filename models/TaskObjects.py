from pydantic import BaseModel
from typing import List

class Tool(BaseModel):
    name: str
    is_hypothetical_tool: bool


class AddTaskInput(BaseModel):
    task_id: str
    name: str
    description: str
    tools_required: List[Tool]
    task_dependencies: List[str]

class AddDetailedTaskInput(BaseModel):
    task_id: str
    name: str
    description: str
    tools_required: List[Tool]
    task_dependencies: List[str]
    output_files: List[str]
    required_input_files: List[str]