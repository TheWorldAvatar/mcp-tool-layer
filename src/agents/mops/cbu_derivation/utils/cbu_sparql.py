from rdflib import Graph, Literal


def extract_ccdc_from_ttl(ttl_text: str) -> str:
    """Return the value of ontomops:hasCCDCNumber via SPARQL from a TTL string.

    If multiple values exist, returns the first. Returns empty string when none.
    """
    try:
        g = Graph()
        g.parse(data=ttl_text, format="turtle")
        q = (
            """
            PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
            SELECT ?ccdc WHERE {
              ?s ontomops:hasCCDCNumber ?ccdc .
            } LIMIT 1
            """
        )
        for row in g.query(q):
            val = row[0]
            if isinstance(val, Literal):
                return str(val)
            return str(val)
    except Exception:
        pass
    return "N/A"


if __name__ == "__main__":
    import os

    data_dir = "data"
    for hash_dir in os.listdir(data_dir):
        hash_path = os.path.join(data_dir, hash_dir)
        if os.path.isdir(hash_path):
            ontomops_output = os.path.join(hash_path, "ontomops_output")
            if os.path.isdir(ontomops_output):
                for fname in os.listdir(ontomops_output):
                    if fname.endswith(".ttl"):
                        ttl_path = os.path.join(ontomops_output, fname)
                        try:
                            with open(ttl_path, "r", encoding="utf-8") as f:
                                ttl_txt = f.read()
                            has_ccdc_text = "CCDC" in ttl_txt
                            ccdc_val = extract_ccdc_from_ttl(ttl_txt)
                            print(
                                f"Hash: {hash_dir} | Entity: {fname}\n"
                                f"  Contains 'CCDC' in text? {has_ccdc_text}\n"
                                f"  Extracted CCDC: {ccdc_val}\n"
                                f"{'-'*40}"
                            )
                        except Exception as e:
                            print(
                                f"Hash: {hash_dir} | Entity: {fname}\n"
                                f"  ERROR reading file or extracting CCDC: {e}\n"
                                f"{'-'*40}"
                            )