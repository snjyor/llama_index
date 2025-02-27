"""Common classes for structured operations."""

import logging
from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional, Sequence, cast

from gpt_index.callbacks.schema import CBEventType
from gpt_index.data_structs.node_v2 import Node
from gpt_index.data_structs.table import StructDatapoint
from gpt_index.indices.response.response_builder import get_response_builder
from gpt_index.indices.service_context import ServiceContext
from gpt_index.langchain_helpers.chain_wrapper import LLMPredictor
from gpt_index.langchain_helpers.sql_wrapper import SQLDatabase
from gpt_index.langchain_helpers.text_splitter import TextSplitter
from gpt_index.prompts.default_prompt_selectors import (
    DEFAULT_REFINE_TABLE_CONTEXT_PROMPT_SEL,
)
from gpt_index.prompts.default_prompts import (
    DEFAULT_TABLE_CONTEXT_PROMPT,
    DEFAULT_TABLE_CONTEXT_QUERY,
)
from gpt_index.prompts.prompts import (
    QuestionAnswerPrompt,
    RefinePrompt,
    RefineTableContextPrompt,
    SchemaExtractPrompt,
    TableContextPrompt,
)
from gpt_index.schema import BaseDocument
from gpt_index.utils import truncate_text

logger = logging.getLogger(__name__)


class SQLDocumentContextBuilder:
    """Builder that builds context for a given set of SQL tables.

    Args:
        sql_database (Optional[SQLDatabase]): SQL database to use,
        llm_predictor (Optional[LLMPredictor]): LLM Predictor to use.
        prompt_helper (Optional[PromptHelper]): Prompt Helper to use.
        text_splitter (Optional[TextSplitter]): Text Splitter to use.
        table_context_prompt (Optional[TableContextPrompt]): A
            Table Context Prompt (see :ref:`Prompt-Templates`).
        refine_table_context_prompt (Optional[RefineTableContextPrompt]):
            A Refine Table Context Prompt (see :ref:`Prompt-Templates`).
        table_context_task (Optional[str]): The query to perform
            on the table context. A default query string is used
            if none is provided by the user.
    """

    def __init__(
        self,
        sql_database: SQLDatabase,
        service_context: Optional[ServiceContext] = None,
        text_splitter: Optional[TextSplitter] = None,
        table_context_prompt: Optional[TableContextPrompt] = None,
        refine_table_context_prompt: Optional[RefineTableContextPrompt] = None,
        table_context_task: Optional[str] = None,
    ) -> None:
        """Initialize params."""
        # TODO: take in an entire index instead of forming a response builder
        if sql_database is None:
            raise ValueError("sql_database must be provided.")
        self._sql_database = sql_database
        self._text_splitter = text_splitter
        self._service_context = service_context or ServiceContext.from_defaults()
        self._table_context_prompt = (
            table_context_prompt or DEFAULT_TABLE_CONTEXT_PROMPT
        )
        self._refine_table_context_prompt = (
            refine_table_context_prompt or DEFAULT_REFINE_TABLE_CONTEXT_PROMPT_SEL
        )
        self._table_context_task = table_context_task or DEFAULT_TABLE_CONTEXT_QUERY

    def build_all_context_from_documents(
        self,
        documents_dict: Dict[str, List[BaseDocument]],
    ) -> Dict[str, str]:
        """Build context for all tables in the database."""
        context_dict = {}
        for table_name in self._sql_database.get_table_names():
            context_dict[table_name] = self.build_table_context_from_documents(
                documents_dict[table_name], table_name
            )
        return context_dict

    def build_table_context_from_documents(
        self,
        documents: Sequence[BaseDocument],
        table_name: str,
    ) -> str:
        """Build context from documents for a single table."""
        schema = self._sql_database.get_single_table_info(table_name)
        prompt_with_schema = QuestionAnswerPrompt.from_prompt(
            self._table_context_prompt.partial_format(schema=schema)
        )
        refine_prompt_with_schema = RefinePrompt.from_prompt(
            self._refine_table_context_prompt.partial_format(schema=schema)
        )
        text_splitter = (
            self._text_splitter
            or self._service_context.prompt_helper.get_text_splitter_given_prompt(
                prompt_with_schema, 1
            )
        )
        # we use the ResponseBuilder to iteratively go through all texts
        response_builder = get_response_builder(
            self._service_context,
            prompt_with_schema,
            refine_prompt_with_schema,
        )
        event_id = self._service_context.callback_manager.on_event_start(
            CBEventType.CHUNKING, payload={"documents": documents}
        )
        text_chunks = []
        for doc in documents:
            chunks = text_splitter.split_text(doc.get_text())
            text_chunks.extend(chunks)
        self._service_context.callback_manager.on_event_end(
            CBEventType.CHUNKING, payload={"chunks": text_chunks}, event_id=event_id
        )

        # feed in the "query_str" or the task
        table_context = response_builder.get_response(
            text_chunks=text_chunks, query_str=self._table_context_task
        )
        return cast(str, table_context)


OUTPUT_PARSER_TYPE = Callable[[str], Optional[Dict[str, Any]]]


class BaseStructDatapointExtractor:
    """Extracts datapoints from a structured document."""

    def __init__(
        self,
        llm_predictor: LLMPredictor,
        schema_extract_prompt: SchemaExtractPrompt,
        output_parser: OUTPUT_PARSER_TYPE,
    ) -> None:
        """Initialize params."""
        self._llm_predictor = llm_predictor
        self._schema_extract_prompt = schema_extract_prompt
        self._output_parser = output_parser

    def _clean_and_validate_fields(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Validate fields with col_types_map."""
        new_fields = {}
        col_types_map = self._get_col_types_map()
        for field, value in fields.items():
            clean_value = value
            if field not in col_types_map:
                continue
            # if expected type is int or float, try to convert value to int or float
            expected_type = col_types_map[field]
            if expected_type == int:
                try:
                    clean_value = int(value)
                except ValueError:
                    continue
            elif expected_type == float:
                try:
                    clean_value = float(value)
                except ValueError:
                    continue
            else:
                if len(value) == 0:
                    continue
                if not isinstance(value, col_types_map[field]):
                    continue
            new_fields[field] = clean_value
        return new_fields

    @abstractmethod
    def _insert_datapoint(self, datapoint: StructDatapoint) -> None:
        """Insert datapoint into index."""

    @abstractmethod
    def _get_col_types_map(self) -> Dict[str, type]:
        """Get col types map for schema."""

    @abstractmethod
    def _get_schema_text(self) -> str:
        """Get schema text for extracting relevant info from unstructured text."""

    def insert_datapoint_from_nodes(self, nodes: Sequence[Node]) -> None:
        """Extract datapoint from a document and insert it."""
        text_chunks = [node.get_text() for node in nodes]
        fields = {}
        for i, text_chunk in enumerate(text_chunks):
            fmt_text_chunk = truncate_text(text_chunk, 50)
            logger.info(f"> Adding chunk {i}: {fmt_text_chunk}")
            # if embedding specified in document, pass it to the Node
            schema_text = self._get_schema_text()
            response_str, _ = self._llm_predictor.predict(
                self._schema_extract_prompt,
                text=text_chunk,
                schema=schema_text,
            )
            cur_fields = self._output_parser(response_str)
            if cur_fields is None:
                continue
            # validate fields with col_types_map
            new_cur_fields = self._clean_and_validate_fields(cur_fields)
            fields.update(new_cur_fields)
        struct_datapoint = StructDatapoint(fields)
        if struct_datapoint is not None:
            self._insert_datapoint(struct_datapoint)
            logger.debug(f"> Added datapoint: {fields}")
