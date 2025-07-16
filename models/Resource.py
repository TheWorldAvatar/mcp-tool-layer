
class Resource:
    def __init__(self, type: str, relative_path: str, absolute_path: str, uri: str, meta_task_name: str = "", iteration: int = -1):
        self.type = type
        self.relative_path = relative_path
        self.absolute_path = absolute_path
        self.uri = uri
        self.meta_task_name = meta_task_name
        self.iteration = iteration

    def __str__(self):
        return f"Resource(type={self.type}\nrelative_path={self.relative_path}\nabsolute_path={self.absolute_path}\nuri={self.uri}\nmeta_task_name={self.meta_task_name}\niteration={self.iteration})"


    def __docker_path__(self):
        # return the relative path of the resource, and the meta_task_name, iteration, and type
        return f"Resource(type={self.type}\nrelative_path={self.relative_path}\nmeta_task_name={self.meta_task_name}\niteration={self.iteration})"


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
    resource = Resource(type="file", relative_path="data/jiying/0/extract_data.py", absolute_path="data/jiying/0/extract_data.py", uri="file:///data/jiying/0/extract_data.py", meta_task_name="jiying", iteration=0)
    print(docker_path(resource))