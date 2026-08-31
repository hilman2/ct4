"""The comparison corpus and its test bench.

A corpus case consists of a template, a context and the output that ct3
delivers for it. The test bench renders every case with a chosen
implementation and compares byte for byte. That is the acceptance
criterion from PLAN.md, section 8: compatibility is measured, not
claimed.
"""

from ct4.corpus.case import Case, read_jsonl, write_jsonl

__all__ = ["Case", "read_jsonl", "write_jsonl"]
