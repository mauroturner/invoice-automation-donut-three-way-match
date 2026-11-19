"""The tables of ``docs/data-dictionary.md``, written from the semantic model (RF26, AC39; plan, section 25). Adapted from project 06's ``docs_sync.py``.

The model (``dashboard.semantic_model.MODEL``) is the specification the export checks every Parquet file against, so the dictionary takes its tables from it instead of from a hand
copy: one section per table, in the order of the model, with every column, its type, its unit and its meaning. ``render_dictionary_tables`` returns the text that sits between the
``columns`` markers of the dictionary, and ``replace_block`` (``reporting.replace_block``) puts it there. ``tests/docs/test_docs.py`` checks that the dictionary holds exactly this
text and that every Parquet file of ``outputs/powerbi/`` has the columns it lists. The ``docs-sync`` command of the command line copies only the results block of the README; it
does not call this module yet.
"""

from __future__ import annotations

from invoiceflow.dashboard.semantic_model import SemanticModel

COLUMNS_START = "<!-- columns:start -->"
COLUMNS_END = "<!-- columns:end -->"
KIND_ES = {"dimension": "Dimensión", "fact": "Hecho"}
KEY_ES = {"dimension": "Clave", "fact": "Grano"}
DTYPE_ES = {"int8": "entero 8", "int16": "entero 16", "int32": "entero 32", "int64": "entero 64", "float64": "decimal", "string": "texto", "bool": "verdadero/falso", "date": "fecha",
            "datetime": "fecha y hora"}
NO_UNIT = "—"
VERSIONED_ES = "Se versiona en `outputs/powerbi/{name}.parquet`."


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _dtype_es(table: str, column: str, dtype: str) -> str:
    """The name the dictionary gives a type; one ``DTYPE_ES`` does not name is a ``ValueError`` that names the column."""
    try:
        return DTYPE_ES[dtype]
    except KeyError:
        raise ValueError(f"el tipo {dtype!r} de la columna {table}.{column} no tiene nombre en el diccionario (docs_sync.DTYPE_ES)") from None


def render_dictionary_tables(model: SemanticModel) -> str:
    """One section per table of the model, in its order: what it is, where it is written, its key or grain, and every column with its type, unit and meaning."""
    lines: list[str] = []
    for table in model.tables:
        keys = ", ".join(f"`{k}`" for k in table.key)
        lines += [f"### `{table.name}`", "", f"{KIND_ES[table.kind]}. {_cell(table.description_es)} {VERSIONED_ES.format(name=table.name)} {KEY_ES[table.kind]}: {keys}.", ""]
        lines += ["| Columna | Tipo | Unidad | Significado |", "| --- | --- | --- | --- |"]
        lines += [f"| `{c.name}` | {_dtype_es(table.name, c.name, c.dtype)} | {_cell(c.unit or NO_UNIT)} | {_cell(c.description_es)} |" for c in table.columns]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


__all__ = ["COLUMNS_END", "COLUMNS_START", "DTYPE_ES", "render_dictionary_tables"]
