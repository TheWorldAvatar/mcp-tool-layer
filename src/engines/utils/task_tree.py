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

    def __repr__(self):
        return f"TaskNode({self.task_id}, name={self.name})"

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


class TaskTree:
    def __init__(self, tasks_data: List[Dict]):
        self.tasks_data = tasks_data
        self.task_nodes: Dict[str, TaskNode] = {}
        self.roots: List[TaskNode] = []
        self.build_task_tree()

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

