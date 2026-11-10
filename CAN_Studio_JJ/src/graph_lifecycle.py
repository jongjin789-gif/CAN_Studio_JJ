"""Close the graph windows belonging to one monitor or log viewer."""


def close_graph_windows(owner):
    combined = owner.combined_view_window
    if combined is not None:
        combined.close_all_graphs()
        combined.deleteLater()
    # Closing the combined view already removes its graphs from this list.
    for graph in list(owner.active_graphs):
        graph.close()
        graph.deleteLater()
    owner.active_graphs.clear()
    owner.synced_graphs_ordered.clear()
    owner.combined_view_window = None
