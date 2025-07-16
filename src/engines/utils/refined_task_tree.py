from collections import defaultdict
from typing import List, Dict, Optional, Set, Any


class RefinedTaskNode:
    """
    A node representing a refined task with detailed information including
    output files, required input files, and complex tool requirements.
    """
    
    def __init__(self, task_data: Dict[str, Any]):
        self.task_id = task_data['task_id']
        self.name = task_data.get('name', '')
        self.description = task_data.get('description', '')
        self.tools_required = task_data.get('tools_required', [])
        self.dependencies = task_data.get('task_dependencies', [])
        self.output_files = task_data.get('output_files', [])
        self.required_input_files = task_data.get('required_input_files', [])
        self.file_name = task_data.get('file_name', '')
        self.children: List['RefinedTaskNode'] = []
        self.parent: Set['RefinedTaskNode'] = set()

    def __repr__(self):
        return (
            f"RefinedTaskNode("
            f"task_id={self.task_id!r}, "
            f"name={self.name!r}, "
            f"description={self.description!r}, "
            f"tools_required={self.tools_required!r}, "
            f"dependencies={self.dependencies!r}, "
            f"output_files={self.output_files!r}, "
            f"required_input_files={self.required_input_files!r}, "
            f"file_name={self.file_name!r}"
            f")"
        )

    def get_all_parent_nodes(self) -> List['RefinedTaskNode']:
        """Get all parent nodes in the dependency chain."""
        parent_nodes = []
        current_node = self
        while current_node:
            parent_nodes.append(current_node)
            if current_node.parent:
                current_node = current_node.parent.pop()    
            else:
                break
        return parent_nodes

    def get_hypothetical_tools(self) -> List[Dict[str, Any]]:
        """Get all hypothetical tools required by this task."""
        return [tool for tool in self.tools_required if tool.get('is_hypothetical_tool', False)]

    def get_llm_generation_tools(self) -> List[Dict[str, Any]]:
        """Get all LLM generation tools required by this task."""
        return [tool for tool in self.tools_required if tool.get('is_llm_generation', False)]

    def get_real_tools(self) -> List[Dict[str, Any]]:
        """Get all real (non-hypothetical) tools required by this task."""
        return [tool for tool in self.tools_required if not tool.get('is_hypothetical_tool', False)]

    def has_hypothetical_tools(self) -> bool:
        """Check if this task has any hypothetical tools."""
        return any(tool.get('is_hypothetical_tool', False) for tool in self.tools_required)

    def has_llm_generation_tools(self) -> bool:
        """Check if this task has any LLM generation tools."""
        return any(tool.get('is_llm_generation', False) for tool in self.tools_required)

    def to_dict(self) -> Dict[str, Any]:
        """
        Returns a JSON-serializable dictionary representation of the RefinedTaskNode.
        Note: parent and children are represented by their task_ids to avoid recursion.
        """
        return {
            'task_id': self.task_id,
            'name': self.name,
            'description': self.description,
            'tools_required': self.tools_required,
            'task_dependencies': self.dependencies,
            'output_files': self.output_files,
            'required_input_files': self.required_input_files,
            'file_name': self.file_name,
            'children': [child.task_id for child in self.children],
            'parent': [parent.task_id for parent in self.parent]
        }


class RefinedTaskTree:
    """
    A tree structure for managing refined tasks with detailed information
    including file dependencies and complex tool requirements.
    """
    
    def __init__(self, tasks_data: List[Dict[str, Any]]):
        self.tasks_data = tasks_data
        self.task_nodes: Dict[str, RefinedTaskNode] = {}
        self.roots: List[RefinedTaskNode] = []
        self.build_task_tree()

    def get_dependency_ordered_task_nodes(self) -> List[RefinedTaskNode]:
        """
        Returns a list of task nodes ordered such that any node always appears before
        any node that depends on it (topological order). Roots come first.
        """
        # Kahn's algorithm for topological sort
        in_degree = {task_id: 0 for task_id in self.task_nodes}
        for node in self.task_nodes.values():
            for dep_id in node.dependencies:
                if dep_id in in_degree:
                    in_degree[dep_id] += 1

        # Start with nodes that have no dependencies (roots)
        queue = [self.task_nodes[tid] for tid, deg in in_degree.items() if deg == 0]
        ordered = []
        visited = set()

        while queue:
            node = queue.pop(0)
            if node.task_id in visited:
                continue
            ordered.append(node)
            visited.add(node.task_id)
            for child in node.children:
                # Decrement in-degree for each child
                in_degree[child.task_id] -= 1
                if in_degree[child.task_id] == 0:
                    queue.append(child)
        # If there are cycles, add remaining nodes at the end (not strictly topological)
        for node in self.task_nodes.values():
            if node not in ordered:
                ordered.append(node)
        return ordered

    def get_all_task_nodes(self) -> List[RefinedTaskNode]:
        """Get all task nodes ordered from root to leaf."""
        all_nodes = list(self.task_nodes.values())
        all_nodes.sort(key=lambda x: len(x.get_all_parent_nodes()))
        return all_nodes

    def get_tasks_with_hypothetical_tools(self) -> List[RefinedTaskNode]:
        """Get all tasks that have hypothetical tools."""
        return [node for node in self.task_nodes.values() if node.has_hypothetical_tools()]

    def get_tasks_with_llm_generation_tools(self) -> List[RefinedTaskNode]:
        """Get all tasks that have LLM generation tools."""
        return [node for node in self.task_nodes.values() if node.has_llm_generation_tools()]

    def get_tasks_by_output_file(self, output_file: str) -> List[RefinedTaskNode]:
        """Get all tasks that produce a specific output file."""
        return [node for node in self.task_nodes.values() if output_file in node.output_files]

    def get_tasks_by_input_file(self, input_file: str) -> List[RefinedTaskNode]:
        """Get all tasks that require a specific input file."""
        return [node for node in self.task_nodes.values() if input_file in node.required_input_files]

    def get_file_dependencies(self) -> Dict[str, List[str]]:
        """
        Get a mapping of files to the tasks that produce them.
        Returns: {file_name: [task_ids]}
        """
        file_dependencies = defaultdict(list)
        for node in self.task_nodes.values():
            for output_file in node.output_files:
                file_dependencies[output_file].append(node.task_id)
        return dict(file_dependencies)

    def get_file_requirements(self) -> Dict[str, List[str]]:
        """
        Get a mapping of files to the tasks that require them.
        Returns: {file_name: [task_ids]}
        """
        file_requirements = defaultdict(list)
        for node in self.task_nodes.values():
            for input_file in node.required_input_files:
                file_requirements[input_file].append(node.task_id)
        return dict(file_requirements)

    def build_task_tree(self):
        """Build the task tree structure with parent-child relationships."""
        # Step 1: Create all task nodes
        for task in self.tasks_data:
            node = RefinedTaskNode(task)
            self.task_nodes[node.task_id] = node

        # Step 2: Build parent-child relationships
        for node in self.task_nodes.values():
            for dep_id in node.dependencies:
                parent_node = self.task_nodes.get(dep_id)
                if parent_node:
                    parent_node.children.append(node)
                    node.parent.add(parent_node)    

        # Step 3: Identify root nodes (nodes with no dependencies)
        self.roots = [node for node in self.task_nodes.values() if not node.dependencies]

    def visualize_task_tree(self):
        """Visualize the task tree structure."""
        def dfs(node: RefinedTaskNode, depth: int = 0, visited=None):
            if visited is None:
                visited = set()
            if node.task_id in visited:
                print("    " * depth + f"- {node.name} ({node.task_id}) [cycle detected]")
                return
            visited.add(node.task_id)

            # Show task info with additional details
            tool_info = f"[{len(node.tools_required)} tools]"
            if node.has_hypothetical_tools():
                tool_info += " [HYP]"
            if node.has_llm_generation_tools():
                tool_info += " [LLM]"
            
            print("    " * depth + f"- {node.name} ({node.task_id}) {tool_info}")
            if node.output_files:
                print("    " * (depth + 1) + f"Outputs: {', '.join(node.output_files)}")
            if node.required_input_files:
                print("    " * (depth + 1) + f"Inputs: {', '.join(node.required_input_files)}")
            
            for child in sorted(node.children, key=lambda n: n.task_id):
                dfs(child, depth + 1, visited.copy())

        if not self.roots:
            print("No root tasks found.")
        else:
            for root in sorted(self.roots, key=lambda n: n.task_id):
                dfs(root)

    def locate_task_node(self, task_id: str) -> Optional[RefinedTaskNode]:
        """Find a task node by its ID."""
        return self.task_nodes.get(task_id)

    def get_root_task_nodes(self) -> List[RefinedTaskNode]:
        """Get all root task nodes."""
        return self.roots

    def to_dict(self) -> Dict[str, Any]:
        """Convert the entire tree to a dictionary representation."""
        return {
            'task_nodes': {task_id: node.to_dict() for task_id, node in self.task_nodes.items()}
        }

    def get_execution_order(self) -> List[str]:
        """Get the recommended execution order of task IDs."""
        ordered_nodes = self.get_dependency_ordered_task_nodes()
        return [node.task_id for node in ordered_nodes]

    def validate_dependencies(self) -> List[str]:
        """
        Validate that all dependencies exist in the task tree.
        Returns a list of error messages.
        """
        errors = []
        for node in self.task_nodes.values():
            for dep_id in node.dependencies:
                if dep_id not in self.task_nodes:
                    errors.append(f"Task {node.task_id} depends on non-existent task {dep_id}")
        return errors

    def get_parallel_executable_tasks(self) -> List[List[RefinedTaskNode]]:
        """
        Get groups of tasks that can be executed in parallel.
        Returns a list of task groups, where each group can be executed in parallel.
        """
        ordered_nodes = self.get_dependency_ordered_task_nodes()
        parallel_groups = []
        current_group = []
        
        for node in ordered_nodes:
            # Check if all dependencies are satisfied
            dependencies_satisfied = all(
                any(dep_node in [n for group in parallel_groups for n in group] 
                    for dep_node in [self.task_nodes[dep_id] for dep_id in node.dependencies 
                                   if dep_id in self.task_nodes])
            )
            
            if dependencies_satisfied:
                current_group.append(node)
            else:
                if current_group:
                    parallel_groups.append(current_group)
                current_group = [node]
        
        if current_group:
            parallel_groups.append(current_group)
            
        return parallel_groups
