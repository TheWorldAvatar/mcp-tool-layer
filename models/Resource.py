import json

class Resource:
    def __init__(
        self,
        type: str,
        relative_path: str,
        absolute_path: str,
        uri: str,
        meta_task_name: str = "",
        iteration: int = -1,
        description: str = ""
    ):
        self.type = type
        self.relative_path = relative_path
        self.absolute_path = absolute_path
        self.uri = uri
        self.meta_task_name = meta_task_name
        self.iteration = iteration
        self.description = description

    def __str__(self):

        return json.dumps({
            "type": self.type,
            "relative_path": self.relative_path,
            "absolute_path": self.absolute_path,
            "uri": self.uri,
            "meta_task_name": self.meta_task_name,
            "iteration": self.iteration,
            "description": self.description
        }, indent=4)

    def __docker_path__(self):
        # return the relative path of the resource, and the meta_task_name, iteration, type, and description
        return (
            f"Resource(type={self.type}\n"
            f"relative_path={self.relative_path}\n"
            f"meta_task_name={self.meta_task_name}\n"
            f"iteration={self.iteration}\n"
            f"description={self.description})"
        )


def docker_path(obj) -> str:
    """
    Helper that calls obj.__docker_path__() if it exists,
    otherwise raises TypeError.
    """
    try:
        meth = getattr(obj, "__docker_path__")
    except AttributeError:             # protocol not implemented
        raise TypeError(
            f"{obj!r} does not implement __docker_path__()"
        ) from None
    return meth()


if __name__ == "__main__":
    resource = Resource(
        type="file",
        relative_path="data/jiying/0/extract_data.py",
        absolute_path="data/jiying/0/extract_data.py",
        uri="file:///data/jiying/0/extract_data.py",
        meta_task_name="jiying",
        iteration=0,
        description="Extracted data script"
    )


    resource = Resource(
        type="docker_container",
        relative_path="docker_container_1",
        absolute_path="docker_container_1",
        uri="docker_container_1",
        meta_task_name="jiying",
        iteration=0,
        description="Docker container 1"
    )
