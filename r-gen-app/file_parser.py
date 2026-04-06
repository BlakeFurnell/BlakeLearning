"""
file_parser.py

Reads an uploaded CSV or Excel file and returns a structured summary dict
that can be serialized to JSON and injected verbatim into an LLM system prompt.

All values are guaranteed to be JSON-serializable:
  - NaN / NaT  → None
  - numpy scalar types → native Python int / float
"""

import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_python(value: Any) -> Any:
    """
    Recursively convert a value to a JSON-safe native Python type.

    pandas describe() and value_counts() return numpy scalars (np.int64,
    np.float64, etc.) that json.dumps() cannot serialize by default.
    float('nan') and float('inf') are also not valid JSON.
    """
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v = float(value)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, dict):
        return {str(k): _to_python(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_python(i) for i in value]
    return value


def _load_dataframe(file_path: str) -> pd.DataFrame:
    """
    Load the file at *file_path* into a DataFrame.

    Supported extensions: .csv, .xlsx, .xls
    Raises ValueError for any other extension.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".csv":
        # Use the Python engine for maximum compatibility with messy CSVs.
        return pd.read_csv(file_path, engine="python")
    elif ext in (".xlsx", ".xls"):
        return pd.read_excel(file_path)
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            "Only .csv, .xlsx, and .xls files are accepted."
        )


def _column_meta(df: pd.DataFrame) -> list[dict]:
    """
    Return per-column metadata: name, dtype string, null count, null percentage.

    null_pct is rounded to 4 decimal places to keep the prompt compact.
    """
    n_rows = len(df)
    meta = []
    for col in df.columns:
        null_count = int(df[col].isna().sum())
        null_pct = round(null_count / n_rows, 4) if n_rows > 0 else 0.0
        meta.append({
            "name": str(col),
            "dtype": str(df[col].dtype),
            "null_count": null_count,
            "null_pct": null_pct,
        })
    return meta


def _sample_rows(df: pd.DataFrame, n: int = 10) -> list[dict]:
    """
    Return the first *n* rows as a list of plain Python dicts (orient='records').

    pd.DataFrame.to_dict() keeps numpy types in the values, so we pass the
    result through _to_python() to ensure JSON safety.
    """
    sample_df = df.head(n)
    # Replace NaN before converting so downstream _to_python has less work.
    sample_df = sample_df.where(pd.notna(sample_df), other=None)
    records = sample_df.to_dict(orient="records")
    return [_to_python(row) for row in records]


def _numeric_summary(df: pd.DataFrame) -> dict:
    """
    Run pandas describe() on all numeric columns and return a clean dict.

    Shape of the output:
        {
            "col_name": {
                "count": ..., "mean": ..., "std": ...,
                "min": ..., "25%": ..., "50%": ..., "75%": ..., "max": ...
            },
            ...
        }

    Returns an empty dict when the DataFrame has no numeric columns.
    """
    numeric_cols = df.select_dtypes(include="number")
    if numeric_cols.empty:
        return {}

    # describe() returns a DataFrame indexed by statistic name.
    # .to_dict() gives {col: {stat: value}} which is the shape we want.
    raw = numeric_cols.describe().to_dict()
    return _to_python(raw)


def _categorical_summary(df: pd.DataFrame, max_unique: int = 50, top_n: int = 10) -> dict:
    """
    For each non-numeric column with fewer than *max_unique* distinct values,
    return the top *top_n* value counts as a dict {value: count}.

    Columns with too many unique values (e.g. free-text IDs) are intentionally
    skipped — they would bloat the prompt without adding useful context.

    Returns an empty dict when no qualifying columns exist.
    """
    summary = {}
    non_numeric = df.select_dtypes(exclude="number")

    for col in non_numeric.columns:
        n_unique = df[col].nunique(dropna=False)
        if n_unique >= max_unique:
            # Too many distinct values; skip to keep the prompt lean.
            continue

        # value_counts() returns a Series with string-ish index; convert both
        # keys and values to native Python types.
        counts = df[col].value_counts(dropna=False).head(top_n)
        summary[str(col)] = {
            # Cast the index value to str so dict keys are always strings
            # (JSON requires string keys).
            str(k): _to_python(v)
            for k, v in counts.items()
        }

    return summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_file(file_path: str) -> dict:
    """
    Read *file_path* and return a structured summary suitable for LLM injection.

    Parameters
    ----------
    file_path : str
        Absolute or relative path to the uploaded file.

    Returns
    -------
    dict with the following keys:

        filename         : str   — original filename (basename only)
        shape            : [int, int] — [row_count, col_count]
        columns          : list[dict] — per-column metadata
        sample           : list[dict] — first 10 rows as records
        numeric_summary  : dict  — describe() output for numeric columns
        categorical_summary : dict — top-10 value counts for low-cardinality
                                     non-numeric columns
        duplicate_rows   : int   — count of fully duplicate rows

    Raises
    ------
    ValueError
        If the file extension is not .csv, .xlsx, or .xls.
    FileNotFoundError
        If *file_path* does not exist (raised by pandas internally).
    """
    try:
        df = _load_dataframe(file_path)

        # Count fully duplicate rows before any transformation.
        duplicate_rows = int(df.duplicated().sum())

        return {
            "filename": os.path.basename(file_path),
            "shape": [int(df.shape[0]), int(df.shape[1])],
            "columns": _column_meta(df),
            "sample": _sample_rows(df),
            "numeric_summary": _numeric_summary(df),
            "categorical_summary": _categorical_summary(df),
            "duplicate_rows": duplicate_rows,
        }
    except ValueError:
        # Re-raise our own clean errors (unsupported extension) unchanged.
        raise
    except Exception as exc:
        raise ValueError(
            "Could not read this file. Make sure it's a valid CSV or Excel file."
        ) from exc
