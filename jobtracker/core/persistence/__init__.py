"""SQLite implementation details behind the public Database facade.

Repositories share the facade's one connection. Database.atomic is the public
transaction boundary; repositories never create their own connection.
"""
