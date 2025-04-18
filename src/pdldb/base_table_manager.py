# ruff:noqa:D102,D101,D107 docstrings
# ruff:noqa:ANN204 Annotation
# ruff:noqa:C901 complexity

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

import polars as pl
from deltalake import DeltaTable, WriterProperties
from deltalake.exceptions import TableNotFoundError

from pdldb.base_table_validator import BaseTable

if TYPE_CHECKING:
    from deltalake.table import TableMerger


class BaseTableManager(ABC):
    def __init__(self, delta_table_path: str, storage_options: Optional[dict[str, str]] = None):
        self.storage_options = storage_options
        self.base_path = Path(delta_table_path)
        self.tables: dict[str, BaseTable] = {}
        self._load_existing_tables()

    def create_table(
        self,
        table_name: str,
        table_schema: dict[str, Any],
        primary_keys: Union[str, list[str]],
    ) -> None:
        if isinstance(primary_keys, list):
            primary_keys = ",".join(primary_keys)

        base_table = BaseTable(name=table_name, table_schema=table_schema, primary_keys=primary_keys)
        self.tables[table_name] = base_table

    def append(
        self,
        table_name: str,
        df: pl.DataFrame,
        delta_write_options: Optional[dict[str, Any]],
    ) -> None:
        base_table = self.tables[table_name]
        if not base_table.validate_schema(df):
            msg = "DataFrame does not match table schema"
            raise ValueError(msg)
        delta_write_options = delta_write_options or {}
        delta_write_options["description"] = base_table.primary_keys

        df.write_delta(
            str(self.base_path / table_name),
            mode="append",
            delta_write_options=delta_write_options,
            storage_options=self.storage_options,
        )

    def merge(
        self,
        table_name: str,
        df: pl.DataFrame,
        merge_condition: str,
        delta_write_options: Optional[dict[str, Any]],
    ) -> None:
        base_table = self.tables[table_name]
        if not base_table.validate_schema(df):
            msg = "DataFrame does not match table schema"
            raise ValueError(msg)

        primary_keys = base_table.primary_keys
        delta_write_options = delta_write_options or {}
        delta_write_options["description"] = primary_keys
        mode = "merge"

        if "," in primary_keys:
            pk_columns = [col.strip() for col in primary_keys.split(",")]
            predicate_conditions = [f"s.{col} = t.{col}" for col in pk_columns]
            predicate = " AND ".join(predicate_conditions)
        else:
            predicate = f"s.{primary_keys} = t.{primary_keys}"

        delta_merge_options = {
            "predicate": predicate,
            "source_alias": "s",
            "target_alias": "t",
        }

        try:
            merger: TableMerger = df.write_delta(
                str(self.base_path / table_name),
                mode=mode,
                storage_options=self.storage_options,
                delta_merge_options=delta_merge_options,
            )

            if merge_condition == "update":
                merger.when_matched_update_all().execute()

            if merge_condition == "insert":
                merger.when_not_matched_insert_all().execute()

            if merge_condition == "delete":
                merger.when_matched_delete().execute()

            if merge_condition == "upsert":
                merger.when_matched_update_all().when_not_matched_insert_all().execute()

            if merge_condition == "upsert_delete":
                merger.when_matched_update_all().when_not_matched_insert_all().when_not_matched_by_source_delete().execute()

        except TableNotFoundError as e:
            if merge_condition in ["insert", "upsert"]:
                df.write_delta(
                    str(self.base_path / table_name),
                    mode="append",
                    delta_write_options=delta_write_options,
                    storage_options=self.storage_options,
                )
            else:
                msg = (
                    f"No log files found or data found."
                    f"Please check if table data exists or if the merge condition ({merge_condition}) is correct."
                )
                raise ValueError(msg) from e
        except Exception as e:
            msg = f"An error occurred during the merge operation: {e}"
            raise RuntimeError(msg) from e

    def overwrite(
        self,
        table_name: str,
        df: pl.DataFrame,
        delta_write_options: Optional[dict[str, Any]],
    ) -> None:
        base_table = self.tables[table_name]
        if not base_table.validate_schema(df):
            msg = "DataFrame does not match table schema"
            raise ValueError(msg)

        delta_write_options = delta_write_options or {}
        delta_write_options["description"] = base_table.primary_keys

        df.write_delta(
            str(self.base_path / table_name),
            mode="overwrite",
            delta_write_options=delta_write_options,
            storage_options=self.storage_options,
        )

    def get_data_frame(self, table_name: str) -> pl.DataFrame:
        table_path = self.base_path / table_name
        try:
            return pl.read_delta(str(table_path), storage_options=self.storage_options)
        except TableNotFoundError as e:
            if table_name in self.tables:
                schema = self.tables[table_name].table_schema
                return pl.DataFrame(schema=schema)
            msg = f"Table '{table_name}' does not exist or has no data: {e}"
            raise ValueError(msg) from e

    def get_lazy_frame(self, table_name: str) -> pl.LazyFrame:
        table_path = self.base_path / table_name
        try:
            return pl.scan_delta(str(table_path), storage_options=self.storage_options)
        except TableNotFoundError as e:
            if table_name in self.tables:
                schema = self.tables[table_name].table_schema
                return pl.DataFrame(schema=schema).lazy()
            msg = f"Table '{table_name}' does not exist or has no data: {e}"
            raise ValueError(msg) from e

    def optimize_table(
        self,
        table_name: str,
        target_size: Optional[int],
        max_concurrent_tasks: Optional[int],
        writer_properties: Optional[WriterProperties],
    ) -> dict[str, Any]:
        delta_table = DeltaTable(str(self.base_path / table_name), storage_options=self.storage_options)

        return delta_table.optimize.compact(
            target_size=target_size,
            max_concurrent_tasks=max_concurrent_tasks,
            writer_properties=writer_properties,
        )

    def vacuum_table(
        self,
        table_name: str,
        retention_hours: Optional[int],
        *,
        enforce_retention_duration: bool = True,
    ) -> list[str]:
        delta_table = DeltaTable(str(self.base_path / table_name), storage_options=self.storage_options)
        return delta_table.vacuum(
            retention_hours=retention_hours,
            dry_run=False,
            enforce_retention_duration=enforce_retention_duration,
        )

    def list_tables(self) -> dict[str, dict[str, Any]]:
        return {name: self.get_table_info(name) for name in self.tables}

    def get_table_info(self, table_name: str) -> dict:
        table_path = self.base_path / table_name
        dt = DeltaTable(str(table_path), storage_options=self.storage_options)
        base_table = self.tables[table_name]
        return {
            "exists": True,
            "version": dt.version(),
            "metadata": dt.metadata(),
            "size": len(list(table_path.glob("*.parquet"))),
            "schema": base_table.table_schema,
            "primary_keys": base_table.primary_keys,
        }

    def get_table_schema(self, table_name: str) -> dict[str, Any]:
        return self.tables[table_name].table_schema

    @abstractmethod
    def _load_existing_tables(self) -> None:
        pass

    @abstractmethod
    def delete_table(self, table_name: str) -> bool:
        pass
