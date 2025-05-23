# ruff:noqa:D102,D101,D107,D105 docstrings
# ruff:noqa:ANN204 Annotation
# ruff:noqa:C901 complexity

import os

from deltalake import WriterProperties

os.environ["RUST_LOG"] = "error"

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, field_validator

from pdldb.local_table_manager import LocalTableManager

if TYPE_CHECKING:
    from .base_table_manager import BaseTableManager


class LakeManagerInitModel(BaseModel):
    base_path: str = Field(..., description="Base path for the lake storage")
    storage_options: Optional[dict[str, Any]] = Field(None, description="Storage options for the lake")

    model_config = ConfigDict(extra="forbid")

    @field_validator("base_path")
    @classmethod
    def validate_base_path(cls, v: str) -> str:
        if not v or not v.strip():
            msg = "Base path cannot be empty"
            raise ValueError(msg)
        return v


class TableCreateModel(BaseModel):
    table_name: str = Field(..., description="Name of the table to create")
    table_schema: dict[str, Any] = Field(..., description="Schema definition for the table")
    primary_keys: Union[str, list[str]] = Field(..., description="Primary key column(s)")

    model_config = ConfigDict(extra="forbid")

    @field_validator("table_name")
    @classmethod
    def validate_table_name(cls, v: str) -> str:
        if not v or not v.strip():
            msg = "Table name cannot be empty"
            raise ValueError(msg)
        return v


class TableOperationModel(BaseModel):
    table_name: str = Field(..., description="Name of the table")
    df: pl.DataFrame = Field(..., description="Data to write")
    delta_write_options: Optional[dict[str, Any]] = Field(None, description="Options for delta write operation")

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    @field_validator("table_name")
    @classmethod
    def validate_table_name(cls, v: str) -> str:
        if not v or not v.strip():
            msg = "Table name cannot be empty"
            raise ValueError(msg)
        return v


class MergeOperationModel(TableOperationModel):
    merge_condition: Literal["update", "insert", "delete", "upsert", "upsert_delete"] = Field(
        "insert", description="Type of merge operation to perform"
    )


class OptimizeTableModel(BaseModel):
    table_name: str = Field(..., description="Name of the table")
    target_size: int = Field(512 * 1024 * 1024, description="Target file size in bytes")
    max_concurrent_tasks: Optional[int] = Field(None, description="Maximum number of concurrent tasks")
    writer_properties: Optional[WriterProperties] = Field(None, description="Writer properties")

    model_config = ConfigDict(extra="forbid")


class VacuumTableModel(BaseModel):
    table_name: str = Field(..., description="Name of the table")
    retention_hours: Optional[int] = Field(0, description="Retention hours for files")
    enforce_retention_duration: Optional[bool] = Field(False, description="Whether to enforce retention duration")

    model_config = ConfigDict(extra="forbid")


class TableNameModel(BaseModel):
    table_name: str = Field(..., description="Name of the table to operate on")

    model_config = ConfigDict(extra="forbid")

    @field_validator("table_name")
    @classmethod
    def validate_table_name(cls, v: str) -> str:
        if not v or not v.strip():
            msg = "Table name cannot be empty"
            raise ValueError(msg)
        return v


class LakeManager:
    """Base class for managing a data lake with tables stored in Delta format.

    This class provides the foundation for creating, reading, updating, and managing
    Delta tables in a data lake. It's designed to be extended by specific implementations
    like LocalLakeManager.
    """

    def __init__(self, base_path: str, storage_options: Optional[dict[str, Any]] = None):
        """Initialize a new LakeManager.

        Args:
            base_path: The base path where the data lake will be stored
            storage_options: Optional cloud storage-specific parameters
        """
        params = LakeManagerInitModel(base_path=base_path, storage_options=storage_options)
        self.base_path = Path(params.base_path)
        self.storage_options = params.storage_options
        self.table_manager: BaseTableManager

    def _check_table_exists(self, table_name: str) -> None:
        if table_name not in self.table_manager.tables:
            msg = f"Table {table_name} does not exist"
            raise ValueError(msg)

    def _check_table_not_exists(self, table_name: str) -> None:
        if table_name in self.table_manager.tables:
            msg = f"Table {table_name} already exists"
            raise ValueError(msg)

    def create_table(
        self,
        table_name: str,
        table_schema: dict[str, Any],
        primary_keys: Union[str, list[str]],
    ) -> None:
        """Create a new table in the data lake.

        Args:
            table_name: Name of the table to create
            table_schema: Schema definition for the new table
            primary_keys: Primary key column(s) for the table

        Notes:
            - The schema is enforced for write operations.
            - The primary keys are used to identify unique records for merge operations.
            - The primary key can be a single column or a composite key (multiple columns).
            - Primary keys can be specified as a string or a list of strings.

        Example: Single primary key
            ```python
            from pdldb import LocalLakeManager
            import polars as pl

            lake_manager = LocalLakeManager("data")
            schema = {
                "sequence": pl.Int32,
                "value_1": pl.Float64,
                "value_2": pl.Utf8,
                "value_3": pl.Float64,
                "value_4": pl.Float64,
                "value_5": pl.Datetime("ns"),
            }
            primary_keys = "sequence"
            lake_manager.create_table("my_table", schema, primary_keys)
            ```

        Example: Composite primary key
            ```python
            primary_keys = ["sequence", "value_1"]
            lake_manager.create_table("my_table", schema, primary_keys)
            ```
        """
        params = TableCreateModel(table_name=table_name, table_schema=table_schema, primary_keys=primary_keys)

        self._check_table_not_exists(table_name=params.table_name)
        self.table_manager.create_table(
            table_name=params.table_name,
            table_schema=params.table_schema,
            primary_keys=params.primary_keys,
        )

    def append_table(
        self,
        table_name: str,
        df: pl.DataFrame,
        delta_write_options: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append data to an existing table.

        Args:
            table_name: Name of the table to append to
            df: DataFrame containing the data to append
            delta_write_options: Optional configuration for the delta write operation

        Notes:
            - The schema of the DataFrame must match the schema of the table
            - Appending data to a table has been intialized but contains no data will create the
                table on your storage backend.

        Example:
            ```python
            lake_manager.append_table("my_table", newdata)
            ```
        """
        params = TableOperationModel(table_name=table_name, df=df, delta_write_options=delta_write_options)

        self._check_table_exists(table_name=params.table_name)
        self.table_manager.append(
            table_name=params.table_name,
            df=params.df,
            delta_write_options=params.delta_write_options,
        )

    def merge_table(
        self,
        table_name: str,
        df: pl.DataFrame,
        merge_condition: Literal["update", "insert", "delete", "upsert", "upsert_delete"] = "insert",
        delta_write_options: Optional[dict[str, Any]] = None,
    ) -> None:
        """Merge data into an existing table based on the specified merge condition.

        Args:
            table_name: Name of the table to merge data into
            df: DataFrame containing the data to merge
            merge_condition: Type of merge operation to perform (update, insert, delete, upsert, upsert_delete)
            delta_write_options: Optional configuration for the delta write operation

        merge_condition:
            - update: Update existing rows only from the new data
            - insert: Insert new rows only from the new data
            - delete: Delete existing rows that exist in the new data
            - upsert: Update existing and insert new rows from the new data
            - upsert_delete: Update existing, insert new rows, and delete rows that don't exist in the new data

        Notes:
            - If the table has been intialized but contains no data, merge operations requiring
                existing data ('update', 'delete', 'upsert_delete') will fail with an error message.
            - The 'insert' and upsert' operations will create the table on your storage backend if the
                table has been intialized but contains no data.
            - Primary keys defined for the table are used to determine matching records.

        Example:
            ```python
            lake_manager.merge_table("my_table", new_data, merge_condition="upsert")
            ```
        """
        params = MergeOperationModel(
            table_name=table_name,
            df=df,
            merge_condition=merge_condition,
            delta_write_options=delta_write_options,
        )

        self._check_table_exists(table_name=params.table_name)

        self.table_manager.merge(
            table_name=params.table_name,
            df=params.df,
            delta_write_options=params.delta_write_options,
            merge_condition=params.merge_condition,
        )

    def overwrite_table(
        self,
        table_name: str,
        df: pl.DataFrame,
        delta_write_options: Optional[dict[str, Any]] = None,
    ) -> None:
        """Overwrite an existing table with new data.

        Args:
            table_name: Name of the table to overwrite
            df: DataFrame containing the new data
            delta_write_options: Optional configuration for the delta write operation

        Notes:
            - The schema of the DataFrame must match the schema of the table
            - Overwriting a table that has been intialized but contains no data will create the
                table on your storage backend.
            - Overwriting a table with existing data will replace the entire table.

        Example:
            ```python
            lake_manager.overwrite_table("my_table", new_data)
            ```
        """
        params = TableOperationModel(table_name=table_name, df=df, delta_write_options=delta_write_options)

        self._check_table_exists(table_name=params.table_name)
        self.table_manager.overwrite(
            table_name=params.table_name,
            df=params.df,
            delta_write_options=params.delta_write_options,
        )

    def get_data_frame(self, table_name: str) -> pl.DataFrame:
        """Get an eager DataFrame from a table.

        Args:
            table_name: Name of the table to read

        Returns:
            A Polars DataFrame containing the table data

        Notes:
            - All table data is loaded into a Polars DataFrame in memory.
            - This is suitable for small to medium-sized tables.

        Example:
            ```python
            df = lake_manager.get_data_frame("my_table")
            ```
        """
        params = TableNameModel(table_name=table_name)
        self._check_table_exists(table_name=params.table_name)
        return self.table_manager.get_data_frame(table_name=params.table_name)

    def get_lazy_frame(self, table_name: str) -> pl.LazyFrame:
        """Get a lazy DataFrame from a table for deferred execution.

        Args:
            table_name: Name of the table to read

        Returns:
            A Polars LazyFrame referencing the table data

        Notes:
            - LazyFrames allow for deferred execution and optimization of query plans.
            - Table data is not loaded into memory until an action (like collect) is called.
            - This is suitable for large tables or complex queries.

        Example:
            ```python
            lazy_frame = lake_manager.get_lazy_frame("my_table")
            result = lazy_frame.filter(col("column") > 10).select(["column"])
            result.collect()
            ```
        """
        params = TableNameModel(table_name=table_name)
        self._check_table_exists(table_name=params.table_name)
        return self.table_manager.get_lazy_frame(table_name=params.table_name)

    def optimize_table(
        self,
        table_name: str,
        target_size: int = 512 * 1024 * 1024,
        max_concurrent_tasks: Optional[int] = None,
        writer_properties: Optional[WriterProperties] = None,
    ) -> None:
        """Optimize a table by compacting small files in to files of the target size.

        Optimizing a table can improve query performance and cloud costs.

        Args:
            table_name: Name of the table to optimize
            target_size: Target file size in bytes for optimization
            max_concurrent_tasks: Maximum number of concurrent tasks for optimization
            writer_properties: Optional writer properties for optimization

        Notes:
            - The target size is the desired size of the output files after optimization.
            - The default target size is 512 MB (512 * 1024 * 1024 bytes).
            - The optimization process may take some time depending on the size of the table
                and the number of files.

        Example:
            ```python
            lake_manager.optimize_table("my_table", target_size=512*1024*1024)
            ```
        """
        params = OptimizeTableModel(
            table_name=table_name,
            target_size=target_size,
            max_concurrent_tasks=max_concurrent_tasks,
            writer_properties=writer_properties,
        )

        self._check_table_exists(table_name=params.table_name)
        self.table_manager.optimize_table(
            table_name=params.table_name,
            target_size=params.target_size,
            max_concurrent_tasks=params.max_concurrent_tasks,
            writer_properties=params.writer_properties,
        )

    def vacuum_table(
        self,
        table_name: str,
        retention_hours: Optional[int] = 168,
        enforce_retention_duration: bool = False,
    ) -> None:
        """Clean up old data files from a table based on the retention period.

        Old data files are those that are no longer referenced by the table.

        Args:
            table_name: Name of the table to vacuum
            retention_hours: Retention period in hours (0 means delete all unreferenced files)
            enforce_retention_duration: Whether to enforce the retention period

        Notes:
            - The retention period is the time duration for which files are retained.
            - Files older than the retention period will be deleted.
            - Setting retention_hours to 0 will delete all unreferenced files, regardless of age.
            - The enforce_retention_duration flag ensures that the retention period is strictly enforced.
            - Use caution when setting retention_hours to 0, as this will delete all unreferenced files.
            - This operation is irreversible, deleted files cannot be recovered.
            - The vacuum operation may take some time depending on the size of the table and the number of files.

        Example:
            ```python
            lake_manager.vacuum_table("my_table", retention_hours=24)
            ```
        """
        params = VacuumTableModel(
            table_name=table_name,
            retention_hours=retention_hours,
            enforce_retention_duration=enforce_retention_duration,
        )

        self._check_table_exists(table_name=params.table_name)
        self.table_manager.vacuum_table(
            table_name=params.table_name,
            retention_hours=params.retention_hours,
            enforce_retention_duration=params.enforce_retention_duration,
        )

    def list_tables(self) -> dict[str, dict[str, Any]]:
        """List all tables in the data lake.

        Returns:
            A dictionary mapping table names to their metadata

        Example:
            ```python
            lake_manager.list_tables()
            ```
        """
        return self.table_manager.list_tables()

    def get_table_info(self, table_name: str) -> dict[str, Any]:
        """Get detailed information about a specific table.

        Args:
            table_name: Name of the table to get information for

        Returns:
            A dictionary containing detailed table information

        Example:
            ```python
            lake_manager.get_table_info("my_table")
            ```
        """
        params = TableNameModel(table_name=table_name)
        self._check_table_exists(table_name=params.table_name)
        return self.table_manager.get_table_info(table_name=params.table_name)

    def get_table_schema(self, table_name: str) -> dict[str, Any]:
        """Get the schema definition for a specific table.

        Args:
            table_name: Name of the table to get the schema for

        Returns:
            A dictionary representing the table schema

        Example:
            ```python
            lake_manager.get_table_schema("my_table")
            ```
        """
        params = TableNameModel(table_name=table_name)
        self._check_table_exists(table_name=params.table_name)
        return self.table_manager.get_table_schema(table_name=params.table_name)

    def delete_table(self, table_name: str) -> bool:
        """Delete a table from the data lake.

        Deleted data files are not recoverable, so use with caution.

        Args:
            table_name: Name of the table to delete

        Returns:
            True if the table was successfully deleted

        Notes:
            - This operation is irreversible, and deleted tables cannot be recovered.
            - Use caution when deleting tables, especially in production environments.
            - Ensure that you have backups or copies of important data before deletion.
            - Deleting a table will remove all associated data files and metadata.

        Example:
            ```python
            lake_manager.delete_table("my_table")
            ```
        """
        params = TableNameModel(table_name=table_name)
        self._check_table_exists(table_name=params.table_name)
        return self.table_manager.delete_table(table_name=params.table_name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: ANN001
        pass


class LocalLakeManager(LakeManager):
    """Implementation of LakeManager for local filesystem storage.

    This class extends the base LakeManager to provide specific functionality
    for managing Delta tables in a local filesystem.
    """

    def __init__(self, base_path: str):
        """Initialize a new LocalLakeManager.

        Args:
            base_path: The local filesystem path where the data lake will be stored

        Example:
            ```python
            from pdldb.lake_manager import LocalLakeManager
            lake_manager = LocalLakeManager("data")
            ```
        """
        params = LakeManagerInitModel(base_path=base_path, storage_options=None)
        super().__init__(params.base_path, params.storage_options)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.table_manager = LocalTableManager(str(self.base_path), self.storage_options)
