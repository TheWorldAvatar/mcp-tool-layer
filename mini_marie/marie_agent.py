"""
MARIE Agent - MOPs Analysis and Research Intelligence Engine

An intelligent agent that answers questions about Metal-Organic Polyhedra (MOPs)
synthesis using the MOPs Knowledge Graph through MCP tools.
"""

import asyncio
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger


class MarieAgent:
    """
    MARIE (MOPs Analysis and Research Intelligence Engine) Agent
    
    An intelligent assistant for querying MOPs synthesis knowledge graph.
    Provides natural language interface to complex SPARQL queries.
    """
    
    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        remote_model: bool = True,
        model_config: Optional[ModelConfig] = None,
    ):
        """
        Initialize MARIE agent with MOPs KG MCP server.
        
        Args:
            model_name: Name of the LLM model to use
            remote_model: Whether to use remote (OpenAI) or local model
            model_config: Optional model configuration
        """
        self.logger = get_logger("agent", "MarieAgent")
        self.logger.info(f"Initializing MARIE agent with model: {model_name}")
        self.model_name = model_name
        self.remote_model = remote_model
        self.model_config = model_config
        
        # Get path to MCP config in configs folder
        self.config_path = Path(__file__).parent.parent / "configs" / "marie_kg.json"
        
        self.logger.info("MARIE agent initialized successfully")
    
    async def ask(
        self,
        question: str,
        previous_question: Optional[str] = None,
        previous_answer: Optional[str] = None,
        recursion_limit: int = 200,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Ask MARIE a question about MOPs synthesis.
        
        Args:
            question: Natural language question about MOPs
            recursion_limit: Maximum reasoning steps for the agent
            
        Returns:
            Tuple of (answer_text, metadata) where metadata contains token usage, etc.
            
        Example:
            >>> marie = MarieAgent()
            >>> answer, meta = await marie.ask("What is the recipe for VMOP-17?")
            >>> print(answer)
        """
        self.logger.info(f"Processing question: {question[:100]}...")
        
        # Enhance the question with context to guide the agent
        enhanced_instruction = self._enhance_question(
            question,
            previous_question=previous_question,
            previous_answer=previous_answer,
        )
        
        try:
            # Create a fresh BaseAgent per question (no reuse across requests)
            base_agent = BaseAgent(
                model_name=self.model_name,
                remote_model=self.remote_model,
                model_config=self.model_config,
                mcp_set_name=str(self.config_path),
                mcp_tools=["mops-kg"],
                structured_output=False,
            )

            result, metadata = await base_agent.run(
                task_instruction=enhanced_instruction,
                recursion_limit=recursion_limit
            )
            
            self.logger.info(
                f"Question answered. Tokens used: {metadata['aggregated_usage']['total_tokens']}"
            )
            
            return result, metadata
            
        except Exception as e:
            self.logger.error(f"Error processing question: {e}", exc_info=True)
            raise
    
    def _enhance_question(
        self,
        question: str,
        previous_question: Optional[str] = None,
        previous_answer: Optional[str] = None,
    ) -> str:
        """
        Enhance user question with helpful context and guidance.
        
        Args:
            question: User's original question
            
        Returns:
            Enhanced question with context
        """
        memory_block = ""
        if previous_question and previous_answer:
            memory_block = f"""
**Previous Turn (for continuity):**
- Previous user question: {previous_question}
- Your previous answer: {previous_answer}
"""

        context = f"""
You are MARIE (MOPs Analysis and Research Intelligence Engine), an expert assistant for Metal-Organic Polyhedra (MOPs) synthesis.

You have access to a comprehensive knowledge graph containing 30 research papers with detailed synthesis procedures, chemical building units, reaction conditions, and characterization data.

{memory_block}

**Your Task:**
Answer the following question using the MOPs knowledge graph tools:

{question}

**Guidelines:**
1. Start by using lookup tools to verify entity names exist (e.g., lookup_synthesis_by_name, lookup_mop_by_name)
2. If looking for synthesis information, follow the pattern: Synthesis → Recipe/Steps → Conditions → Products
3. When presenting chemical recipes, include amounts and formulas
4. For synthesis procedures, list steps in order with conditions (temperature, duration)
5. Always cite CCDC numbers when available
6. If you encounter "No results found", try variations of the name or use related queries
7. Format your answer clearly with sections and bullet points for readability

**Common Entity Naming Patterns:**
- Syntheses: VMOP-XX, IRMOP-XX, MOP-XX, UMC-X, etc.
- MOPs: Same as syntheses, plus "Cage XXX", "Nanocapsule XXX"
- CCDC numbers: 6-7 digit numbers like "869988", "1576897"

Please provide a comprehensive, well-structured answer.
"""
        return context
    
    async def get_synthesis_info(
        self,
        synthesis_name: str,
        include_recipe: bool = True,
        include_steps: bool = True,
        include_conditions: bool = True,
        include_products: bool = True,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Get comprehensive information about a specific synthesis.
        
        Args:
            synthesis_name: Name of the synthesis (e.g., "VMOP-17")
            include_recipe: Include chemical recipe
            include_steps: Include synthesis steps
            include_conditions: Include temperature and duration conditions
            include_products: Include MOP products
            
        Returns:
            Tuple of (formatted_info, metadata)
        """
        sections = []
        if include_recipe:
            sections.append("the complete chemical recipe with amounts")
        if include_steps:
            sections.append("all synthesis steps in order")
        if include_conditions:
            sections.append("temperature and duration conditions")
        if include_products:
            sections.append("the final MOP products with CCDC numbers")
        
        question = f"""
        Provide comprehensive information about the synthesis of {synthesis_name}.
        Include: {', '.join(sections)}.
        
        Format the answer with clear sections and present data in tables where appropriate.
        """
        
        return await self.ask(question)
    
    async def compare_syntheses(
        self,
        synthesis_names: list,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Compare multiple synthesis procedures.
        
        Args:
            synthesis_names: List of synthesis names to compare
            
        Returns:
            Tuple of (comparison_report, metadata)
        """
        names_str = ", ".join(synthesis_names)
        
        question = f"""
        Compare the following synthesis procedures: {names_str}
        
        For each synthesis, provide:
        1. Number of chemical inputs required
        2. Number of synthesis steps
        3. Temperature range used
        4. Total reaction time
        5. Final MOP product(s) and CCDC numbers
        
        Then provide a comparative analysis highlighting:
        - Similarities in procedure
        - Key differences
        - Complexity comparison
        
        Present the comparison in a clear, structured format.
        """
        
        return await self.ask(question)
    
    async def find_mop_info(
        self,
        mop_name: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Get information about a specific MOP.
        
        Args:
            mop_name: Name of the MOP (e.g., "CIAC-105")
            
        Returns:
            Tuple of (mop_info, metadata)
        """
        question = f"""
        Provide detailed information about the MOP: {mop_name}
        
        Include:
        1. CCDC number
        2. Chemical formula
        3. Chemical building units (CBUs) with formulas
        4. Which synthesis procedure(s) produce this MOP
        
        Format the answer clearly with sections.
        """
        
        return await self.ask(question)
    
    async def search_by_chemical(
        self,
        chemical_name: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Find syntheses that use a specific chemical.
        
        Args:
            chemical_name: Name of the chemical to search for
            
        Returns:
            Tuple of (search_results, metadata)
        """
        question = f"""
        Find all synthesis procedures that use the chemical: {chemical_name}
        
        For each synthesis found:
        1. List the synthesis name
        2. Show how much of this chemical is used (amount)
        3. Indicate what MOP it produces
        
        If the exact chemical name doesn't match, suggest related chemicals.
        """
        
        return await self.ask(question)
    
    async def get_corpus_statistics(self) -> Tuple[str, Dict[str, Any]]:
        """
        Get overall statistics about the knowledge graph.
        
        Returns:
            Tuple of (statistics_report, metadata)
        """
        question = """
        Provide comprehensive statistics about the MOPs knowledge graph.
        
        Include:
        1. Total number of MOPs
        2. Total number of syntheses
        3. Total number of synthesis steps
        4. Total number of chemical building units
        5. Most commonly used chemicals (top 10)
        
        Present the statistics in a clear, organized format.
        """
        
        return await self.ask(question)


# ============================================================================
# Example Usage and Demo
# ============================================================================

async def demo_marie():
    """Demonstrate MARIE's capabilities."""
    print("="*80)
    print("MARIE - MOPs Analysis and Research Intelligence Engine")
    print("="*80)
    
    marie = MarieAgent(model_name="gpt-4o-mini")
    
    # Example 1: Ask about a synthesis
    print("\n📋 Example 1: Getting synthesis information")
    print("-" * 80)
    answer, meta = await marie.get_synthesis_info(
        "VMOP-17",
        include_recipe=True,
        include_steps=True,
        include_conditions=True
    )
    print(answer)
    print(f"\nTokens used: {meta['aggregated_usage']['total_tokens']}")
    
    # Example 2: Ask about a MOP
    print("\n\n🔬 Example 2: Getting MOP information")
    print("-" * 80)
    answer, meta = await marie.find_mop_info("CIAC-105")
    print(answer)
    print(f"\nTokens used: {meta['aggregated_usage']['total_tokens']}")
    
    # Example 3: Compare syntheses
    print("\n\n⚖️ Example 3: Comparing syntheses")
    print("-" * 80)
    answer, meta = await marie.compare_syntheses(["UMC-1", "UMC-2"])
    print(answer)
    print(f"\nTokens used: {meta['aggregated_usage']['total_tokens']}")
    
    # Example 4: Free-form question
    print("\n\n❓ Example 4: Free-form question")
    print("-" * 80)
    answer, meta = await marie.ask(
        "What are the most common solvents used in MOPs synthesis?"
    )
    print(answer)
    print(f"\nTokens used: {meta['aggregated_usage']['total_tokens']}")
    
    print("\n" + "="*80)
    print("Demo completed!")
    print("="*80)


if __name__ == "__main__":
    # Run the demo
    asyncio.run(demo_marie())

