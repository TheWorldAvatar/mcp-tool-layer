def full_file_access(file_path: str) -> str:
    # json file 
    # ttl file 

    if file_path.endswith(".json"):
        with open(file_path, "r") as f:
            return f.read()
    elif file_path.endswith(".ttl"):
        with open(file_path, "r") as f:
            return f.read()
    elif file_path.endswith(".obda"):
        with open(file_path, "r") as f:
            return f.read()
    else:
        return "File type not supported. During execution, only json and ttl files are supported."


if __name__ == "__main__":
    print(full_file_access("sandbox/data/gaussian/1/vibdisps.csv"))