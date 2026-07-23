"""
unknown_query_logger.py
Logs queries the chatbot couldn't match to an FAQ, to a local flat file,
for manual review when updating the FAQ knowledge base.

Note: chat_engine.py's _fallback() + app.py already call
notifications.notify("unanswered_question", ...) for every unmatched
query, which stores it in SQLite and surfaces it via the admin
dashboard's notification bell - that's the primary, already-wired-in
path, and it doesn't have the downsides below.

This flat-file version is a lighter-weight alternative. If you use it,
keep in mind:
  - unmatched_queries.txt should be added to .gitignore (it isn't yet) -
    otherwise raw customer queries end up committed to git history.
  - Writing to a plain .txt file has no locking; under concurrent
    requests (e.g. behind waitress/gunicorn with multiple workers) writes
    can interleave. Fine for low traffic, not ideal at scale.
  - Unlike the dashboard notification, entries here aren't visible
    anywhere in the admin UI - someone has to open the file directly.
"""


def handle_unknown_query(user_query):
    # Log unmatched query to a file for future FAQ updates
    with open("unmatched_queries.txt", "a") as f:
        f.write(f"{user_query}\n")

    return (
        "I'm not sure about that specific detail yet, but I can get one of our "
        "specialists to answer it directly! Would you like to leave your email or phone number?"
    )
