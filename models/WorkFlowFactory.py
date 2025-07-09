# file: airflow_playground/factory/workflow_factory.py

from pathlib import Path
import json
import pendulum
from airflow import DAG
from airflow.decorators import task as airflow_task
from airflow.models.baseoperator import chain

from airflow_playground.plugins.MCPCallOperator import MCPCallOperator


# Optional: for simple python_fn tasks
def read_config(file: str):
    import json, pathlib
    return json.loads(pathlib.Path(file).read_text())

PY_FN_REGISTRY = {
    "read_config": read_config,
}


class WorkFlowFactory:
    """
    Convert a DAG specification (dict) into an Airflow DAG.
    """

    def __init__(self, dag_spec: dict):
        self.spec = dag_spec
        self.task_map: dict[str, object] = {}

    def create_dag(self) -> DAG:
        dag = DAG(
            dag_id=self.spec["dag_id"],
            start_date=pendulum.parse(self.spec["start_date"]),
            schedule=self.spec.get("schedule"),
            catchup=False,
            tags=["dynamic"],
        )

        with dag:
            for t in self.spec["tasks"]:
                task_obj = self._build_task(t)
                self.task_map[t["task_id"]] = task_obj

            for t in self.spec["tasks"]:
                for upstream in t.get("depends_on", []):
                    chain(self.task_map[upstream], self.task_map[t["task_id"]])

        return dag

    def _build_task(self, t: dict):
        op = t["operator"]

        if op == "python_fn":
            fn = PY_FN_REGISTRY[t["python_callable"]]
            params = t["params"]

            @airflow_task(task_id=t["task_id"])
            def _wrapper():
                return fn(**params)

            return _wrapper()

        elif op == "MCPCallOperator":
            return MCPCallOperator(
                task_id=t["task_id"],
                module_path=t["module_path"],
                tool_name=t["tool_name"],
                tool_args=t["tool_args"],
                run_once=t.get("run_once", False),
            )

        else:
            raise ValueError(f"Unknown operator: {op}")

    def write_to_dag_python_file(self, dag: DAG, file_path: str):
        """
        Write a static Python script that defines the DAG and tasks as specified.
        This is useful for inspecting or registering the DAG statically with Airflow.
        """
        lines = [
            "from airflow import DAG",
            "from airflow.decorators import task",
            "from airflow.models.baseoperator import chain",
            "from datetime import datetime",
            "from airflow_playground.plugins.MCPCallOperator import MCPCallOperator",
            "",
            f"with DAG(dag_id='{dag.dag_id}', start_date=datetime({dag.start_date.year}, {dag.start_date.month}, {dag.start_date.day}), "
            f"schedule_interval={repr(dag.schedule)}, catchup=False, tags={dag.tags}) as dag:",
            ""
        ]

        task_names = {}

        for t in self.spec["tasks"]:
            tid = t["task_id"]
            op = t["operator"]

            if op == "python_fn":
                # Only support simple literals for params
                fn = t["python_callable"]
                args_str = ", ".join(f"{k}={repr(v)}" for k, v in t["params"].items())
                task_code = (
                    f"    @task(task_id='{tid}')\n"
                    f"    def {tid}():\n"
                    f"        from airflow_playground.factory.workflow_factory import PY_FN_REGISTRY\n"
                    f"        return PY_FN_REGISTRY['{fn}']({args_str})\n"
                    f"\n"
                    f"    {tid}_t = {tid}()\n"
                )
                task_names[tid] = f"{tid}_t"

            elif op == "MCPCallOperator":
                tool_args_str = json.dumps(t["tool_args"])
                task_code = (
                    f"    {tid} = MCPCallOperator(\n"
                    f"        task_id='{tid}',\n"
                    f"        module_path={repr(t['module_path'])},\n"
                    f"        tool_name={repr(t['tool_name'])},\n"
                    f"        tool_args={tool_args_str},\n"
                    f"        run_once={t.get('run_once', False)}\n"
                    f"    )\n"
                )
                task_names[tid] = tid

            else:
                raise ValueError(f"Unsupported operator: {op}")

            lines.append(task_code)

        # Handle dependencies
        for t in self.spec["tasks"]:
            current = task_names[t["task_id"]]
            for upstream in t.get("depends_on", []):
                up = task_names[upstream]
                lines.append(f"    chain({up}, {current})")

        # Write to file
        with open(file_path, "w") as f:
            f.write("\n".join(lines))



if __name__ == "__main__":
    dag_spec_file_path = "airflow_playground/specs/test.json"
    dag_spec = json.load(open(dag_spec_file_path))
    workflow_factory = WorkFlowFactory(dag_spec)
    dag = workflow_factory.create_dag()
    workflow_factory.write_to_dag_python_file(dag, "airflow_playground/dags/test.py")



