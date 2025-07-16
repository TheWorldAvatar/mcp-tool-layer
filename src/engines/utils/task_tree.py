from collections import defaultdict
from typing import List, Dict, Optional, Set


class TaskNode:
    
    def __init__(self, task_data: Dict):
        self.task_id = task_data['task_id']
        self.name = task_data.get('name', '')
        self.description = task_data.get('description', '')
        self.tools_required = task_data.get('tools_required', [])
        self.dependencies = task_data.get('task_dependencies', [])
        self.file_name = task_data.get('file_name', '')  # Add file_name field
        self.children: List['TaskNode'] = []
        self.parent: Set['TaskNode'] = set()

        # 

    def __repr__(self):
        return (
            f"TaskNode("
            f"task_id={self.task_id!r}, "
            f"name={self.name!r}, "
            f"description={self.description!r}, "
            f"tools_required={self.tools_required!r}, "
            f"dependencies={self.dependencies!r}, "
            f"file_name={self.file_name!r}"
            f")"
        )

    def get_all_parent_nodes(self):
        parent_nodes = []
        current_node = self
        while current_node:
            parent_nodes.append(current_node)
            if current_node.parent:
                current_node = current_node.parent.pop()    
            else:
                break
        return parent_nodes


    def to_dict(self):
        """
        Returns a JSON-serializable dictionary representation of the TaskNode.
        Note: parent and children are represented by their task_ids to avoid recursion.
        """
        return {
            'task_id': self.task_id,
            'name': self.name,
            'description': self.description,
            'tools_required': self.tools_required,
            'task_dependencies': self.dependencies,
            'file_name': self.file_name,
            'children': [child.task_id for child in self.children],
            'parent': [parent.task_id for parent in self.parent]
        }




class TaskTree:
    def __init__(self, tasks_data: List[Dict]):
        self.tasks_data = tasks_data
        self.task_nodes: Dict[str, TaskNode] = {}
        self.roots: List[TaskNode] = []
        self.build_task_tree()

    def get_dependency_ordered_task_nodes(self) -> List[TaskNode]:
        """
        Returns a list of task nodes ordered such that any node always appears before
        any node that depends on it (topological order). Roots come first.
        """
        # Kahn's algorithm for topological sort
        in_degree = {task_id: 0 for task_id in self.task_nodes}
        for node in self.task_nodes.values():
            for dep_id in node.dependencies:
                if dep_id in in_degree:
                    in_degree[node.task_id] += 1

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

    def get_all_task_nodes(self):
        # you should order the task nodes list by the order of from root to leaf
        all_nodes = list(self.task_nodes.values())
        all_nodes.sort(key=lambda x: len(x.get_all_parent_nodes()))
        return all_nodes

    def build_task_tree(self):
        # Step 1: Create all task nodes
        for task in self.tasks_data:
            node = TaskNode(task)
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
        def dfs(node: TaskNode, depth: int = 0, visited=None):
            if visited is None:
                visited = set()
            if node.task_id in visited:
                print("    " * depth + f"- {node.name} ({node.task_id}) [cycle detected]")
                return
            visited.add(node.task_id)

            print("    " * depth + f"- {node.name} ({node.task_id})")
            for child in sorted(node.children, key=lambda n: n.task_id):
                dfs(child, depth + 1, visited.copy())

        if not self.roots:
            print("No root tasks found.")
        else:
            for root in sorted(self.roots, key=lambda n: n.task_id):
                dfs(root)

    def locate_task_node(self, task_id: str):
        return self.task_nodes.get(task_id)

    def get_root_task_nodes(self):
        return self.roots

    def to_dict(self):
        return {
            'task_nodes': {task_id: node.to_dict() for task_id, node in self.task_nodes.items()}
        }

