from fastmcp import FastMCP
from src.utils.global_logger import get_logger, mcp_tool_logger
from models.locations import SANDBOX_TASK_DIR
import os

from src.mcp_servers.mops_misc.operations.in_context_search import in_context_search

log = get_logger(__name__)
mcp = FastMCP(name="mops_misc")


@mcp.prompt(name="instruction", description="Instructions for miscellaneous search utilities")
def instruction_prompt():
    return (
        """
        This server provides utility tools to assist extraction agents.

        Tools:
        - in_context_search_file(task_name, filename, query, before=3, after=3, case_sensitive=False, max_results=None)
          Search occurrences of a query in a text file and return sentence windows around each hit.

        Typical use:
        1) Pass task_name = DOI subfolder, e.g. 10.1021_acs.inorgchem.4c02394
        2) filename like "10.1021_acs.inorgchem.4c02394_complete.md"
        3) Provide a query such as a reagent name or formula (e.g. "H2NDBDC")
        """
    )

 

@mcp.tool(name="in_context_search", description="Search a task by DOI and return contextual snippets around a query.", tags=["misc", "search"])
@mcp_tool_logger
def in_context_search_doi(
    doi: str,
    query: str,
    before: int = 3,
    after: int = 3,
    case_sensitive: bool = False,
    max_results: int | None = None,
) -> str:
    try:
        task_dir = os.path.join(SANDBOX_TASK_DIR, doi)
        if not os.path.isdir(task_dir):
            return f"Task folder not found: {task_dir}"

        # Prefer <doi>_complete.md, then <doi>.md, then any .md in the folder
        candidate_names = [
            f"{doi}_complete.md",
            f"{doi}.md",
        ]
        md_path = None
        for name in candidate_names:
            p = os.path.join(task_dir, name)
            if os.path.exists(p):
                md_path = p
                break
        if md_path is None:
            # fallback: first .md file in directory
            for fname in os.listdir(task_dir):
                if fname.lower().endswith(".md"):
                    md_path = os.path.join(task_dir, fname)
                    break
        if md_path is None:
            return f"No markdown file found in {task_dir}"

        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()

        hits = in_context_search(
            text=text,
            query=query,
            sentences_before=before,
            sentences_after=after,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )
        if not hits:
            return f"No matches found in {os.path.basename(md_path)}."
        lines = [f"Matches for '{query}' in {os.path.basename(md_path)} (task {doi}):", ""]
        for h in hits:
            lines.append(f"- Match #{h['match_index']} sentences {h['window_range']}:\n  {h['snippet']}")
        return "\n".join(lines)
    except Exception as e:
        log.exception("in_context_search failed")
        return f"Error: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")


