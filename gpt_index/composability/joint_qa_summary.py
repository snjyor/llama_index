"""Joint QA Summary graph."""


from typing import Sequence, Optional
from gpt_index.storage.docstore.registry import get_default_docstore

from gpt_index.callbacks.schema import CBEventType
from gpt_index.indices.service_context import ServiceContext
from gpt_index.composability import ComposableGraph
from gpt_index.indices.list.base import GPTListIndex
from gpt_index.indices.tree.base import GPTTreeIndex
from gpt_index.indices.vector_store import GPTVectorStoreIndex
from gpt_index.readers.schema.base import Document
from gpt_index.storage.docstore import BaseDocumentStore

DEFAULT_SUMMARY_TEXT = "Use this index for summarization queries"
DEFAULT_QA_TEXT = (
    "Use this index for queries that require retrieval of specific "
    "context from documents."
)


class QASummaryGraphBuilder:
    """Joint QA Summary graph builder.

    Can build a graph that provides a unified query interface
    for both QA and summarization tasks.

    NOTE: this is a beta feature. The API may change in the future.

    Args:
        docstore (BaseDocumentStore): A BaseDocumentStore to use for storing nodes.
        service_context (ServiceContext): A ServiceContext to use for
            building indices.
        summary_text (str): Text to use for the summary index.
        qa_text (str): Text to use for the QA index.
        node_parser (NodeParser): A NodeParser to use for parsing.

    """

    def __init__(
        self,
        docstore: Optional[BaseDocumentStore] = None,
        service_context: Optional[ServiceContext] = None,
        summary_text: str = DEFAULT_SUMMARY_TEXT,
        qa_text: str = DEFAULT_QA_TEXT,
    ) -> None:
        """Init params."""
        self._docstore = docstore or get_default_docstore()
        self._service_context = service_context or ServiceContext.from_defaults()
        self._summary_text = summary_text
        self._qa_text = qa_text

    def build_graph_from_documents(
        self,
        documents: Sequence[Document],
    ) -> "ComposableGraph":
        """Build graph from index."""
        event_id = self._service_context.callback_manager.on_event_start(
            CBEventType.CHUNKING, payload={"documents": documents}
        )
        nodes = self._service_context.node_parser.get_nodes_from_documents(documents)
        self._service_context.callback_manager.on_event_end(
            CBEventType.CHUNKING, payload={"nodes": nodes}, event_id=event_id
        )
        self._docstore.add_documents(nodes, allow_update=True)

        # used for QA
        vector_index = GPTVectorStoreIndex(
            nodes,
            service_context=self._service_context,
        )
        # used for summarization
        list_index = GPTListIndex(nodes, service_context=self._service_context)

        vector_index.index_struct.summary = self._qa_text
        list_index.index_struct.summary = self._summary_text

        graph = ComposableGraph.from_indices(
            GPTTreeIndex,
            [vector_index, list_index],
            service_context=self._service_context,
        )

        return graph
