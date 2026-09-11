"""PubChem MCP payload/retry guards. No live NCBI calls."""

from __future__ import annotations

import unittest
from typing import Any

from pydantic import TypeAdapter, ValidationError

from src.mcp_servers.pubchem.curated_names import (
    curated_name_records,
    curated_pubchem_record,
)
from src.mcp_servers.pubchem.name_dedup import (
    as_record_dict,
    as_record_list,
    finalize_pubchem_list,
    finalize_pubchem_record,
)
from src.mcp_servers.pubchem.retry import (
    backoff_seconds,
    call_pubchem,
    is_not_found_error,
    is_retryable_pubchem_error,
)


_LIST_SCHEMA = TypeAdapter(list[dict[str, Any]])
_DICT_SCHEMA = TypeAdapter(dict[str, Any])


class _HttpError(Exception):
    def __init__(self, code: int, msg: str = "") -> None:
        super().__init__(msg or str(code))
        self.code = code


class PubChemPayloadTests(unittest.TestCase):
    def test_curated_pentoxy_is_fastmcp_list(self) -> None:
        payload = curated_name_records("5-Pentoxy-1,3-benzenedicarboxylic acid")
        self.assertIsNotNone(payload)
        _LIST_SCHEMA.validate_python(payload)
        self.assertEqual(payload[0]["source"], "curated-not-in-pubchem")
        self.assertEqual(payload[0]["formula"], "C14H18O5")

    def test_bare_curated_dict_is_rejected_by_list_schema(self) -> None:
        record = curated_pubchem_record("5-OPent-bdc")
        self.assertIsInstance(record, dict)
        with self.assertRaises(ValidationError):
            _LIST_SCHEMA.validate_python(record)

    def test_as_record_list_wraps_dict(self) -> None:
        wrapped = as_record_list({"cid": 1, "names": ["ethanol"]})
        _LIST_SCHEMA.validate_python(wrapped)
        self.assertEqual(len(wrapped), 1)

    def test_cid_empty_lookup_is_dict_not_list(self) -> None:
        payload = finalize_pubchem_record(None, query="999999999", use_llm=False)
        _DICT_SCHEMA.validate_python(payload)
        self.assertFalse(payload.get("matched", True))
        with self.assertRaises(ValidationError):
            _DICT_SCHEMA.validate_python(
                finalize_pubchem_list(None, query="999999999", use_llm=False)
            )

    def test_as_record_dict_unwraps_miss_list(self) -> None:
        payload = as_record_dict(
            finalize_pubchem_list([], query="missing", use_llm=False)
        )
        _DICT_SCHEMA.validate_python(payload)
        self.assertIn("error", payload)


class PubChemRetryTests(unittest.TestCase):
    def test_404_is_miss_not_retry(self) -> None:
        named = type("NotFoundError", (_HttpError,), {})(404, "No CID found")
        self.assertTrue(is_not_found_error(named))
        self.assertFalse(is_retryable_pubchem_error(named))
        self.assertFalse(is_retryable_pubchem_error(_HttpError(404)))

    def test_gateway_and_busy_retry(self) -> None:
        self.assertTrue(is_retryable_pubchem_error(_HttpError(502, "Bad Gateway")))
        self.assertTrue(is_retryable_pubchem_error(_HttpError(503, "Server Busy")))
        self.assertTrue(is_retryable_pubchem_error(_HttpError(429, "Too Many Requests")))
        self.assertTrue(is_retryable_pubchem_error(TimeoutError("PubChem request exceeded 20s")))

    def test_client_errors_do_not_retry(self) -> None:
        self.assertFalse(is_retryable_pubchem_error(_HttpError(400, "Bad Request")))

    def test_backoff_grows(self) -> None:
        self.assertEqual(backoff_seconds(1), 1.0)
        self.assertEqual(backoff_seconds(2), 2.0)
        self.assertEqual(backoff_seconds(3), 4.0)
        self.assertEqual(backoff_seconds(4), 8.0)
        self.assertEqual(backoff_seconds(5), 8.0)

    def test_call_pubchem_does_not_retry_404(self) -> None:
        calls = {"n": 0}

        def boom() -> list:
            calls["n"] += 1
            raise type("NotFoundError", (_HttpError,), {})(404, "No CID found")

        slept: list[float] = []
        result = call_pubchem(
            "get_compounds(name='x')",
            boom,
            timeout_seconds=5.0,
            attempts=5,
            sleep=slept.append,
        )
        self.assertIsNone(result)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(slept, [])

    def test_call_pubchem_retries_503_then_succeeds(self) -> None:
        calls = {"n": 0}

        def flaky() -> list[dict[str, Any]]:
            calls["n"] += 1
            if calls["n"] < 3:
                raise _HttpError(503, "PUGREST.ServerBusy")
            return [{"cid": 702, "molecular_formula": "C2H6O"}]

        slept: list[float] = []
        result = call_pubchem(
            "get_compounds(name='ethanol')",
            flaky,
            timeout_seconds=5.0,
            attempts=5,
            sleep=slept.append,
        )
        self.assertEqual(calls["n"], 3)
        self.assertEqual(result[0]["cid"], 702)
        self.assertEqual(slept, [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
