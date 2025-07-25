import ast
import os
import importlib.util
from typing import Dict, List, Any, Optional
from pathlib import Path


class MCPToolAnalyzer:
    """
    A class to dynamically analyze MCP server files and extract function information.
    """
    
    def __init__(self, mcp_servers_dir: str = "src/mcp_servers"):
        self.mcp_servers_dir = Path(mcp_servers_dir)
        self.tools_info = {}
        
    def get_python_files(self) -> List[Path]:
        """Get all Python files in the mcp_servers directory."""
        python_files = []
        for file_path in self.mcp_servers_dir.glob("*.py"):
            if file_path.name != "__init__.py" and file_path.name != "tool_analyzer.py":
                python_files.append(file_path)
        return python_files
    
    def parse_function_signature(self, func_def: ast.FunctionDef) -> Dict[str, Any]:
        """Parse a function definition and extract its signature information."""
        func_info = {
            "name": func_def.name,
            "args": [],
            "return_type": None
        }
        
        # Parse arguments
        args = func_def.args.args
        defaults = func_def.args.defaults
        num_defaults = len(defaults)
        num_args = len(args)
        
        for i, arg in enumerate(args):
            arg_info = {
                "name": arg.arg,
                "type": None,
                "default": None
            }
            
            # Get type annotation
            if arg.annotation:
                arg_info["type"] = self._get_type_string(arg.annotation)
            
            # Get default value - defaults are for the last N arguments
            default_index = i - (num_args - num_defaults)
            if default_index >= 0:
                arg_info["default"] = self._get_value_string(defaults[default_index])
            
            func_info["args"].append(arg_info)
        
        # Parse return type
        if func_def.returns:
            func_info["return_type"] = self._get_type_string(func_def.returns)
        
        return func_info
    
    def _get_type_string(self, node: ast.AST) -> str:
        """Convert AST type annotation to string representation."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Constant):
            return str(node.value)
        elif isinstance(node, ast.Attribute):
            return f"{self._get_type_string(node.value)}.{node.attr}"
        elif isinstance(node, ast.Subscript):
            base = self._get_type_string(node.value)
            slice_str = self._get_type_string(node.slice)
            return f"{base}[{slice_str}]"
        elif isinstance(node, ast.Tuple):
            elements = [self._get_type_string(el) for el in node.elts]
            return f"({', '.join(elements)})"
        elif isinstance(node, ast.List):
            elements = [self._get_type_string(el) for el in node.elts]
            return f"[{', '.join(elements)}]"
        else:
            return str(node)
    
    def _get_value_string(self, node: ast.AST) -> str:
        """Convert AST default value to string representation."""
        if isinstance(node, ast.Constant):
            return repr(node.value)
        elif isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.List):
            elements = [self._get_value_string(el) for el in node.elts]
            return f"[{', '.join(elements)}]"
        elif isinstance(node, ast.Dict):
            items = []
            for key, value in zip(node.keys, node.values):
                key_str = self._get_value_string(key)
                value_str = self._get_value_string(value)
                items.append(f"{key_str}: {value_str}")
            return f"{{{', '.join(items)}}}"
        else:
            return str(node)
    
    def analyze_file(self, file_path: Path) -> Dict[str, Any]:
        """Analyze a single Python file and extract function information."""
        module_name = f"src.mcp_servers.{file_path.stem}"
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            tree = ast.parse(content)
            functions = []
            
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # Check if function has @mcp.tool() decorator
                    has_mcp_decorator = False
                    for decorator in node.decorator_list:
                        if (isinstance(decorator, ast.Call) and 
                            isinstance(decorator.func, ast.Attribute) and
                            decorator.func.attr == 'tool'):
                            has_mcp_decorator = True
                            break
                        elif (isinstance(decorator, ast.Attribute) and
                              decorator.attr == 'tool'):
                            has_mcp_decorator = True
                            break
                    
                    if has_mcp_decorator:
                        func_info = self.parse_function_signature(node)
                        func_info["module"] = module_name
                        functions.append(func_info)
            
            return {
                "module": module_name,
                "file_path": str(file_path),
                "functions": functions
            }
            
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}")
            return {
                "module": module_name,
                "file_path": str(file_path),
                "functions": [],
                "error": str(e)
            }
    
    def analyze_all_files(self) -> Dict[str, Any]:
        """Analyze all Python files in the mcp_servers directory."""
        python_files = self.get_python_files()
        all_tools = {}
        
        for file_path in python_files:
            file_info = self.analyze_file(file_path)
            if file_info["functions"]:
                all_tools[file_info["module"]] = file_info
        
        return all_tools
    
    def generate_tools_dictionary(self) -> Dict[str, Any]:
        """Generate a comprehensive dictionary of all MCP tools."""
        tools_info = self.analyze_all_files()
        
        # Flatten the structure for easier access
        flattened_tools = {}
        
        for module_name, module_info in tools_info.items():
            for func_info in module_info["functions"]:
                tool_key = f"{module_name}.{func_info['name']}"
                
                flattened_tools[tool_key] = {
                    "function_name": func_info["name"],
                    "module_name": module_name,
                    "args": func_info["args"],
                    "return_type": func_info["return_type"],
                    "file_path": module_info["file_path"]
                }
        
        return flattened_tools
    
    def print_tools_summary(self):
        """Print a summary of all discovered tools."""
        tools_dict = self.generate_tools_dictionary()
        
        print(f"Found {len(tools_dict)} MCP tools across all files:\n")
        
        for tool_key, tool_info in tools_dict.items():
            print(f"Tool: {tool_key}")
            print(f"  Function: {tool_info['function_name']}")
            print(f"  Module: {tool_info['module_name']}")
            print(f"  Return Type: {tool_info['return_type']}")
            print("  Arguments:")
            for arg in tool_info['args']:
                default_str = f" = {arg['default']}" if arg['default'] is not None else ""
                type_str = f": {arg['type']}" if arg['type'] is not None else ""
                print(f"    {arg['name']}{type_str}{default_str}")
            print()


def main():
    """Main function to demonstrate the tool analyzer."""
    analyzer = MCPToolAnalyzer()
    
    # Generate the tools dictionary
    tools_dict = analyzer.generate_tools_dictionary()
    
    # Print summary
    analyzer.print_tools_summary()
    
    # Return the dictionary for further use
    return tools_dict


if __name__ == "__main__":
    tools_dict = main()
    print(f"\nTotal tools found: {len(tools_dict)}") 